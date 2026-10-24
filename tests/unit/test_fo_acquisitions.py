"""FO acquisitions-and-divestitures pre-check: the passages, the agent's verified evidence, the per-deal rules
(first match), and the adjustments to DSO and Debt/EBITDAR.

Synthetic 10-K text and facts only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.acquisitions import (
    acquisitions_precheck,
    amount_in,
    apply_deal_adjustments,
    deal_passages,
    parse_amount,
)
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import acquisitions_card, acquisitions_status, measure_card
from app.services.fo.runner import escalations_for, gate_for
from app.services.fo.scoring import SCORING_DEFAULTS, composite_risk
from app.services.llm.client import FakeLLMBackend

FY25, FY24 = "2025-01-01--2025-12-31", "2024-01-01--2024-12-31"
REVENUE = 10_000e6

ACQ = "On April 1, 2025, we completed the acquisition of Beta Health for a purchase price of $2.4 billion in cash."
PRO_FORMA = ("The following unaudited pro forma information presents the combined results as if the acquisition had "
             "occurred on January 1, 2024 (dollars in millions):")
PRO_FORMA_TABLE = ["Pro forma", "2025", "2024", "Revenues", "10,600", "10,100", "Net income", "820", "760"]
DIV = ("On September 30, 2025, we sold our Delta clinics business for proceeds of $300 million. The Delta clinics "
       "business contributed revenues of $150 million in 2025 prior to the sale.")
DISC = ("On June 30, 2025, we completed the sale of our Gamma pharmacy business, which is presented as discontinued "
        "operations for all periods.")
NOT_MATERIAL = ("During 2025, we also acquired three physician practices for $40 million; pro forma information has "
                "not been presented because the acquisitions were not material.")
BIG_SALE = "On November 15, 2025, we sold our Omega imaging business for proceeds of $1.5 billion."
SMALL_SALE = "On November 15, 2025, we sold a surgery center for proceeds of $60 million."
PRIOR = "On March 3, 2024, we acquired Zeta Labs for $500 million."
AFTER_SALE = ("On February 14, 2026, we completed the sale of our Sigma labs business for proceeds of $900 million and "
              "used $800 million of the proceeds to repay borrowings under our term loan.")
AFTER_FIGURES = ("The Sigma labs business generated revenues of $400 million, operating income of $90 million and "
                 "depreciation and amortization of $20 million in 2025, and was classified as held for sale at "
                 "December 31, 2025.")
CASH_FLOW = ["Cash flows from investing activities:", "Acquisition of hospitals and health care entities", "(397)"]


def _fact(concept, period, value):
    return {"concept": concept, "period_key": period, "value": value, "type": "numeric", "dimensions": {},
            "unit": "USD"}


FACTS = [_fact("us-gaap:Revenues", FY25, REVENUE), _fact("us-gaap:Revenues", FY24, 9_000e6),
         _fact("us-gaap:AccountsReceivableNetCurrent", "2025-12-31", 1_500e6),
         _fact("us-gaap:AccountsReceivableNetCurrent", "2024-12-31", 1_200e6),
         _fact("us-gaap:Assets", "2025-12-31", 20_000e6)]


def _data(*, deals=(), table=(), subsequent=(), statements=CASH_FLOW, policies=()):
    notes = [*statements, "NOTE 1 — ACCOUNTING POLICIES",
             "We prepare our statements in accordance with generally accepted accounting principles.", *policies,
             "NOTE 3 — ACQUISITIONS AND DIVESTITURES", *deals, *table, "NOTE 4 — INCOME TAXES",
             "Our effective tax rate was 21% in 2025, compared with 22% in 2024, on pre-tax income.",
             *(["NOTE 16 — SUBSEQUENT EVENTS", *subsequent] if subsequent else [])]
    return {"meta": {"entity_name": "Example Co", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": FACTS, "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "We divested two clinics in 2025, as Note 3 explains."},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _ev(fact, pid, quote, amount=None, date=None):
    return {"fact": fact, "passage_id": pid, "quote": quote, "amount": amount, "date": date}


def _deal(did, kind, outcome, *evidence, timing="in_year", presentation=None, materiality="not_stated"):
    return {"deal_id": did, "kind": kind, "description": did, "timing": timing,
            "presentation": presentation or ("not_applicable" if kind == "acquisition" else "continuing"),
            "materiality": materiality, "evidence": list(evidence), "outcome": outcome}


def _says(status, *deals):
    return {"acquisitions": {"status": status, "deals": list(deals), "rationale": "Scripted.",
                             "missing_information": []}}


def _run(data, response, db_session):
    return acquisitions_precheck(data, session=db_session, backend=scripted_backend(response), mode="scripted",
                                 case_id="EX_FY2025")


def test_the_deal_notes_are_read_in_full_with_tables_and_their_scale():
    ad = deal_passages(_data(deals=[ACQ, PRO_FORMA], table=PRO_FORMA_TABLE, subsequent=[AFTER_SALE]))
    assert [(p["passage_id"], p["section"], p["kind"]) for p in ad["passages"]] == [
        ("P01", "acquisitions_note", "text"), ("P02", "acquisitions_note", "text"),
        ("P03", "acquisitions_note", "table"), ("P04", "subsequent_events", "text")]
    assert ad["passages"][2]["text"] == " | ".join(PRO_FORMA_TABLE) and ad["passages"][2]["scale"] == 1e6
    # Not read: the cash flow statement's "Acquisition of…" line (before the notes), MD&A, the tax note.
    assert all("397" not in p["text"] and "Note 3 explains" not in p["text"] and "tax rate" not in p["text"]
               for p in ad["passages"])
    located = {s["key"]: s["located"] for s in ad["sections"]}
    assert located["acquisitions_note"] and located["subsequent_events"] and not located["discontinued_operations"]


def test_a_discontinued_operations_sub_heading_and_strong_mentions_elsewhere_are_collected():
    data = _data(policies=["Discontinued Operations", DISC, "Revenue Recognition Policy",
                           "Revenue is recognized when control of the promised services transfers to patients."])
    ad = deal_passages(data)
    assert [(p["section"], p["text"]) for p in ad["passages"]] == [("discontinued_operations", DISC)]
    other = deal_passages(_data(policies=[DISC]))["passages"]
    assert [(p["section"], p["text"]) for p in other] == [("other", DISC)]  # inside note 1, no sub-heading


def test_no_deal_passage_means_no_deal_and_no_agent_call(db_session):
    result = acquisitions_precheck(_data(statements=[]), session=db_session, backend=FakeLLMBackend(),
                                   mode="scripted")
    assert (result["status"], result["decided_by"], result["agent"]) == ("none", "no_hits", None)
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_deal_passage_always_needs_the_agent(db_session):
    data = _data(deals=[ACQ])
    with pytest.raises(PrecheckAgentError, match="Pre-check Agent is required"):
        acquisitions_precheck(data)
    with pytest.raises(PrecheckAgentError, match="no 'acquisitions' response"):
        acquisitions_precheck(data, backend=scripted_backend({"covenants": {"status": "compliant"}}),
                              mode="scripted")
    rejected = FakeLLMBackend()
    rejected.register("analyze", "not json", key="fo_precheck_acquisitions")
    with pytest.raises(PrecheckAgentError, match="output was rejected .*the run was not recorded"):
        acquisitions_precheck(data, session=db_session, backend=rejected, mode="scripted")


def test_an_amount_must_be_a_whole_number_in_its_quote():
    assert amount_in("150", "Medicare | 150 | 140") and amount_in("$2.4 billion", ACQ)
    assert not amount_in("15", "Medicare | 150") and not amount_in("2.4", "$12.45 billion")
    assert not amount_in("1,234", "21,234") and amount_in("10,600", "Revenues | 10,600.")


def test_amounts_are_read_with_their_scale():
    assert parse_amount("$2.4 billion", None) == (2.4e9, None)
    assert parse_amount("10,600", 1e6) == (10_600e6, None)
    assert parse_amount("(397)", 1e6) == (397e6, None)
    assert parse_amount("10,600", None)[1] == "the amount's unit (thousands, millions) isn't stated in its passage"


def test_first_match_per_deal_and_the_dso_revenue(db_session):
    data = _data(deals=[ACQ, PRO_FORMA, DIV, DISC, NOT_MATERIAL], table=PRO_FORMA_TABLE)
    result = _run(data, _says(
        "adjusted",
        _deal("D1", "acquisition", "pro_forma_revenue", _ev("deal", "P01", ACQ, date="2025-04-01"),
              _ev("consideration", "P01", ACQ, amount="$2.4 billion"),
              _ev("pro_forma_revenue", "P06", "Revenues | 10,600", amount="10,600")),
        _deal("D2", "divestiture", "remove_divested_revenue",
              _ev("deal", "P03", "On September 30, 2025, we sold our Delta clinics business", date="2025-09"),
              _ev("divested_revenue", "P03", "contributed revenues of $150 million in 2025", amount="$150 million")),
        _deal("D3", "divestiture", "discontinued_operations", _ev("deal", "P04", DISC, date="2025-06-30"),
              _ev("discontinued_operations", "P04", "which is presented as discontinued operations"),
              presentation="discontinued_operations"),
        _deal("D4", "acquisition", "not_material", _ev("deal", "P05", NOT_MATERIAL, date="2025"),
              _ev("not_material", "P05", "pro forma information has not been presented because the acquisitions "
                                         "were not material"), materiality="not_material")), db_session)
    assert (result["status"], result["decided_by"]) == ("adjusted", "agent")
    assert [(d["deal_id"], d["outcome"], d["timing"]) for d in result["deals"]] == [
        ("D1", "pro_forma_revenue", "in_year"), ("D2", "remove_divested_revenue", "in_year"),
        ("D3", "discontinued_operations", "in_year"), ("D4", "not_material", "in_year")]
    dso = result["adjustments"]["dso"]
    assert (dso["revenue_reported"], dso["added"], dso["removed"]) == (REVENUE, 600e6, 150e6)
    assert dso["revenue"] == REVENUE + 600e6 - 150e6 and dso["deals"] == ["D1", "D2"]
    assert result["adjustments"]["leverage"] is None and result["unresolved_measures"] == []
    assert any("ASC 805 pro forma gives revenue and earnings, not EBITDAR" in n for n in result["notes"])
    assert result["deals"][0]["shared_with"] == ["leverage", "dso", "key_input_trigger"]
    assert result["agent"]["status_consistent"] and result["agent"]["held_back_evidence"] == []


def test_a_material_deal_without_revenue_is_unresolved_a_small_one_is_not_material_by_size(db_session):
    data = _data(deals=[BIG_SALE, SMALL_SALE])
    result = _run(data, _says(
        "unresolved",
        _deal("D1", "divestiture", "unresolved", _ev("deal", "P01", BIG_SALE, date="2025-11-15"),
              _ev("consideration", "P01", BIG_SALE, amount="$1.5 billion")),
        _deal("D2", "divestiture", "not_material", _ev("deal", "P02", SMALL_SALE, date="2025-11-15"),
              _ev("consideration", "P02", SMALL_SALE, amount="$60 million"))), db_session)
    assert [(d["deal_id"], d["materiality"], d["outcome"]) for d in result["deals"]] == [
        ("D1", "material", "unresolved"), ("D2", "not_material", "not_material")]
    assert "7.5% of total assets" in result["deals"][0]["materiality_basis"]
    assert (result["status"], result["unresolved_measures"], result["review_required"]) == ("unresolved", ["dso"],
                                                                                           True)
    assert result["adjustments"] == {"dso": None, "leverage": None}


def test_a_divestiture_after_the_year_end_adjusts_debt_and_ebitdar_together(db_session):
    data = _data(deals=["We had no acquisitions in 2025 other than the Sigma transaction described in Note 16."],
                 subsequent=[AFTER_SALE, AFTER_FIGURES])
    evidence = [_ev("deal", "P02", AFTER_SALE, date="2026-02-14"),
                _ev("debt_repaid", "P02", "used $800 million of the proceeds to repay borrowings", amount="$800 million"),
                _ev("divested_revenue", "P03", "generated revenues of $400 million", amount="$400 million"),
                _ev("held_for_sale", "P03", "was classified as held for sale at December 31, 2025"),
                _ev("ebit", "P03", "operating income of $90 million", amount="$90 million"),
                _ev("da", "P03", "depreciation and amortization of $20 million", amount="$20 million")]
    result = _run(data, _says("adjusted", _deal("D1", "divestiture", "remove_divested_revenue", *evidence,
                                                timing="after_year_end", presentation="held_for_sale")), db_session)
    [deal] = result["deals"]
    assert (deal["timing"], deal["presentation"], deal["outcome"]) == ("after_year_end", "held_for_sale",
                                                                       "remove_divested_revenue")
    assert result["adjustments"]["dso"]["removed"] == 400e6  # held for sale: its receivables are out of AR
    lev = result["adjustments"]["leverage"]
    assert (lev["ebitdar"], lev["debt"]) == (-110e6, -800e6) and "together" in lev["how"]

    no_ebitdar = _run(data, _says("adjusted", _deal("D1", "divestiture", "remove_divested_revenue",
                                                    *evidence[:4], timing="after_year_end")), db_session)
    assert no_ebitdar["adjustments"]["leverage"] is None  # never one side
    assert no_ebitdar["unresolved_measures"] == ["leverage"] and no_ebitdar["status"] == "unresolved"
    assert any("never one side" in n and "can't be built from the 10-K" in n for n in no_ebitdar["notes"])


def test_evidence_that_does_not_hold_up_is_held_back_and_the_outcome_rederived(db_session):
    data = _data(deals=[ACQ, PRO_FORMA, PRIOR], table=PRO_FORMA_TABLE)
    result = _run(data, _says(
        "adjusted",
        _deal("D1", "acquisition", "pro_forma_revenue", _ev("deal", "P01", ACQ),
              _ev("date", "P01", ACQ, date="2025-05-01"),  # the quote says April
              _ev("pro_forma_revenue", "P04", "Revenues | 10,600", amount="11,600"),  # not in the quote
              _ev("pro_forma_revenue", "P04", "Net income | 820", amount="820"),  # names no revenue
              _ev("consideration", "P09", ACQ, amount="$2.4 billion")),  # never supplied
        _deal("D2", "acquisition", "pro_forma_revenue", _ev("deal", "P03", PRIOR, date="2024-03-03"),
              timing="in_year"),
        _deal("D3", "acquisition", "unresolved", _ev("deal", "P01", "We bought a hospital in Ohio in 2025."))),
        db_session)
    reasons = [h["reason"] for h in result["agent"]["held_back_evidence"]]
    assert reasons == ["the date 2025-05-01 is not in the quote", "the amount 11,600 is not in the quote",
                       "quote names no revenue", "cites a passage that was not supplied",
                       "quote is not verbatim in the cited passage"]
    assert [h["reason"] for h in result["agent"]["held_back_deals"]] == ["no verified quote discloses the deal"]
    d1, d2 = result["deals"]
    assert (d1["outcome"], d1["materiality"], d1["timing"]) == ("unresolved", "material", "in_year")
    assert "as the agent read it" in d1["timing_basis"]  # no verified date
    assert (d2["timing"], d2["outcome"]) == ("prior_year", "not_for_the_year")  # the date decides, not the agent
    assert result["status"] == "unresolved" and not result["agent"]["status_consistent"]
    assert any("D1: the agent gave pro forma revenue; the verified evidence gives unresolved" in n
               for n in result["notes"])
    # The pro forma table is a deal fact no verified deal uses: the cross-check flags it.
    assert any("keyword rules found a pro forma revenue statement in P04" in n for n in result["notes"])


def test_pro_forma_revenue_below_reported_revenue_is_held_back(db_session):
    data = _data(deals=[ACQ, PRO_FORMA], table=["Pro forma", "2025", "Revenues", "9,600"])
    result = _run(data, _says("adjusted", _deal(
        "D1", "acquisition", "pro_forma_revenue", _ev("deal", "P01", ACQ, date="2025-04-01"),
        _ev("pro_forma_revenue", "P03", "Revenues | 9,600", amount="9,600"))), db_session)
    assert "below the reported revenue" in result["agent"]["held_back_evidence"][0]["reason"]


def _dso_and_leverage():
    dso = {"key": "dso", "columns": [
        {"heading": "FY25", "period": FY25, "receivables": 1_500e6, "revenue": REVENUE, "dso": 54.75,
         "prior_dso": 48.67, "change": 6.08, "reason": None},
        {"heading": "FY24", "period": FY24, "receivables": 1_200e6, "revenue": 9_000e6, "dso": 48.67,
         "prior_dso": None, "change": None, "reason": "No prior-year DSO"}]}
    leverage = {"key": "leverage", "columns": [
        {"heading": "FY25", "period": FY25, "ebitdar": 2_000e6, "debt": 5_000e6, "adj_debt": 6_000e6,
         "cash": 1_000e6, "adj_net_debt": 5_000e6,
         "debt_to_ebitdar": {"ratio": 3.0, "negative_ebitda": False, "reason": None},
         "net_debt_to_ebitdar": {"ratio": 2.5, "negative_ebitda": False, "reason": None}}]}
    return [dso, leverage]


def test_the_adjustments_recompute_dso_and_leverage_on_the_latest_year():
    ad = {"adjustments": {
        "dso": {"period": FY25, "revenue_reported": REVENUE, "added": 600e6, "removed": 0.0, "revenue": 10_600e6,
                "deals": ["D1"]},
        "leverage": {"period": FY25, "ebitdar": -110e6, "debt": -800e6, "deals": ["D2"], "how": "together"}},
        "unresolved_measures": []}
    fo = _dso_and_leverage()
    dso, lev = apply_deal_adjustments(fo, ad)
    col = dso["columns"][0]
    assert (col["revenue_reported"], col["revenue"]) == (REVENUE, 10_600e6)
    assert col["dso"] == pytest.approx(1_500e6 / (10_600e6 / 365)) and col["change"] == pytest.approx(
        col["dso"] - 48.67)
    assert dso["columns"][1]["revenue"] == 9_000e6 and "prior year is as reported" in dso["deal_note"]
    c = lev["columns"][0]
    assert (c["ebitdar"], c["debt"], c["adj_debt"], c["adj_net_debt"]) == pytest.approx(
        (1_890e6, 4_200e6, 5_200e6, 4_200e6))
    assert c["debt_to_ebitdar"]["ratio"] == pytest.approx(5_200 / 1_890) and c["ebitdar_reported"] == 2_000e6
    assert fo[0]["columns"][0]["revenue"] == REVENUE  # the inputs are untouched


def test_an_unresolved_measure_is_escalated_for_an_analyst_override():
    ad = {"status": "unresolved", "reason": "A material acquisition or divestiture whose revenue isn't disclosed",
          "adjustments": {"dso": None, "leverage": None}, "unresolved_measures": ["dso"],
          "deals": [{"deal_id": "D1", "effects": {"unresolved": ["dso"]}}]}
    dso = apply_deal_adjustments(_dso_and_leverage(), ad)[0]
    assert dso["deal_unresolved"] and "escalated to an analyst" in dso["deal_note"]
    dso["columns"][0]["score"] = 6.2
    [e] = escalations_for({"status": "pass"}, {"status": "compliant"}, [dso], ad)
    assert (e["id"], e["type"], e["measure"], e["engine_score"]) == ("acquisitions:dso", "measure_override", "dso",
                                                                       6.2)
    assert e["reason"].endswith("(D1)") and e["title"] == "acquisition or divestiture unresolved"
    assert gate_for("pass", "compliant", ad)["requires_decision"]
    assert escalations_for({"status": "fail", "reason": "x"}, {"status": "compliant"}, [dso], ad)[0][
        "type"] == "run_decision"  # a going-concern fail supersedes the measures
    settings = {"values": dict(SCORING_DEFAULTS)}
    fo = [{"key": "leverage", "columns": [{"heading": "FY", "period": FY25, "composite": {"score": 4.0}}]},
          {"key": "liquidity", "columns": [{"heading": "FY", "score": 3}]}, dso]
    rows = {r["key"]: r for r in composite_risk(fo, settings)["rows"]}
    assert "acquisition or divestiture unresolved: as reported, escalated" in rows["dso"]["notes"]


def _decision(measure, decision, analyst, engine=6.2):
    return {"measure": measure, "source": "acquisitions", "decision": decision, "engine_score": engine,
            "analyst_score": analyst, "reviewer": "A", "reason": "r", "decided_at": "2026-10-22T00:00:00"}


def test_the_unresolved_tag_becomes_the_analysts_decision():
    deal = {"deal_id": "D1", "kind": "divestiture", "timing": "in_year", "presentation": "continuing",
            "outcome": "unresolved", "outcome_text": "Material deal, but its revenue isn't disclosed: unresolved",
            "evidence": [], "effects": {"dso": None, "leverage": None, "unresolved": ["dso", "leverage"]}}
    ad = {"status": "unresolved", "decided_by": "agent", "mode": "scripted", "reason": "x", "effect": "y",
          "deals": [deal], "unresolved_measures": ["dso", "leverage"]}
    card = acquisitions_card(ad)
    assert (card["label"], card["deals"][0]["outcome_label"]) == ("UNRESOLVED", "unresolved")
    assert acquisitions_status(ad, []) == "unresolved"

    one = [_decision("dso", "override", 7)]  # leverage is still waiting
    assert acquisitions_card(ad, one)["label"] == "UNRESOLVED" and acquisitions_status(ad, one) == "unresolved"
    assert [e["tag"] for e in acquisitions_card(ad, one)["escalated"]] == ["Overridden by analyst", "Unresolved"]

    both = [*one, _decision("leverage", "override", 5, engine=4.1)]
    card = acquisitions_card(ad, both)
    assert (card["label"], card["deals"][0]["outcome_label"]) == ("OVERRIDDEN BY ANALYST", "Overridden by analyst")
    assert card["escalated"][0]["score"] == "7 (engine 6.2)" and "DSO: Overridden by analyst" in card["deals"][0][
        "effects_text"]

    kept = [_decision("dso", "accept", 6.2), _decision("leverage", "accept", 4.1, engine=4.1)]
    card = acquisitions_card(ad, kept)
    assert (card["label"], card["deals"][0]["outcome_label"], acquisitions_status(ad, kept)) == (
        "ENGINE SCORE", "Engine score", "Engine score")

    mixed = [_decision("dso", "override", 7), _decision("leverage", "accept", 4.1, engine=4.1)]
    assert acquisitions_status(ad, mixed) == "DSO: Overridden by analyst · Debt/EBITDAR: Engine score"

    dso = {**apply_deal_adjustments(_dso_and_leverage(), ad)[0], "title": "DSO", "short": "DSO change"}
    assert measure_card(dso)["deal_tag"] == "Unresolved"
    decided = measure_card(dso, kept[0])
    assert decided["deal_tag"] == "Engine score" and "Unresolved acquisition" not in decided["deal_note"]
    assert measure_card(dso, mixed[0])["deal_tag"] == "Overridden by analyst"
