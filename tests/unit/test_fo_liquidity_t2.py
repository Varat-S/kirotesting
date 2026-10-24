"""FO Trigger Agent, Liquidity T2: when it fires (liquidity coverage or refinancing coverage scoring 9 or more after
Liquidity T1; agent triggers enabled; not after a covenant breach), the passages it reads (subsequent events, the debt
note, MD&A liquidity and any going-concern disclosure), DSCR T1's refinancing record reused, the verified evidence, the
outcome per debt item (first match: already long-term under ASC 470-10-45-14 gets no further adjustment; refinanced or
extended after the year-end moves to its new maturity; repaid from cash takes out the debt and the cash used; repaid
from new borrowing or equity takes out the debt and puts the new debt in at its maturity; only planned is unresolved),
the recomputation of both tests and the escalation.

Synthetic 10-K text and a scored liquidity measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.liquidity_t2 import apply_liquidity_t2, bucket_of, liquidity_t2_passages, liquidity_t2_trigger
from app.services.fo.measures import score_liquidity, set_ladder
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import liquidity_t2_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import COVENANT_BLOCK, adjust_for_covenants

ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
PLAN = "We intend to refinance the 4.5% senior notes due June 2026 before they mature."
NOTES_DUE = "Our 4.5% senior notes of $300 million are due on June 15, 2026, and are shown in current maturities."
TERM_DUE = "Our term loan of $200 million is due on March 31, 2026, and is shown in current maturities."
RECLASS = ("Commercial paper of $100 million was classified as long-term at December 31, 2025 because we have the "
           "intent and ability to refinance it on a long-term basis.")
REFI = ("On February 10, 2026, we issued $300 million of 5.25% senior notes due February 15, 2033 and used the net "
        "proceeds to redeem the 4.5% senior notes due June 2026.")
EXTEND = "On January 20, 2026, we amended our term loan to extend its maturity to March 31, 2029."
CASH = "On February 1, 2026, we repaid the $200 million term loan in full using cash on hand."
EQUITY = "On March 5, 2026, we repaid the term loan with the proceeds of a common stock offering."
GC = "Management concluded that there is no substantial doubt about our ability to continue as a going concern."


def _data(subsequent=(REFI, EXTEND, CASH, EQUITY)):
    mdna = ["Liquidity and Capital Resources", PLAN]
    notes = ["NOTE 7 — LONG-TERM DEBT", NOTES_DUE, TERM_DUE, RECLASS, "NOTE 15 — SUBSEQUENT EVENTS", *subsequent,
             "NOTE 16 — GOING CONCERN", GC]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _col(table=True):
    """Sources 100 cash + 200 CFO against 500 due + 100 CapEx + 50 dividends: coverage 0.46x (score 10).
    Refinancing: 100 cash + 2 × 50 FCF against 500 + 100 + 100 weighted: 0.29x (score 9)."""
    col = {"heading": "Next 12M from 2025-12-31", "as_at": "2025-12-31", "cash": 100e6, "cfo": 200e6,
           "capex": 100e6, "dividends": 50e6, "nci_distributions": 0.0, "debt": 1000e6, "revolver_found": None,
           "revolver_found_source": "Not found"}
    ladder = {"y1": 500e6, "y2": 100e6, "y3": 100e6, "y4": 0.0, "y5": 0.0, "after": 300e6} if table else {"y1": 500e6}
    set_ladder(col, ladder, "Tagged maturity table" if table else None, "Tagged")
    return col


def _liquidity(covenant=None, table=True):
    col = _col(table)
    score_liquidity(col, None, COVENANT_BLOCK if covenant == "breach" else None)
    m = {"key": "liquidity", "title": "Liquidity and debt maturity", "short": "Liquidity",
         "rows": [("cash", "Cash"), ("y1", "Debt due in 12 months")], "columns": [col]}
    if covenant:
        adjust_for_covenants(m, covenant)
    return [{"key": "overall", "columns": [{"score": 5}]}, m]


def _pid(text):
    return next(p["passage_id"] for p in liquidity_t2_passages(_data())["passages"] if p["text"] == text)


def _q(text, **kw):
    return {"passage_id": _pid(text), "quote": text, **kw}


def _item(iid="I1", kind="refinanced", source="not_applicable", dscr_t1_item=None, **kw):
    return {"item_id": iid, "description": "Debt", "dscr_t1_item": dscr_t1_item, "kind": kind, "source": source,
            **{k: kw.get(k) for k in ("reclassified", "event", "original_maturity", "amount", "new_maturity",
                                      "source_evidence", "cash_used")}}


def _says(status, *items):
    return {"liquidity_t2": {"status": status, "items": list(items), "rationale": "Scripted.",
                             "missing_information": []}}


def _run(response, db_session, metrics=None, data=None, dscr_t1=None):
    return liquidity_t2_trigger(data or _data(), metrics or _liquidity(), ENABLED, session=db_session,
                                backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025",
                                dscr_t1=dscr_t1)


NOTES = dict(original_maturity=_q(NOTES_DUE, date="2026-06-15"), amount=_q(NOTES_DUE, amount="$300 million"))
TERM = dict(original_maturity=_q(TERM_DUE, date="2026-03-31"), amount=_q(TERM_DUE, amount="$200 million"))
REFINANCED = _item(event=_q(REFI, date="2026-02-10"), new_maturity=_q(REFI, date="2033-02-15"), **NOTES)


def test_the_agent_reads_subsequent_events_the_debt_note_mdna_liquidity_and_going_concern():
    t = liquidity_t2_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("R01", "mdna_liquidity", PLAN), ("R02", "debt_note", NOTES_DUE), ("R03", "debt_note", TERM_DUE),
        ("R04", "debt_note", RECLASS), ("R05", "subsequent_events", REFI), ("R06", "subsequent_events", EXTEND),
        ("R07", "subsequent_events", CASH), ("R08", "subsequent_events", EQUITY), ("R09", "going_concern", GC)]


def test_buckets_are_years_after_the_year_end():
    from datetime import date

    end = date(2025, 12, 31)
    assert [bucket_of(date(*d), end) for d in ((2025, 12, 31), (2026, 6, 15), (2026, 12, 31), (2027, 1, 1),
                                               (2030, 12, 31), (2033, 2, 15))] == [
        None, "y1", "y1", "y2", "y5", "after"]


def test_scores_below_9_a_covenant_breach_or_a_skip_do_not_fire(db_session):
    metrics = _liquidity()
    col = metrics[1]["columns"][0]
    col.update(coverage_score=7, refinancing_score=5, score=7)
    low = liquidity_t2_trigger(_data(), metrics, ENABLED)
    assert (low["status"], low["fired"]) == ("not_triggered", False) and "coverage 7, refinancing 5" in low["reason"]
    breach = liquidity_t2_trigger(_data(), _liquidity(covenant="breach"), ENABLED)
    assert (breach["status"], breach["fired"]) == ("not_triggered", False) and "covenant breach" in breach["reason"]
    skipped = liquidity_t2_trigger(_data(), _liquidity(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_liquidity_t2(_liquidity(), skipped) == _liquidity()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fired_trigger_needs_the_agent_and_nothing_to_read_needs_none(db_session):
    with pytest.raises(PrecheckAgentError, match="no 'liquidity_t2' response"):
        liquidity_t2_trigger(_data(), _liquidity(), ENABLED, backend=scripted_backend({"icr_t1": {}}),
                             mode="scripted")
    result = liquidity_t2_trigger({**_data(), "document": []}, _liquidity(), ENABLED)
    assert result["fired_by"] == "coverage score 10 and refinancing score 9 (9 or more)"
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("none_found", "no_text", False)


def test_refinanced_after_the_year_end_moves_the_debt_to_its_new_maturity(db_session):
    result = _run(_says("recomputed", REFINANCED), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("recomputed", False, False)
    assert result["adjustments"] == [{"item_id": "I1", "from": "y1", "to": "after", "amount": 300e6,
                                      "cash_used": 0.0}]
    metrics = _liquidity()
    m = apply_liquidity_t2(metrics, result)[1]
    col = m["columns"][0]
    assert (col["y1"], col["after"], col["weighted_maturities"]) == (200e6, 600e6, 400e6)
    assert (col["coverage"], col["refinancing"]) == pytest.approx((300 / 350, 0.5))
    assert (col["coverage_score"], col["refinancing_score"], col["score"]) == (9, 7, 9)
    assert col["maturity_adjustment"]["calculated"] == 10 and metrics[1]["columns"][0]["y1"] == 500e6
    assert m["trigger_summary"] == "trigger Liquidity T2: maturities recomputed" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"liquidity_t2": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert labels[-2:] == ["Section score before trigger Liquidity T2", "Section score (after trigger Liquidity T2)"]
    card = liquidity_t2_card(result)
    assert (card["label"], card["debts"][0]["move"]) == ("MATURITIES RECOMPUTED", "Year 1 → After year 5")


def test_extended_moves_the_debt_too(db_session):
    extended = _item(kind="extended", event=_q(EXTEND, date="2026-01-20"), new_maturity=_q(EXTEND, date="2029-03-31"),
                     **TERM)
    result = _run(_says("recomputed", extended), db_session)
    col = apply_liquidity_t2(_liquidity(), result)[1]["columns"][0]
    assert (col["y1"], col["y4"]) == (300e6, 200e6)


def test_repaid_from_cash_takes_out_the_debt_and_the_cash_used(db_session):
    cash = _item(kind="repaid", source="cash", event=_q(CASH, date="2026-02-01"), source_evidence=_q(CASH), **TERM)
    result = _run(_says("recomputed", cash), db_session)
    assert result["adjustments"] == [{"item_id": "I1", "from": "y1", "to": None, "amount": 200e6,
                                      "cash_used": 200e6}]
    col = apply_liquidity_t2(_liquidity(), result)[1]["columns"][0]
    assert (col["y1"], col["cash"]) == (300e6, 0.0)  # 100 cash − 200 used, never below 0


def test_repaid_from_new_borrowing_or_equity(db_session):
    borrowed = _item(kind="repaid", source="new_debt", event=_q(REFI, date="2026-02-10"),
                     new_maturity=_q(REFI, date="2033-02-15"), source_evidence=_q(REFI), **NOTES)
    result = _run(_says("recomputed", borrowed), db_session)
    assert result["adjustments"][0] == {"item_id": "I1", "from": "y1", "to": "after", "amount": 300e6,
                                        "cash_used": 0.0}
    equity = _item(kind="repaid", source="equity", event=_q(EQUITY, date="2026-03-05"), source_evidence=_q(EQUITY),
                   **TERM)
    result = _run(_says("recomputed", equity), db_session)
    col = apply_liquidity_t2(_liquidity(), result)[1]["columns"][0]
    assert (col["y1"], col["cash"], col["after"]) == (300e6, 100e6, 300e6)
    unsourced = _item(kind="repaid", source="cash", event=_q(EQUITY, date="2026-03-05"), source_evidence=_q(EQUITY),
                      **TERM)
    held = _run(_says("unresolved", unsourced), db_session)
    assert [h["reason"] for h in held["agent"]["held_back"]] == ["the quote doesn't show it was repaid from cash"]
    assert held["unresolved_why"] == "I1: executed, but the source of repayment not verified"


def test_debt_already_classified_as_long_term_gets_no_further_adjustment(db_session):
    already = _item(kind="refinanced", reclassified=_q(RECLASS), event=_q(REFI, date="2026-02-10"),
                    new_maturity=_q(REFI, date="2033-02-15"), **NOTES)
    result = _run(_says("already_long_term", already), db_session)
    assert (result["status"], result["unresolved"], result["adjustments"]) == ("already_long_term", False, [])
    m = apply_liquidity_t2(_liquidity(), result)[1]
    assert m["columns"][0]["score"] == 10 and m["trigger_summary"] == "trigger Liquidity T2: already long-term"


def test_only_planned_is_unresolved(db_session):
    planned = _item(kind="planned", event=_q(PLAN, date="2026"), **NOTES)
    result = _run(_says("unresolved", planned), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"] == "I1: only planned"
    metrics = apply_liquidity_t2(_liquidity(), result)
    assert metrics[1]["columns"][0]["score"] == 10 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"liquidity_t2": result})
    assert (e["id"], e["measure"]) == ("liquidity_t2:liquidity", "liquidity")
    called_executed = _run(_says("recomputed", _item(event=_q(PLAN, date="2026"), **NOTES)), db_session)
    assert (called_executed["status"], called_executed["disagreement"]) == ("unresolved", True)
    assert any("reads as a plan" in n for n in called_executed["notes"])


def test_executed_items_are_applied_even_when_another_is_unresolved(db_session):
    planned = _item("I2", kind="planned", event=_q(PLAN, date="2026"), **TERM)
    result = _run(_says("unresolved", REFINANCED, planned), db_session)
    assert (result["status"], len(result["adjustments"])) == ("unresolved", 1)
    assert apply_liquidity_t2(_liquidity(), result)[1]["columns"][0]["y1"] == 200e6


def test_an_executed_item_without_its_new_maturity_or_dated_before_the_year_end(db_session):
    no_new = _item(event=_q(REFI, date="2026-02-10"), **NOTES)
    result = _run(_says("unresolved", no_new), db_session)
    assert result["unresolved_why"] == "I1: executed, but the new maturity not verified"
    early = _item(event=_q(REFI, date="2025-12-31"), new_maturity=_q(REFI, date="2033-02-15"), **NOTES)
    held = _run(_says("none_found", early), db_session)
    assert held["agent"]["held_back"][0]["reason"] == "the date 2025-12-31 is not in the quote"


def test_dscr_t1s_verified_refinancing_is_reused(db_session):
    t1 = {"fired": True, "refinancings": [{
        "item_id": "F1", "outcome": "executed", "date": "2026-02-10", "maturity": "2026-06-15", "amount": 300e6,
        "event_evidence": {"passage_id": "D05", "quote": REFI, "section": "Subsequent events"},
        "reclassified_evidence": None,
        "maturity_evidence": {"passage_id": "D02", "quote": NOTES_DUE, "section": "Debt note"},
        "amount_evidence": {"passage_id": "D02", "quote": NOTES_DUE, "section": "Debt note"}}]}
    reuse = _item(dscr_t1_item="F1", new_maturity=_q(REFI, date="2033-02-15"))
    result = _run(_says("recomputed", reuse), db_session, dscr_t1=t1)
    assert (result["status"], result["adjustments"][0]["from"], result["adjustments"][0]["amount"]) == (
        "recomputed", "y1", 300e6)
    assert result["dscr_t1"][0]["item_id"] == "F1"
    assert result["agent"]["inputs"]["dscr_t1"][0]["outcome"] == "executed"
    facts = [q["fact"] for q in liquidity_t2_card(result)["evidence"]]
    assert "I1: event (reused from DSCR T1)" in facts and "I1: new maturity" in facts
    unknown = _run(_says("none_found", _item(dscr_t1_item="F9")), db_session, dscr_t1=t1)
    assert any("which T1 didn't verify" in n for n in unknown["notes"])


def test_dscr_t1s_reclassification_is_reused(db_session):
    t1 = {"fired": True, "refinancings": [{
        "item_id": "F1", "outcome": "reclassified", "date": None, "maturity": None, "amount": None,
        "event_evidence": {"passage_id": "D04", "quote": RECLASS, "section": "Debt note"},
        "reclassified_evidence": {"passage_id": "D04", "quote": RECLASS, "section": "Debt note"},
        "maturity_evidence": None, "amount_evidence": None}]}
    result = _run(_says("already_long_term", _item(dscr_t1_item="F1")), db_session, dscr_t1=t1)
    assert (result["status"], result["unresolved"]) == ("already_long_term", False)


def test_without_a_maturity_table_only_year_1_changes(db_session):
    metrics = _liquidity(table=False)
    result = _run(_says("recomputed", REFINANCED), db_session, metrics=metrics)
    m = apply_liquidity_t2(metrics, result)[1]
    col = m["columns"][0]
    assert (col["y1"], col.get("after"), col["refinancing"]) == (200e6, None, None)
    assert "no maturity table tagged" in m["trigger_note"]


def test_a_covenant_relief_is_applied_again(db_session):
    metrics = _liquidity(covenant="relief")
    result = _run(_says("recomputed", REFINANCED), db_session, metrics=metrics)
    col = apply_liquidity_t2(metrics, result)[1]["columns"][0]
    assert (col["score"], col["covenant_adjustment"]["calculated"]) == (10, 9)  # 9 + 1


def test_the_agents_answer_is_recomputed_but_not_applied(db_session):
    result = _run(_says("recomputed", _item(event=_q(PLAN, date="2026"), new_maturity=_q(REFI, date="2033-02-15"),
                                            **NOTES)), db_session)
    assert result["status"] == "unresolved"
    assert result["agent_reading"]["applies"]["adjustments"] == [
        {"item_id": "I1", "from": "y1", "to": "after", "amount": 300e6, "cash_used": 0.0}]
