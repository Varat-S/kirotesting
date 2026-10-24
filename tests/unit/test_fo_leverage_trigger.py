"""FO Trigger Agent, Debt/EBITDAR T1: when it fires (|net one-offs as tagged| above 5% of EBITDAR, or EBITDAR ≤ 0; agent
triggers enabled), the passages it reads (MD&A and the notes), the verified evidence, the outcome (first match: one-offs
quantified and not recurring, EBITDAR normalised; operational with no one-offs, no adjustment; a one-off identified but
not quantified, the 3-year average EBITDAR; partly quantified with an unexplained remainder, or no explanation:
unresolved), the rescoring of Debt/EBITDAR and its escalation.

Synthetic 10-K text and a scored Debt/EBITDAR measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.leverage_triggers import apply_leverage_t1, leverage_t1_passages, leverage_t1_trigger
from app.services.fo.measures import one_offs_for
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import leverage_t1_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_leverage

FY25, FY24, FY23 = "2025-01-01--2025-12-31", "2024-01-01--2024-12-31", "2023-01-01--2023-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
IMPAIRMENT = "In 2025, we recorded a goodwill impairment charge of $120 million in our Retail segment."
GAIN = "We recognized a gain on the sale of our Denver facility of $20 million in 2025."
LITIGATION = "In 2025, we recorded a charge for the settlement of litigation with a former supplier."
OPERATIONS = "Operating income decreased in 2025 because of lower patient volumes and higher labor costs."
RECURS = "We record restructuring charges each year as we consolidate facilities."
RESTRUCTURING = "Restructuring charges were $15 million in 2025."
TAX = "Our effective tax rate was 21% in 2025."
SETTINGS = {"values": {"p": 5.0, "m": 2.0, "delta": 2.5, "cap": 3.0},
            "benchmarks": {"debt_to_ebitdar": 3.0, "net_debt_to_ebitdar": 2.5}, "sector": "Hospitals/Healthcare Facilities"}


def _data(small_business=False):
    mdna = ["Results of Operations", IMPAIRMENT, GAIN, LITIGATION, OPERATIONS, RECURS, RESTRUCTURING]
    notes = ["NOTE 10 — INCOME TAXES", TAX]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [{"concept": "dei:EntitySmallBusiness", "value": small_business, "type": "text",
                       "dimensions": {}}], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _col(heading, period, ebitdar, net=0.0):
    """Debt + op. leases 2,000, cash 200: net debt + op. leases 1,800."""
    col = {"heading": heading, "period": period, "basis": "historical", "ebitdar": ebitdar, "debt": 1800e6,
           "adj_debt": 2000e6, "cash": 200e6, "adj_net_debt": 1800e6, "one_off_charges": max(net, 0.0) + 20e6,
           "one_off_gains": 20e6 - min(net, 0.0), "net_one_offs": net, "net_one_offs_concept": "tagged one-offs"}
    for key, num in (("debt_to_ebitdar", 2000e6), ("net_debt_to_ebitdar", 1800e6)):
        col[key] = {"ratio": num / ebitdar if ebitdar > 0 else None, "negative_ebitda": ebitdar <= 0, "reason": None}
    score_leverage(col, SETTINGS)
    return col


def _leverage(ebitdar=400e6, net=100e6, years=3):
    """A scored Debt/EBITDAR: FY2025 EBITDAR 400 (net one-offs 100: 25%), FY2024 500, FY2023 600."""
    cols = [_col("FY2025", FY25, ebitdar, net), _col("FY2024", FY24, 500e6), _col("FY2023", FY23, 600e6)][:years]
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "leverage", "title": "Debt to EBITDAR", "short": "Debt / EBITDAR", "rows": [], "columns": cols,
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "D": 5.0, "D_dso": 3.0}}]


def _score_at(ebitdar):
    return _col("FY2025", FY25, ebitdar)["composite"]["score"]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, one_offs=(), unquantified=(), operational=()):
    return {"leverage_t1": {"status": status, "one_offs": list(one_offs), "unquantified": list(unquantified),
                            "operational": list(operational), "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None, data=None):
    return leverage_t1_trigger(data or _data(), metrics or _leverage(), ENABLED, session=db_session,
                               backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


CHARGE = {"item_id": "O1", "kind": "charge", "category": "impairment", "description": "Goodwill impairment",
          "evidence": _quote("L01", IMPAIRMENT, amount="$120 million"), "recurring": False, "recurring_evidence": None}
SALE = {"item_id": "O2", "kind": "gain", "category": "disposal", "description": "Gain on sale",
        "evidence": _quote("L02", GAIN, amount="$20 million"), "recurring": False, "recurring_evidence": None}
RESTRUCTURE = {"item_id": "O3", "kind": "charge", "category": "restructuring", "description": "Restructuring",
               "evidence": _quote("L06", RESTRUCTURING, amount="$15 million"), "recurring": True,
               "recurring_evidence": _quote("L05", RECURS)}
UNQUANTIFIED = {"item_id": "U1", "category": "litigation", "description": "Litigation settlement",
                "evidence": _quote("L03", LITIGATION)}
OPERATIONAL = _quote("L04", OPERATIONS)


def test_tagged_one_offs_net_charges_against_gains():
    values = {("us-gaap:AssetImpairmentCharges", FY25): (120e6, "USD"),
              ("us-gaap:GoodwillImpairmentLoss", FY25): (30e6, "USD"),  # same group: the first tagged counts
              ("us-gaap:RestructuringCharges", FY25): (15e6, "USD"),
              ("us-gaap:GainLossOnSaleOfPropertyPlantEquipment", FY25): (20e6, "USD")}
    got = one_offs_for(values, {}, FY25)
    assert (got["one_off_charges"], got["one_off_gains"], got["net_one_offs"]) == (135e6, 20e6, 115e6)
    assert one_offs_for({}, {}, FY25)["net_one_offs_concept"] == "none tagged (taken as 0)"


def test_the_agent_reads_mdna_and_the_notes():
    t = leverage_t1_passages(_data())
    assert [(p["passage_id"], p["text"]) for p in t["passages"]] == [
        ("L01", IMPAIRMENT), ("L02", GAIN), ("L03", LITIGATION), ("L04", OPERATIONS), ("L05", RECURS),
        ("L06", RESTRUCTURING)]
    assert all(TAX not in p["text"] for p in t["passages"])  # no one-off or operating driver


def test_small_one_offs_a_going_concern_fail_or_no_score_do_not_fire(db_session):
    low = leverage_t1_trigger(_data(), _leverage(net=20e6), ENABLED)  # 5% of 400: not above
    assert (low["status"], low["fired"]) == ("not_triggered", False)
    assert low["reason"].startswith("Net one-offs as tagged are 20.0 million (5.0% of EBITDAR), not more than 5%")
    skipped = leverage_t1_trigger(_data(), _leverage(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_leverage_t1(_leverage(), skipped) == _leverage()
    unscored = _leverage()
    for c in unscored[1]["columns"]:
        c["composite"]["score"] = None
    assert leverage_t1_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_ebitdar_at_or_below_zero_fires_without_one_offs(db_session):
    result = _run(_says("operational", operational=[OPERATIONAL]), db_session, _leverage(ebitdar=-50e6, net=0.0))
    assert result["fired"] and result["fired_by"] == "EBITDAR is -50.0 million (zero or negative)"
    assert (result["status"], result["unresolved"]) == ("operational", False)
    assert result["reason"] == "The change in EBITDAR is operational (L04), with no one-offs found: no adjustment"
    m = apply_leverage_t1(_leverage(ebitdar=-50e6, net=0.0), result)[1]
    assert m["columns"][0]["ebitdar"] == -50e6 and m["trigger_summary"] == "trigger Debt/EBITDAR T1: operational"
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"leverage_t1": result}) == []


def test_a_fired_trigger_needs_the_agent_and_without_text_it_is_unresolved(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        leverage_t1_trigger(_data(), _leverage(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'leverage_t1' response"):
        leverage_t1_trigger(_data(), _leverage(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = leverage_t1_trigger(nothing, _leverage(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("unresolved", "no_text", True)
    assert result["unresolved_why"] == "no explanation for the one-offs or the level of EBITDAR"


def test_quantified_one_offs_normalise_ebitdar_and_rescore(db_session):
    result = _run(_says("normalised", [CHARGE, SALE]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("normalised", False, False)
    assert (result["normalisation"], result["ebitdar_used"]) == (100e6, 500e6)  # 400 + 120 charge − 20 gain
    metrics = _leverage()
    m = apply_leverage_t1(metrics, result)[1]
    col = m["columns"][0]
    assert (col["ebitdar_before_trigger"], col["ebitdar"]) == (400e6, 500e6)
    assert col["debt_to_ebitdar"]["ratio"] == pytest.approx(4.0) and col["net_debt_to_ebitdar"]["ratio"] == 3.6
    assert col["composite"]["score"] == _score_at(500e6) < col["ebitdar_adjustment"]["calculated"] == _score_at(400e6)
    assert metrics[1]["columns"][0]["ebitdar"] == 400e6  # the input is untouched
    assert m["trigger_summary"] == "trigger Debt/EBITDAR T1: normalised" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"leverage_t1": result}) == []
    assert [r["label"] for r in measure_card(m)["result_rows"]] == [
        "Debt / EBITDAR", "Debt / EBITDAR as calculated (before trigger Debt/EBITDAR T1)", "Debt / EBITDAR score",
        "Net debt / EBITDAR", "Net debt / EBITDAR as calculated (before trigger Debt/EBITDAR T1)",
        "Net debt / EBITDAR score", "Composite leverage score as calculated (before trigger Debt/EBITDAR T1)",
        "Composite leverage score (after trigger Debt/EBITDAR T1)"]
    assert leverage_t1_card(result)["label"] == "NORMALISED: RECOMPUTED"
    assert (result["smaller_reporting_company"], result["average_years"]) == (False, 3)


def test_normalised_ebitdar_at_or_below_zero_scores_10_with_the_flag(db_session):
    """EBITDAR 10 includes a one-off gain of 20: normalised EBITDAR is −10, so Debt/EBITDAR scores 10, no ratio."""
    metrics = _leverage(ebitdar=10e6, net=-20e6)
    result = _run(_says("normalised", [SALE]), db_session, metrics)
    assert (result["status"], result["ebitdar_used"]) == ("normalised", -10e6)
    col = apply_leverage_t1(metrics, result)[1]["columns"][0]
    for key in ("debt_to_ebitdar", "net_debt_to_ebitdar"):
        assert (col[key]["score"], col[key]["ratio"], col[key]["flag"]) == (10.0, None, "EBITDAR ≤ 0")
    assert col["composite"]["score"] == 10 and col["ebitdar_adjustment"]["calculated"] < 10


def test_a_recurring_item_is_explained_but_not_adjusted(db_session):
    result = _run(_says("normalised", [CHARGE, SALE, RESTRUCTURE]), db_session)
    assert result["status"] == "normalised" and result["normalisation"] == 100e6  # the 15 restructuring stays in
    assert [e["item_id"] for e in result["recurring"]] == ["O3"]
    unproven = _run(_says("normalised", [CHARGE, SALE, {**RESTRUCTURE, "recurring_evidence": _quote("L06",
                                                                                                    RESTRUCTURING)}]),
                    db_session)
    assert unproven["normalisation"] == 115e6  # the claim doesn't hold up: counted as a one-off
    assert unproven["agent"]["held_back"][0]["reason"] == "the quote doesn't show the item recurs"


def test_an_unquantified_one_off_uses_the_average_ebitdar(db_session):
    result = _run(_says("average_ebitdar", unquantified=[UNQUANTIFIED]), db_session)
    assert (result["status"], result["unresolved"]) == ("average_ebitdar", False)
    assert result["ebitdar_used"] == 500e6  # (400 + 500 + 600) / 3
    m = apply_leverage_t1(_leverage(), result)[1]
    col = m["columns"][0]
    assert col["ebitdar_basis"] == "3-year average EBITDAR (FY2023–FY2025)"
    assert col["composite"]["score"] == _score_at(500e6)
    card = measure_card(m)
    assert card["column_notes"][0]["entries"][0] == ("EBITDAR is the 3-year average EBITDAR (FY2023–FY2025) "
                                                     "(trigger Debt/EBITDAR T1)")
    src = _run(_says("average_ebitdar", unquantified=[UNQUANTIFIED]), db_session, _leverage(years=2),
               data=_data(small_business="☒"))
    assert (src["status"], src["unresolved"], src["smaller_reporting_company"]) == ("average_ebitdar", False, True)
    assert (src["ebitdar_used"], src["average"]["label"]) == (450e6, "2-year average EBITDAR (FY2024–FY2025)")
    assert leverage_t1_card(src)["label"] == "2-YEAR AVERAGE: RECOMPUTED"
    assert apply_leverage_t1(_leverage(years=2), src)[1]["trigger_summary"] == "trigger Debt/EBITDAR T1: 2-year average"
    short = _run(_says("average_ebitdar", unquantified=[UNQUANTIFIED]), db_session, _leverage(years=2))
    assert (short["status"], short["unresolved"]) == ("unresolved", True)
    assert short["reason"].startswith("Unresolved: a one-off is identified but not quantified, and fewer than 3 years")
    assert short["disagreement"]  # the agent read average_ebitdar


def test_partly_quantified_or_an_unexplained_remainder_is_unresolved(db_session):
    partly = _run(_says("unresolved", [CHARGE, SALE], [UNQUANTIFIED]), db_session)
    assert (partly["status"], partly["unresolved"], partly["disagreement"]) == ("unresolved", True, False)
    assert partly["unresolved_why"] == "partly quantified: U1 left unquantified"
    metrics = apply_leverage_t1(_leverage(), partly)
    m = metrics[1]
    assert m["columns"][0]["ebitdar"] == 400e6 and m["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t1": partly})
    assert (e["id"], e["measure"], e["calculated_score"]) == ("leverage_t1:leverage", "leverage", _score_at(400e6))
    assert "disagreement" not in e and "Unresolved: partly quantified: U1 left unquantified" in e["reason"]
    remainder = _run(_says("unresolved", [SALE]), db_session)  # 100 tagged, −20 verified: 80 short of it
    assert remainder["unresolved_why"] == ("the verified evidence leaves 120.0 million of the tagged net one-offs "
                                           "unexplained")


def test_operational_does_not_explain_tagged_one_offs_and_a_disagreement_escalates(db_session):
    result = _run(_says("operational", operational=[OPERATIONAL]), db_session)  # 100 tagged, no one-off verified
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, True)
    metrics = apply_leverage_t1(_leverage(), result)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t1": result})
    assert e["disagreement"] and "the agent read operational; the verified evidence gives unresolved" in e["reason"]


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    not_verbatim = {**CHARGE, "evidence": _quote("L01", "We recorded an impairment of $120 million.",
                                                 amount="$120 million")}
    no_item = {**SALE, "evidence": _quote("L04", OPERATIONS, amount="$20 million")}
    no_driver = _quote("L01", IMPAIRMENT)
    result = _run(_says("normalised", [not_verbatim, no_item], operational=[no_driver]), db_session)
    assert [(h["item_id"], h["reason"]) for h in result["agent"]["held_back"]] == [
        ("O1", "quote is not verbatim in the cited passage"),
        ("O2", "the quote names no one-off item (impairment, litigation, acquisition costs, restructuring, a gain or "
               "loss on a disposal…)"),
        ("P1", "the quote names no operating driver (volumes, pricing, costs…)")]
    assert (result["status"], result["unresolved"]) == ("unresolved", True)


def test_the_agents_answer_is_recomputed_when_the_verified_result_disagrees(db_session):
    """As for ACHC: the agent answers NORMALISED, its quotes don't hold up, so the verified outcome is unresolved. Its
    answer is still recomputed, at face value, on a copy: EBITDAR 400 + 120 charge − 20 gain = 500."""
    from app.services.fo.runner import agent_recomputation

    not_verbatim = {**CHARGE, "evidence": _quote("L01", "We recorded an impairment of $120 million.",
                                                 amount="$120 million")}
    result = _run(_says("normalised", [not_verbatim, SALE]), db_session)
    assert (result["status"], result["disagreement"]) == ("unresolved", True)
    reading = result["agent_reading"]
    assert (reading["status"], reading["ebitdar_used"], reading["normalisation"]) == ("normalised", 500e6, 100e6)
    metrics = _leverage()
    [rec] = agent_recomputation(metrics, apply_leverage_t1, result, "leverage_t1", "Debt/EBITDAR trigger T1")
    assert (rec["step"], rec["by"]) == ("leverage_t1:agent",
                                        "Debt/EBITDAR trigger T1: the agent's answer, if accepted (not applied)")
    by_label = {x["label"]: x for x in rec["results"]}
    assert (by_label["Debt / EBITDAR"]["before"], by_label["Debt / EBITDAR"]["after"]) == ("5.00x", "4.00x")
    composite = by_label["Composite leverage score"]
    assert composite["after"] == f"{_score_at(500e6):g}" and composite["before"] == f"{_score_at(400e6):g}"
    assert metrics[1]["columns"][0]["ebitdar"] == 400e6  # never applied to the scored measure
    applied = apply_leverage_t1(metrics, result)
    [e] = escalations_for(PASS, COMPLIANT, applied[1:], None, None, {"leverage_t1": result})
    assert f"the agent's answer, if accepted, gives composite leverage score {_score_at(500e6):g}" in e["reason"]
    unparsed = _run(_says("normalised", [{**SALE, "evidence": _quote("L02", GAIN, amount="twenty")}]), db_session)
    assert unparsed["agent_reading"]["ebitdar_used"] is None  # nothing readable: no recomputation
    assert unparsed["agent_reading"]["notes"][0].startswith("O2: left out")
    assert agent_recomputation(metrics, apply_leverage_t1, unparsed, "leverage_t1", "x") == []


def test_the_escalation_merges_with_an_unresolved_deal_on_leverage(db_session):
    result = _run(_says("unresolved", [CHARGE, SALE], [UNQUANTIFIED]), db_session)
    metrics = apply_leverage_t1(_leverage(), result)
    ad = {"unresolved_measures": ["leverage"], "deals": [], "reason": "Material divestiture, revenue not disclosed"}
    [e] = escalations_for(PASS, COMPLIANT, copy.deepcopy(metrics[1:]), ad, None, {"leverage_t1": result})
    assert e["checks"] == ["acquisitions", "leverage_t1"]  # one Debt/EBITDAR decision for both
