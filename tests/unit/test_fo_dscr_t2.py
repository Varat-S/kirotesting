"""FO Trigger Agent, DSCR T2: when it fires (CapEx above 1.5× depreciation, depreciation only; agent triggers
enabled), the passages it reads (MD&A liquidity and capital resources, the segment note), the verified evidence, the
first-match outcome (committed growth: no adjustment; split disclosed: recompute with the disclosed maintenance CapEx;
growth not quantified: depreciation as maintenance CapEx; no growth: hold the score), the escalation of DSCR only when
the trigger is unresolved, and T1 judging the DSCR T2 corrected.

Synthetic 10-K text and a scored DSCR column only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.dscr_t2 import apply_t2, dscr_t2_trigger, t2_passages
from app.services.fo.dscr_triggers import apply_t1, dscr_t1_trigger
from app.services.fo.measures import depreciation_for
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import measure_card
from app.services.fo.runner import escalations_for

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
CASH = ("At December 31, 2025, we had $1.2 billion of cash and cash equivalents and $2.0 billion available under our "
        "revolving credit facility.")
TOTAL = "Capital expenditures were $900 million in 2025, compared with $600 million in 2024."
SPLIT = ("Of our 2025 capital expenditures, $200 million was maintenance capital expenditures and the rest was growth "
         "capital expenditures.")
GROWTH = ("Our capital expenditures in 2025 included the expansion of our Texas manufacturing plant and new "
          "distribution centers.")
COMMITTED = ("At December 31, 2025, we had growth projects under construction with an estimated cost to complete of "
             "$350 million.")
FORECAST = "We expect maintenance capital expenditures of $250 million in 2026."
USEFUL_LIVES = ("Depreciation of property and equipment is computed using the straight-line method over the estimated "
                "useful lives.")
PPE_NOTE = ("Construction in progress includes the expansion of our Texas manufacturing plant, which we expect to "
            "complete in 2026.")
SEGMENTS = ("We manage our business in two segments, North America and International, and evaluate capital "
            "expenditures by segment.")
SEGMENT_TABLE = ["Capital expenditures", "North America", "600", "International", "300"]
SEGMENT_GROWTH = ("Segment capital expenditures in North America include the growth projects described in "
                  "Management's Discussion and Analysis.")
TAX = "Our effective tax rate was 21% in 2025, compared with 22% in 2024, on pre-tax income and capital expenditures."


def _data():
    mdna = ["Liquidity and Capital Resources", CASH, "Capital Expenditures", TOTAL, SPLIT, GROWTH, COMMITTED, FORECAST,
            "Critical Accounting Estimates", USEFUL_LIVES]
    notes = ["NOTE 5 — PROPERTY AND EQUIPMENT", PPE_NOTE, "NOTE 14 — SEGMENT INFORMATION", SEGMENTS, *SEGMENT_TABLE,
             SEGMENT_GROWTH, "NOTE 15 — INCOME TAXES", TAX]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _dscr(capex=900e6, depreciation=400e6, cash_flow=300e6, score=9, ratio=0.3, principal=700e6):
    """A scored DSCR: cash flow 300 (after CapEx 900) against interest 200 + principal 700 + rent 100 = 0.30x (9)."""
    forward = {"heading": "Next 12M from 2025-12-31", "period": FY25, "basis": "forward", "score": score,
               "ratio": ratio, "reason": None, "capex": capex, "capex_concept": "us-gaap:PaymentsToAcquireProductiveAssets",
               "depreciation": depreciation, "depreciation_concept": "Depreciation [us-gaap:Depreciation]",
               "cash_flow": cash_flow, "interest": 200e6, "rent": 100e6, "principal": principal,
               "principal_concept": "us-gaap:LongTermDebtCurrent", "principal_as_at": "2025-12-31",
               "debt_service": 300e6 + principal}
    return [{"key": "overall", "columns": [{"score": 7}]},
            {"key": "dscr", "title": "DSCR", "short": "DSCR", "rows": [], "bands": [],
             "columns": [forward, {**forward, "heading": "FY2025", "basis": "historical"}]}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, growth=(), committed=(), maintenance=None, grown=None):
    return {"dscr_t2": {"status": status, "growth": list(growth), "committed": list(committed),
                        "maintenance_capex": maintenance, "growth_capex": grown, "rationale": "Scripted.",
                        "missing_information": []}}


def _run(response, db_session, metrics=None, data=None):
    return dscr_t2_trigger(data or _data(), metrics or _dscr(), ENABLED, session=db_session,
                           backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


EXPANSION = _quote("K03", "included the expansion of our Texas manufacturing plant and new distribution centers")
UNDER_CONSTRUCTION = _quote("K04", "we had growth projects under construction")
MAINTENANCE = _quote("K02", SPLIT, amount="$200 million")


def test_the_agent_reads_mdna_capital_resources_and_the_segment_note():
    t2 = t2_passages(_data())
    assert [(p["passage_id"], p["section"], p["kind"], p["text"]) for p in t2["passages"]] == [
        ("K01", "mdna_capex", "text", TOTAL), ("K02", "mdna_capex", "text", SPLIT),
        ("K03", "mdna_capex", "text", GROWTH), ("K04", "mdna_capex", "text", COMMITTED),
        ("K05", "mdna_capex", "text", FORECAST), ("K06", "segment_note", "text", SEGMENTS),
        ("K07", "segment_note", "table", " | ".join(SEGMENT_TABLE)), ("K08", "segment_note", "text", SEGMENT_GROWTH)]
    # Not read: cash without a T2 term, critical accounting estimates, the PP&E note, the tax note.
    assert all(t not in p["text"] for p in t2["passages"] for t in (CASH, USEFUL_LIVES, PPE_NOTE, TAX))
    assert all(s["located"] for s in t2["sections"])


def test_capex_at_or_below_1_5x_depreciation_or_after_a_going_concern_fail_does_not_fire(db_session):
    at = dscr_t2_trigger(_data(), _dscr(capex=600e6), ENABLED)  # exactly 1.5×: not above
    assert (at["status"], at["fired"], at["agent"]) == ("not_triggered", False, None)
    assert at["reason"] == "CapEx is 1.50× depreciation, not above 1.5×: trigger T2 not set off"
    untagged = dscr_t2_trigger(_data(), _dscr(depreciation=None), ENABLED)
    assert (untagged["status"], untagged["reason"]) == ("not_triggered",
                                                        "No depreciation tagged: trigger T2 not set off")
    skipped = dscr_t2_trigger(_data(), _dscr(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_t2(_dscr(), skipped) == _dscr()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_t2_fires_whatever_dscr_scored_and_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        dscr_t2_trigger(_data(), _dscr(score=1, ratio=2.5), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'dscr_t2' response"):
        dscr_t2_trigger(_data(), _dscr(), ENABLED, backend=scripted_backend({"dscr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = dscr_t2_trigger(nothing, _dscr(), ENABLED)  # nothing to read: no call, the score is held
    assert (result["status"], result["decided_by"], result["fired"], result["unresolved"]) == (
        "no_growth", "no_text", True, False)


def test_committed_growth_comes_first_no_adjustment(db_session):
    result = _run(_says("committed_growth", [EXPANSION], [UNDER_CONSTRUCTION], MAINTENANCE), db_session)
    assert (result["status"], result["unresolved"], result["maintenance_capex"]) == ("committed_growth", False, None)
    assert result["reason"] == "Growth projects committed or under construction (K04): no adjustment"
    assert any("come first" in n for n in result["notes"])  # the split disclosed too doesn't count
    _, m = apply_t2(_dscr(), result)
    col = m["columns"][0]
    assert (col["score"], col["capex"], col["cash_flow"]) == (9, 900e6, 300e6) and "capex_reported" not in col
    assert m["trigger_summary"] == "trigger T2: no adjustment" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"dscr_t2": result}) == []


def test_a_disclosed_split_recomputes_with_the_disclosed_maintenance_capex(db_session):
    result = _run(_says("split_disclosed", [EXPANSION], maintenance=MAINTENANCE), db_session)
    assert (result["status"], result["maintenance_capex"], result["split"]["basis"]) == (
        "split_disclosed", 200e6, "maintenance")
    metrics = _dscr()
    _, m = apply_t2(metrics, result)
    col = m["columns"][0]
    # 300 + (900 − 200) = 1,000 against 1,000: 1.00x scores 5.
    assert (col["capex_reported"], col["capex"], col["cash_flow_reported"], col["cash_flow"]) == (
        900e6, 200e6, 300e6, 1_000e6)
    assert col["ratio"] == pytest.approx(1.0) and col["score"] == 5 and m["columns"][1]["score"] == 9
    adj = col["capex_adjustment"]
    assert (adj["calculated"], adj["recomputed"], adj["score"], adj["outcome"]) == (9, 5, 5, "split_disclosed")
    assert "DSCR trigger T2 (CapEx 2.25× depreciation > 1.5×" in m["trigger_note"] and "Resolved" in m["trigger_note"]
    assert metrics[1]["columns"][0]["capex"] == 900e6  # the input is untouched
    # Only growth CapEx disclosed: maintenance CapEx is total CapEx less it (900 − 650 = 250).
    grown = _run(_says("split_disclosed", grown=_quote("K02", SPLIT.replace("$200 million was maintenance",
                                                                             "$650 million was growth"),
                                                       amount="$650 million")), db_session,
                 data=_with_text(SPLIT, SPLIT.replace("$200 million was maintenance", "$650 million was growth")))
    assert (grown["split"]["basis"], grown["maintenance_capex"]) == ("growth", 250e6)


def _with_text(old, new):
    data = _data()
    for sec in data["document"]:
        sec["text"] = sec["text"].replace(old, new)
    return data


def test_growth_described_but_not_quantified_uses_depreciation_as_maintenance_capex(db_session):
    result = _run(_says("growth_not_quantified", [EXPANSION, _quote("K08", SEGMENT_GROWTH)]), db_session)
    assert (result["status"], result["maintenance_capex"]) == ("growth_not_quantified", 400e6)
    assert result["reason"].startswith("Growth described but not quantified (K03, K08): input correction")
    _, m = apply_t2(_dscr(), result)
    # 300 + (900 − 400) = 800 against 1,000: 0.80x scores 7.
    assert (m["columns"][0]["cash_flow"], m["columns"][0]["score"]) == (800e6, 7)
    card = measure_card(m)
    assert [r["label"] for r in card["result_rows"]] == [
        "DSCR", "DSCR as calculated (before trigger T2)", "Risk score as calculated (before trigger T2)",
        "Risk score (after trigger T2)"]


def test_no_growth_described_holds_the_score(db_session):
    result = _run(_says("no_growth"), db_session)
    assert (result["status"], result["unresolved"], result["reason"]) == (
        "no_growth", False, "No growth spending described: score held")
    _, m = apply_t2(_dscr(), result)
    assert m["columns"][0]["score"] == 9 and "capex_reported" not in m["columns"][0]


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    result = _run(_says("committed_growth",
                        growth=[_quote("K01", TOTAL)],  # a total, not growth
                        committed=[_quote("K03", GROWTH)],  # growth, but not committed or under construction
                        maintenance=_quote("K05", FORECAST, amount="$250 million")), db_session)
    reasons = [(h["item_id"], h["fact"], h["reason"]) for h in result["agent"]["held_back"]]
    assert reasons == [
        ("G1", "growth", "the quote describes no growth spending (expansion, new capacity, development or "
                         "construction)"),
        ("K1", "committed", "the quote doesn't say the project is committed or under construction"),
        ("S1", "maintenance CapEx", "the quote reads as a forecast, not the year's spending")]
    assert (result["status"], result["unresolved"]) == ("no_growth", True)
    too_big = _run(_says("split_disclosed", maintenance=_quote("K07", " | ".join(SEGMENT_TABLE), amount="600")),
                   db_session)  # the segment table's "600" is not maintenance CapEx
    assert too_big["agent"]["held_back"][0]["reason"] == "the quote doesn't say it is maintenance CapEx"
    wrong = _run(_says("split_disclosed", maintenance=_quote("K02", SPLIT, amount="$2,000 million")), db_session)
    assert wrong["agent"]["held_back"][0]["reason"] == "the amount $2,000 million is not in the quote"
    budget = "Approximately $240 million of this budgeted amount is maintenance capital expenditures."
    budgeted = _run(_says("split_disclosed", maintenance=_quote("K05", budget, amount="$240 million")), db_session,
                    data=_with_text(FORECAST, budget))  # next year's budget, not the year's spending
    assert budgeted["agent"]["held_back"][0]["reason"] == "the quote reads as a forecast, not the year's spending"


def test_an_unresolved_t2_escalates_dscr_and_merges_with_t1(db_session):
    result = _run(_says("no_growth", [EXPANSION]), db_session)  # the agent held the score; the evidence says growth
    assert (result["status"], result["unresolved"]) == ("growth_not_quantified", True)
    metrics = apply_t2(_dscr(), result)
    assert metrics[1]["trigger_escalated"] and metrics[1]["trigger_note"].endswith(
        "Escalated to an analyst, who accepts this score or overrides it.")
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"dscr_t2": result})
    assert (e["id"], e["measure"], e["engine_score"], e["calculated_score"], e["disagreement"]) == (
        "dscr_t2:dscr", "dscr", 7, 9, True)
    assert "the agent read no growth; the verified evidence gives growth not quantified" in e["reason"]
    # With T1 unresolved too, DSCR has one escalation carrying both, and its calculated score is before either.
    t1 = {"fired": True, "unresolved": True, "score": 7, "column": "Next 12M from 2025-12-31",
          "status": "no_adjustment", "reason": "x", "agent": {"stated_status": "adjusted"}}
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"dscr_t2": result, "dscr_t1": t1})
    assert (e["checks"], e["calculated_score"]) == (["dscr_t2", "dscr_t1"], 9)


def test_t1_judges_the_dscr_t2_corrected(db_session):
    # T2 lifts DSCR from 0.30x (9) to 1.00x (5): T1 (9 or more) is no longer set off, so no agent is needed for it.
    metrics = apply_t2(_dscr(), _run(_says("split_disclosed", maintenance=MAINTENANCE), db_session))
    t1 = dscr_t1_trigger(_data(), metrics, ENABLED)
    assert (t1["status"], t1["score"]) == ("not_triggered", 5)
    # Still 9 or more after T2: T1 fires on the corrected column and both notes are kept, T2's first.
    low = _dscr(cash_flow=-300e6, ratio=-0.3, score=10)
    metrics = apply_t2(low, _run(_says("split_disclosed", maintenance=MAINTENANCE), db_session, metrics=low))
    assert metrics[1]["columns"][0]["score"] == 9  # −300 + 700 = 400 against 1,000: 0.40x
    t1 = dscr_t1_trigger(_data(), metrics, ENABLED, session=db_session, backend=scripted_backend({"dscr_t1": {
        "status": "no_adjustment", "rollover": [], "refinancings": [], "rationale": "Scripted.",
        "missing_information": []}}), mode="scripted")
    m = apply_t1(metrics, t1)[1]
    assert m["trigger_note"].index("DSCR trigger T2") < m["trigger_note"].index("DSCR trigger T1")
    assert m["trigger_summary"] == "trigger T2: recomputed; trigger T1: no adjustment"


def test_depreciation_excludes_the_amortisation_of_acquired_intangibles():
    values = {("us-gaap:DepreciationDepletionAndAmortization", FY25): (500e6, "USD"),
              ("us-gaap:AmortizationOfIntangibleAssets", FY25): (120e6, "USD")}
    src, dep = depreciation_for(values, {}, FY25)
    assert dep == 380e6 and "amortisation of intangibles" in src
    values[("us-gaap:Depreciation", FY25)] = (350e6, "USD")  # tagged: used as is
    assert depreciation_for(values, {}, FY25)[1] == 350e6
    assert depreciation_for({}, {}, FY25) == (None, None)
