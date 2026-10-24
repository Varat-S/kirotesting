"""FO Trigger Agent, Debt/EBITDAR T4: when it fires (total debt from a fallback: the tagged total skipped, or finance
leases added; agent triggers enabled), the passages it reads (the balance sheet, the debt note and the lease note), the
verified evidence, the reconciliation code does (the instruments add up to the note's total, which ties to the balance
sheet; finance leases counted once), the recomputed Debt/EBITDAR and the escalation when it doesn't reconcile.

Synthetic 10-K text and a scored Debt/EBITDAR measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.leverage_t4 import apply_leverage_t4, leverage_t4_passages, leverage_t4_trigger
from app.services.fo.measures import total_debt
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import leverage_t4_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_leverage

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
BS = "Long-term debt, including amounts due within one year, was $2,000 million at December 31, 2025."
NOTES = "Senior notes due 2030 had a carrying amount of $1,500 million at December 31, 2025."
TERM = "Term loan borrowings under our credit agreement were $420 million at December 31, 2025."
FL_DEBT = "Finance lease obligations included in long-term debt were $100 million at December 31, 2025."
COSTS = "Unamortized debt issuance costs of $20 million are deducted from the carrying amount of the notes."
TOTAL = "Total debt was $2,000 million at December 31, 2025, including finance lease obligations."
FL_NOTE = "Finance lease liabilities totaled $100 million at December 31, 2025."
REVENUE = "Revenue increased 5% in 2025 on higher patient volumes."
SETTINGS = {"values": {"p": 5.0, "m": 2.0, "delta": 2.5, "cap": 3.0},
            "benchmarks": {"debt_to_ebitdar": 3.0, "net_debt_to_ebitdar": 2.5}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    text = ["Consolidated Balance Sheets", BS, "Note 8. Long-Term Debt", NOTES, TERM, FL_DEBT, COSTS, TOTAL,
            "Note 9. Leases", FL_NOTE, "Note 10. Revenue", REVENUE]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "8", "title": "Financial Statements", "text": "\n".join(text)}]}


def _col(debt=2100e6, borrowings=2000e6, added=100e6, skipped=None, op_leases=300e6, cash=200e6, ebitdar=600e6):
    """Balance-sheet debt lines 2,000 + finance lease lines 100 = 2,100 (a fallback); op. leases 300."""
    adj = debt + op_leases
    fallback = None if added is None and skipped is None else {
        "total_skipped": skipped, "borrowings": borrowings,
        "finance_leases_added": None if added is None else {"concept": "us-gaap:FinanceLeaseLiabilityCurrent + "
                                                                       "us-gaap:FinanceLeaseLiabilityNoncurrent",
                                                            "value": added}}
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "ebitdar": ebitdar, "debt": debt,
           "debt_concept": "us-gaap:LongTermDebtCurrent + us-gaap:LongTermDebtNoncurrent", "debt_fallback": fallback,
           "op_leases": op_leases, "adj_debt": adj, "cash": cash, "adj_net_debt": adj - cash}
    for key, num in (("debt_to_ebitdar", adj), ("net_debt_to_ebitdar", adj - cash)):
        col[key] = {"ratio": num / ebitdar, "negative_ebitda": False, "reason": None}
    score_leverage(col, SETTINGS)
    return col


def _leverage(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "leverage", "title": "Debt to EBITDAR", "short": "Debt / EBITDAR", "rows": [],
             "columns": [_col(**kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "D": 5.0, "D_dso": 3.0}}]


def _amount(pid, quote, amount):
    return {"passage_id": pid, "quote": quote, "amount": amount}


def _inst(item_id, kind, pid, quote, amount):
    return {"item_id": item_id, "kind": kind, "description": kind, "evidence": _amount(pid, quote, amount)}


INSTRUMENTS = [_inst("E1", "borrowing", "B02", NOTES, "$1,500 million"),
               _inst("E2", "borrowing", "B03", TERM, "$420 million"),
               _inst("E3", "finance_lease", "B04", FL_DEBT, "$100 million"),
               _inst("E4", "deduction", "B05", COSTS, "$20 million")]
NOTE_TOTAL = _amount("B06", TOTAL, "$2,000 million")
LEASE_NOTE = _amount("B07", FL_NOTE, "$100 million")


def _says(status, instruments=INSTRUMENTS, total=NOTE_TOTAL, fl=LEASE_NOTE, included=True, evidence=None):
    return {"leverage_t4": {"status": status, "instruments": list(instruments), "note_total": total,
                            "finance_lease_liabilities": fl,
                            "finance_leases_in_total": {"included": included, "evidence": evidence},
                            "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return leverage_t4_trigger(_data(), metrics or _leverage(), ENABLED, session=db_session,
                               backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def test_total_debt_records_the_fallback():
    values = {("us-gaap:LongTermDebtCurrent", "2025-12-31"): (100.0, None),
              ("us-gaap:LongTermDebtNoncurrent", "2025-12-31"): (1900.0, None),
              ("us-gaap:DebtAndCapitalLeaseObligations", "2025-12-31"): (2000.0, None),
              ("us-gaap:FinanceLeaseLiabilityCurrent", "2025-12-31"): (10.0, None),
              ("us-gaap:FinanceLeaseLiabilityNoncurrent", "2025-12-31"): (90.0, None)}
    lines = {"us-gaap:LongTermDebtCurrent", "us-gaap:LongTermDebtNoncurrent", "us-gaap:FinanceLeaseLiabilityCurrent",
             "us-gaap:FinanceLeaseLiabilityNoncurrent"}
    _, debt, fallback = total_debt(values, {}, "2025-12-31", lines)
    assert debt == 2100.0 and fallback["borrowings"] == 2000.0
    assert fallback["total_skipped"]["value"] == 2000.0 and fallback["finance_leases_added"]["value"] == 100.0
    _, debt, fallback = total_debt(values, {}, "2025-12-31", frozenset())  # no lines presented: the total is used
    assert (debt, fallback) == (2000.0, None)


def test_the_agent_reads_the_balance_sheet_debt_and_lease_notes():
    t = leverage_t4_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("B01", "balance_sheet", BS), ("B02", "debt_note", NOTES), ("B03", "debt_note", TERM),
        ("B04", "debt_note", FL_DEBT), ("B05", "debt_note", COSTS), ("B06", "debt_note", TOTAL),
        ("B07", "lease_note", FL_NOTE)]


def test_tagged_debt_a_going_concern_fail_or_no_score_do_not_fire(db_session):
    tagged = leverage_t4_trigger(_data(), _leverage(added=None), ENABLED)
    assert (tagged["status"], tagged["fired"]) == ("not_triggered", False) and "not a fallback" in tagged["reason"]
    skipped = leverage_t4_trigger(_data(), _leverage(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_leverage_t4(_leverage(), skipped) == _leverage()
    unscored = _leverage()
    unscored[1]["columns"][0]["composite"]["score"] = None
    assert leverage_t4_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_skipped_total_fires_only_when_it_differs_from_the_lines():
    ties = _leverage(debt=2000e6, added=None, skipped={"concept": "us-gaap:DebtAndCapitalLeaseObligations",
                                                         "value": 2005e6})  # within 1%
    quiet = leverage_t4_trigger(_data(), ties, ENABLED)
    assert (quiet["status"], quiet["fired"]) == ("not_triggered", False) and "ties to them" in quiet["reason"]
    differs = _leverage(debt=2000e6, added=None, skipped={"concept": "us-gaap:DebtAndCapitalLeaseObligations",
                                                            "value": 2100e6})
    fired = leverage_t4_trigger({**_data(), "document": []}, differs, ENABLED)
    assert fired["fired"] and "which it differs from by more than 1%" in fired["fired_by"]
    both = _leverage(skipped={"concept": "us-gaap:DebtAndCapitalLeaseObligations", "value": 2000e6})
    assert leverage_t4_trigger({**_data(), "document": []}, both, ENABLED)["fired_by"] == (
        "total debt 2,100.0 million from a fallback: finance lease liabilities of 100.0 million added")


def test_a_fired_trigger_needs_the_agent_and_without_text_it_is_unresolved(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        leverage_t4_trigger(_data(), _leverage(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'leverage_t4' response"):
        leverage_t4_trigger(_data(), _leverage(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = leverage_t4_trigger(nothing, _leverage(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("does_not_reconcile", "no_text", True)


def test_finance_leases_counted_twice_are_taken_out(db_session):
    """The debt note's 2,000 already holds the 100 of finance leases the engine added again: debt 2,100 → 2,000."""
    result = _run(_says("reconciles"), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("reconciles", False, False)
    assert (result["instruments_sum"], result["finance_leases_inside"], result["reconciled_debt"]) == (
        2000e6, True, 2000e6)
    metrics = _leverage()
    m = apply_leverage_t4(metrics, result)[1]
    col = m["columns"][0]
    assert (col["debt_before_trigger"], col["debt"], col["adj_debt"], col["adj_net_debt"]) == (
        2100e6, 2000e6, 2300e6, 2100e6)
    assert col["debt_to_ebitdar"]["ratio"] == pytest.approx(2300 / 600)
    assert col["composite"]["score"] == _col(debt=2000e6)["composite"]["score"]
    assert metrics[1]["columns"][0]["debt"] == 2100e6  # the input is untouched
    assert m["trigger_summary"] == "trigger Debt/EBITDAR T4: reconciled" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"leverage_t4": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert "Debt / EBITDAR as calculated (before trigger Debt/EBITDAR T4)" in labels
    assert labels[-1] == "Composite leverage score (after trigger Debt/EBITDAR T4)"
    card = leverage_t4_card(result)
    assert card["label"] == "RECONCILED: RECOMPUTED" and card["reconciled"] == "2,000.0"


def test_finance_leases_outside_the_total_are_added_once(db_session):
    """The combined debt-and-lease total (2,000) was skipped for the balance-sheet debt lines (1,900), which leave the
    finance leases out: the note's 1,900 ties, the lease note's 100 sits outside it, so total debt = 2,000."""
    metrics = _leverage(debt=1900e6, borrowings=1900e6, added=None,
                        skipped={"concept": "us-gaap:DebtAndCapitalLeaseObligations", "value": 2000e6})
    without = [i for i in INSTRUMENTS if i["kind"] != "finance_lease"]
    result = _run(_says("reconciles", without, total=None, included=False), db_session, metrics)
    assert (result["status"], result["finance_leases_inside"], result["reconciled_debt"]) == (
        "reconciles", False, 2000e6)
    assert "+ finance leases 100.0 million" in result["reason"]
    assert apply_leverage_t4(metrics, result)[1]["columns"][0]["debt"] == 2000e6


def test_a_total_that_does_not_tie_is_unresolved(db_session):
    big = _amount("B01", BS, "$2,000 million")  # the balance sheet line, which names debt
    result = _run(_says("does_not_reconcile", INSTRUMENTS[:2], total=big), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("does_not_reconcile", True, False)
    assert result["unresolved_why"] == ("the debt-note instruments (1,920.0 million) don't add up to the note's total "
                                        "(2,000.0 million)")
    metrics = apply_leverage_t4(_leverage(), result)
    assert metrics[1]["columns"][0]["debt"] == 2100e6 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t4": result})
    assert (e["id"], e["measure"]) == ("leverage_t4:leverage", "leverage") and "disagreement" not in e
    off = _run(_says("reconciles", [], total=_amount("B06", TOTAL, "$2,000 million")),
               db_session, _leverage(debt=2600e6, borrowings=2500e6))
    assert off["reason"].startswith("Unresolved: the debt note's total (2,000.0 million) doesn't tie to the balance "
                                    "sheet's debt (2,500.0 million)")
    assert (off["status"], off["disagreement"]) == ("does_not_reconcile", True)
    assert leverage_t4_card(off)["label"] == "DOESN'T RECONCILE: UNRESOLVED"


def test_amounts_that_do_not_hold_up_are_held_back(db_session):
    wrong = [_inst("E1", "borrowing", "B02", NOTES, "$1,600 million"),
             _inst("E2", "finance_lease", "B03", TERM, "$420 million")]
    result = _run(_says("does_not_reconcile", wrong, total=None, fl=None, included=False), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "the amount $1,600 million is not in the quote", "the quote doesn't mention finance leases"]
    assert result["unresolved_why"] == "the debt note's total debt wasn't found (no verified total or instrument amounts)"
