"""FO Trigger Agent, DSCR T1: when it fires (score 9 or more, below 0.5x; agent triggers enabled), the passages it
reads, the verified evidence, the two independent adjustments (T1a: rollover debt with a qualifying backstop removed
from principal; T1b: −2 for an executed refinancing not already classified as long-term, a plan gets no adjustment)
and the escalation of DSCR, only when the trigger is unresolved (the agent and the verified evidence disagree).

Synthetic 10-K text and a scored DSCR column only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.dscr_triggers import apply_t1, dscr_t1_trigger, t1_passages
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.runner import escalations_for

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
MDNA = "At December 31, 2025, we had $2.0 billion available under our revolving credit facility."
SCALE_LINE = "Our debt at December 31, 2025 consisted of the following (in millions):"
TABLE = ["Commercial paper", "1,500", "Senior notes due March 2026", "1,000", "Revolving credit facility", "500"]
CP_TEXT = ("We issue commercial paper from time to time to fund working capital, and we roll over maturing commercial "
           "paper with new issuances.")
BACKSTOP = ("Our commercial paper program is backed by our $2.5 billion revolving credit facility, which matures in "
            "June 2029; $2.0 billion of it was undrawn at December 31, 2025.")
REVOLVER_TEXT = "Borrowings under the revolving credit facility may be repaid and reborrowed at any time."
IN_YEAR = "In November 2025, we redeemed $300 million of senior notes due 2026 with cash on hand."
SECURITISATION = ("Our $400 million accounts receivable securitization facility matures in September 2026 and is "
                  "renewed annually.")
RECLASS = ("The senior notes due March 2026 were classified as long-term at December 31, 2025 because we had the "
           "intent and ability to refinance them.")
REFI = ("In February 2026, we issued $1.0 billion of 5.25% senior notes due 2031 and used the net proceeds to redeem "
        "the senior notes due March 2026.")
PLAN = "We intend to refinance the remaining 2026 maturities before they come due."
TAX = "Our effective tax rate was 21% in 2025, compared with 22% in 2024, on pre-tax income."


def _data(backstop=BACKSTOP):
    notes = ["NOTE 7 — LONG-TERM DEBT", SCALE_LINE, *TABLE, CP_TEXT, backstop, REVOLVER_TEXT, IN_YEAR, SECURITISATION,
             RECLASS, "NOTE 12 — SUBSEQUENT EVENTS", REFI, PLAN, "NOTE 13 — INCOME TAXES", TAX]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(["Liquidity and Capital Resources", MDNA])},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _dscr(score=9, concept="us-gaap:DebtCurrent", **col):
    """A scored DSCR: cash flow 1,320 against interest 200 + principal 3,000 + rent 100 = 0.40x (0–0.5x: 9)."""
    forward = {"heading": "Next 12M from 2025-12-31", "period": FY25, "basis": "forward", "score": score,
               "ratio": 0.4, "reason": None, "principal": 3_000e6, "principal_concept": concept,
               "principal_as_at": "2025-12-31", "interest": 200e6, "rent": 100e6, "cash_flow": 1_320e6,
               "debt_service": 3_300e6, **col}
    return [{"key": "overall", "columns": [{"score": 7}]},
            {"key": "dscr", "columns": [forward, {**forward, "heading": "FY2025", "basis": "historical", "score": 9}]}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _backstop(undrawn="$2.0 billion", matures="2029-06", quote=BACKSTOP):
    return _quote("D04", quote, amount=undrawn, date=matures)


CP = {"item_id": "R1", "kind": "commercial_paper", "description": "Commercial paper", "in_year1_principal": True,
      "balance": _quote("D02", "Commercial paper | 1,500", amount="1,500"),
      "rollover": _quote("D03", "we roll over maturing commercial paper with new issuances"), "backstop": _backstop()}
REV = {"item_id": "R2", "kind": "revolver", "description": "Revolver borrowings", "in_year1_principal": True,
       "balance": _quote("D02", "Revolving credit facility | 500", amount="500"),
       "rollover": _quote("D05", "may be repaid and reborrowed"), "backstop": _backstop()}
SEC = {"item_id": "R3", "kind": "securitisation_facility", "description": "Receivables securitisation",
       "in_year1_principal": True, "balance": _quote("D07", SECURITISATION, amount="$400 million"),
       "rollover": _quote("D07", "is renewed annually"), "backstop": None}
EXECUTED = {"item_id": "F1", "kind": "executed", "description": "March 2026 notes redeemed with new notes",
            "event": _quote("D09", REFI, date="2026-02"),
            "maturity": _quote("D09", "the senior notes due March 2026", date="2026-03"),
            "amount": _quote("D09", "we issued $1.0 billion of 5.25% senior notes", amount="$1.0 billion"),
            "reclassified": None}
PLANNED = {"item_id": "F2", "kind": "plan", "description": "Remaining 2026 maturities",
           "event": _quote("D10", PLAN, date=None), "maturity": None, "amount": None, "reclassified": None}


def _says(status, rollover=(), refinancings=()):
    return {"dscr_t1": {"status": status, "rollover": list(rollover), "refinancings": list(refinancings),
                        "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None, data=None):
    return dscr_t1_trigger(data or _data(), metrics or _dscr(), ENABLED, session=db_session,
                           backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def _outcomes(result, key="rollover"):
    return [(e["item_id"], e["outcome"], e.get("removed")) for e in result[key]]


def test_the_agent_reads_the_debt_note_mdna_liquidity_and_subsequent_events():
    t1 = t1_passages(_data())
    assert [(p["passage_id"], p["section"], p["kind"]) for p in t1["passages"]] == [
        ("D01", "mdna_liquidity", "text"), ("D02", "debt_note", "table"), ("D03", "debt_note", "text"),
        ("D04", "debt_note", "text"), ("D05", "debt_note", "text"), ("D06", "debt_note", "text"),
        ("D07", "debt_note", "text"), ("D08", "debt_note", "text"),
        ("D09", "subsequent_events", "text"), ("D10", "subsequent_events", "text")]
    assert t1["passages"][1]["text"] == " | ".join(TABLE) and t1["passages"][1]["scale"] == 1e6
    assert all(TAX not in p["text"] for p in t1["passages"])  # the tax note closes the subsequent-events note
    assert all(s["located"] for s in t1["sections"])


def test_below_9_or_after_a_going_concern_fail_the_trigger_does_not_fire(db_session):
    low = dscr_t1_trigger(_data(), _dscr(score=7), ENABLED)  # 0.5–1.0x: no backend needed, nothing to interpret
    assert (low["status"], low["fired"], low["agent"]) == ("not_triggered", False, None)
    skipped = dscr_t1_trigger(_data(), _dscr(score=10), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_t1(_dscr(), skipped) == _dscr()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fired_trigger_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        dscr_t1_trigger(_data(), _dscr(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'dscr_t1' response"):
        dscr_t1_trigger(_data(), _dscr(), ENABLED, backend=scripted_backend({"prepaid": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = dscr_t1_trigger(nothing, _dscr(), ENABLED)  # nothing to read: no call, no adjustment, nothing unresolved
    assert (result["status"], result["decided_by"], result["fired"], result["unresolved"]) == (
        "no_adjustment", "no_text", True, False)


def test_t1a_and_t1b_together_remove_rollover_debt_and_take_2_off(db_session):
    result = _run(_says("adjusted", [CP, REV], [EXECUTED]), db_session)
    assert (result["status"], result["decided_by"], result["agent"]["status_consistent"]) == ("adjusted", "agent", True)
    # 1,500 + 500 fit within the 2,000 undrawn on the facility, which matures more than 12 months out.
    assert _outcomes(result) == [("R1", "remove_from_principal", 1_500e6), ("R2", "remove_from_principal", 500e6)]
    assert result["principal_removed"] == 2_000e6 and result["refinancing_relief"] == 2
    [f] = result["refinancings"]
    assert (f["outcome"], f["date"], f["maturity"], f["amount"]) == ("executed", "2026-02", "2026-03", 1e9)
    assert f["share_of_principal"] == pytest.approx(1 / 3) and result["shared_with"] == ["liquidity_t2"]
    metrics = _dscr()
    _, m = apply_t1(metrics, result)
    col = m["columns"][0]
    assert (col["principal_reported"], col["principal"], col["debt_service"]) == (3_000e6, 1_000e6, 1_300e6)
    assert col["ratio"] == pytest.approx(1_320 / 1_300)
    adj = col["trigger_adjustment"]
    # 0.40x scores 9; 1.02x (1.0–1.25x) scores 5; the executed refinancing takes it to 3.
    assert (adj["calculated"], adj["recomputed"], adj["refinancing"], adj["score"]) == (9, 5, -2, 3)
    assert col["score"] == 3 and m["columns"][1]["score"] == 9  # only the headline (forward) column
    assert "T1a:" in m["trigger_note"] and "T1b:" in m["trigger_note"] and "Resolved" in m["trigger_note"]
    assert metrics[1]["columns"][0]["principal"] == 3_000e6  # the input is untouched
    # The agent and the verified evidence agree: the adjustment stands, nothing is escalated.
    assert not result["unresolved"] and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"dscr_t1": result}) == []


def test_agreed_no_adjustment_leaves_the_score_as_calculated_and_escalates_nothing(db_session):
    result = _run(_says("no_adjustment", [SEC]), db_session)
    assert (result["status"], result["unresolved"], result["agent"]["status_consistent"]) == (
        "no_adjustment", False, True)
    _, m = apply_t1(_dscr(), result)
    assert m["columns"][0]["score"] == 9 and m["columns"][0]["ratio"] == 0.4 and not m["trigger_escalated"]
    assert "principal_reported" not in m["columns"][0] and m["trigger_summary"] == "trigger T1: no adjustment"
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"dscr_t1": result}) == []
    plan = _run(_says("plan_only", refinancings=[PLANNED]), db_session)  # agreed plan only: also resolved
    assert (plan["status"], plan["unresolved"]) == ("plan_only", False)


def test_t1a_needs_undrawn_committed_capacity_at_least_the_amount_maturing_beyond_12_months(db_session):
    small = BACKSTOP.replace("$2.0 billion", "$1.0 billion")
    too_small = _run(_says("adjusted", [{**CP, "backstop": _backstop("$1.0 billion", quote=small)}]), db_session,
                     data=_data(backstop=small))
    assert _outcomes(too_small) == [("R1", "backstop_too_small", 0.0)]  # whole or nothing: no partial removal
    assert (too_small["status"], too_small["principal_removed"]) == ("no_adjustment", 0)
    shared = BACKSTOP.replace("$2.0 billion", "$1.6 billion")
    one_fits = _run(_says("adjusted", [{**CP, "backstop": _backstop("$1.6 billion", quote=shared)},
                                       {**REV, "backstop": _backstop("$1.6 billion", quote=shared)}]),
                    db_session, data=_data(backstop=shared))
    assert _outcomes(one_fits) == [("R1", "remove_from_principal", 1_500e6), ("R2", "backstop_too_small", 0.0)]
    soon = BACKSTOP.replace("June 2029", "June 2026")
    reasons = {}
    for label, backstop, data in (("no date", _backstop(matures=None), None),
                                  ("no amount", _backstop(undrawn=None), None),
                                  ("within 12 months", _backstop(matures="2026-06", quote=soon), _data(backstop=soon))):
        r = _run(_says("adjusted", [{**CP, "backstop": backstop}]), db_session, data=data)
        assert _outcomes(r) == [("R1", "no_backstop", 0.0)]
        reasons[label] = r["agent"]["held_back"][0]["reason"]
    assert reasons == {"no date": "the backstop's maturity date is not given",
                       "no amount": "the backstop's undrawn committed capacity is not given",
                       "within 12 months": "the backstop matures 2026-06, not more than 12 months after the year-end"}


def test_commercial_paper_counts_only_when_year1_principal_includes_short_term_borrowings(db_session):
    current_portion = _run(_says("adjusted", [CP, REV]), db_session,
                           metrics=_dscr(concept="us-gaap:LongTermDebtCurrent"))
    assert _outcomes(current_portion) == [("R1", "not_in_principal", 0.0), ("R2", "remove_from_principal", 500e6)]
    assert any("doesn't include commercial paper" in n for n in current_portion["notes"])
    table = _dscr(concept="Debt maturity table, year 1: us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipal…",
                  principal_basis="maturity_table", principal_includes_short_term=False)
    maturity_table = _run(_says("adjusted", [CP]), db_session, metrics=table)
    assert _outcomes(maturity_table) == [("R1", "not_in_principal", 0.0)]


def test_a_364_day_or_securitisation_facility_stays_in_principal_without_a_committed_term_beyond_12_months(db_session):
    result = _run(_says("no_adjustment", [SEC]), db_session)
    assert _outcomes(result) == [("R3", "no_backstop", 0.0)]
    assert any("securitisation facility maturing within 12 months stays in principal" in n for n in result["notes"])
    # The facility can't be its own backstop: it shows no undrawn committed capacity, and it matures within 12 months.
    itself = _run(_says("adjusted", [{**SEC, "backstop": _quote("D07", SECURITISATION, amount="$400 million",
                                                                date="2026-09")}]), db_session)
    assert itself["agent"]["held_back"][0]["reason"] == "the quote shows no undrawn committed capacity"
    assert itself["principal_removed"] == 0


def test_t1b_debt_already_classified_as_long_term_gets_no_adjustment(db_session):
    reclassified = _run(_says("no_adjustment", refinancings=[
        {**EXECUTED, "reclassified": _quote("D08", RECLASS)}]), db_session)
    assert _outcomes(reclassified, "refinancings") == [("F1", "reclassified", None)]
    assert (reclassified["status"], reclassified["refinancing_relief"]) == ("no_adjustment", 0)
    weak = _run(_says("adjusted", refinancings=[
        {**EXECUTED, "reclassified": _quote("D08", "The senior notes due March 2026 were")}]), db_session)
    assert _outcomes(weak, "refinancings") == [("F1", "executed", None)] and weak["refinancing_relief"] == 2
    assert weak["agent"]["held_back"][0]["reason"] == "the quote doesn't say the debt was classified as long-term"


def test_t1b_a_stated_plan_gets_no_adjustment_and_its_passage_is_cited(db_session):
    result = _run(_says("plan_only", refinancings=[PLANNED]), db_session)
    assert (result["status"], result["refinancing_relief"], result["principal_removed"]) == ("plan_only", 0, 0)
    assert result["reason"] == ("Only a stated plan to refinance year-1 maturities (D10): no adjustment, because a "
                                "plan isn't executed")
    _, m = apply_t1(_dscr(), result)
    assert m["columns"][0]["score"] == 9 and "(D10)" in m["trigger_note"]
    # The agent calls a plan executed: its quote reads as a plan, so it gets no adjustment.
    claimed = _run(_says("adjusted", refinancings=[{**PLANNED, "kind": "executed"}]), db_session)
    assert (claimed["status"], claimed["refinancings"][0]["kind"]) == ("plan_only", "plan")
    assert any("reads as a plan" in n for n in claimed["notes"]) and not claimed["agent"]["status_consistent"]


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    bad_cp = {**CP, "balance": _quote("D02", "Commercial paper | 1,500", amount="1,50")}
    undated = {**REV, "backstop": _backstop(matures="2026-06")}  # the quote says June 2029
    in_year = {**EXECUTED, "item_id": "F3", "event": _quote("D06", IN_YEAR, date="2025-11")}
    not_year1 = {**EXECUTED, "maturity": _quote("D09", "5.25% senior notes due 2031", date="2031")}
    result = _run(_says("adjusted", [bad_cp, undated], [in_year, not_year1]), db_session)
    reasons = [(h["item_id"], h["fact"], h["reason"]) for h in result["agent"]["held_back"]]
    assert reasons == [
        ("R1", "balance", "the amount 1,50 is not in the quote"),
        ("R2", "backstop", "the date 2026-06 is not in the quote"),
        ("F3", "event", "dated on or before the year-end: already in the year-end figures"),
        ("F1", "maturity", "the maturity 2031 is not within the 12 months after the year-end")]
    assert _outcomes(result) == [("R2", "no_backstop", 0.0)]
    assert _outcomes(result, "refinancings") == [("F1", "not_year1", None)]
    assert result["status"] == "no_adjustment"


def test_an_unresolved_trigger_escalates_dscr_whichever_side_is_more_severe(db_session):
    result = _run(_says("no_adjustment", [CP, REV], [EXECUTED]), db_session)  # the agent held the score
    assert result["unresolved"]
    metrics = apply_t1(_dscr(), result)
    assert metrics[1]["trigger_escalated"] and "Unresolved" in metrics[1]["trigger_note"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"dscr_t1": result})
    assert (e["id"], e["measure"], e["engine_score"], e["calculated_score"]) == ("dscr_t1:dscr", "dscr", 3, 9)
    assert e["disagreement"] and e["check"] == "dscr_t1" and "checks" not in e  # one escalation, not merged
    assert "the agent read no adjustment; the verified evidence gives adjusted" in e["reason"]
    # The agent claims an adjustment its evidence doesn't support: unresolved too, the score stays as calculated.
    claimed = _run(_says("adjusted", [{**CP, "backstop": None}]), db_session)
    _, m = apply_t1(_dscr(), claimed)
    assert (claimed["status"], claimed["unresolved"], m["columns"][0]["score"]) == ("no_adjustment", True, 9)
    [e] = escalations_for(PASS, COMPLIANT, [m], None, None, {"dscr_t1": claimed})
    assert e["engine_score"] == 9 and "the agent read adjusted; the verified evidence gives no adjustment" in e["reason"]
    assert escalations_for({"status": "fail", "reason": "x"}, COMPLIANT, metrics[1:], None, None,
                           {"dscr_t1": result})[0]["type"] == "run_decision"
