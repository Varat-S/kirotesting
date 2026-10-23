"""FO Trigger Agent, EBITDAR Margin T1: when it fires (Debt/EBITDAR T1 removed one-offs from the margin's year; agent
triggers enabled), the passages it reads, where each removed item was booked (verified), the outcome (in revenue:
revenue adjusted too; outside revenue: T1's EBITDAR only; unclear: unresolved), the rescored margin, its escalation,
and the margin-only cash-lease-payments fallback.

Synthetic 10-K text and a scored margin measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.margin_triggers import apply_margin_t1, margin_t1_passages, margin_t1_trigger
from app.services.fo.measures import PRETAX_CONCEPTS, build_financial_overview
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import margin_t1_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_margin

FY25, FY24 = "2025-01-01--2025-12-31", "2024-01-01--2024-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
IN_REVENUE = "Revenues in 2025 include $50 million from a one-time settlement with a payer."
IN_OPERATING = "The goodwill impairment charge of $30 million is included in operating expenses."
BELOW = "The gain on the settlement is included in other income, net."
VAGUE = "We recognized the litigation settlement in 2025 as described in Note 9."
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D": 5.0},
            "benchmarks": {"ebitdar_margin": 0.12}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    text = ["Consolidated Statements of Operations", IN_REVENUE, IN_OPERATING, "Note 9. Other Income", BELOW, VAGUE]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "8", "title": "Financial Statements", "text": "\n".join(text)}]}


def _col(ebitdar=500e6, revenue=5000e6, period=FY25):
    col = {"heading": "FY2025", "period": period, "basis": "historical", "ebitdar": ebitdar, "revenue": revenue,
           "ratio": ebitdar / revenue, "reason": None}
    score_margin(col, SETTINGS)
    return col


def _margin(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "margin", "title": "EBITDAR margin", "short": "EBITDAR margin", "rows": [], "columns": [_col(**kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D_dso": 3.0}}]


def _t1(status="normalised", period=FY25):
    """Debt/EBITDAR T1 removed a 50 gain (EBITDAR −50) and added back a 30 charge (+30): normalisation −20."""
    one_offs = [{"item_id": "O1", "kind": "gain", "category": "litigation", "description": "Settlement",
                 "amount": 50e6, "effect": -50e6, "evidence": {"passage_id": "L01", "quote": "q1"}},
                {"item_id": "O2", "kind": "charge", "category": "impairment", "description": "Impairment",
                 "amount": 30e6, "effect": 30e6, "evidence": {"passage_id": "L02", "quote": "q2"}}]
    return {"fired": True, "status": status, "one_offs": one_offs if status == "normalised" else [],
            "normalisation": -20e6 if status == "normalised" else 0.0, "column": "FY2025",
            "inputs": {"period": period}}


def _q(pid, quote):
    return {"passage_id": pid, "quote": quote}


def _says(status, *locations):
    return {"margin_t1": {"status": status, "locations": list(locations), "rationale": "Scripted.",
                          "missing_information": []}}


def _place(item_id, location, evidence=None):
    return {"item_id": item_id, "location": location, "evidence": evidence}


def _run(response, db_session, metrics=None, t1=None):
    return margin_t1_trigger(_data(), metrics or _margin(), ENABLED, session=db_session,
                             backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025",
                             leverage_t1=t1 or _t1())


def test_the_agent_reads_the_income_statement_and_the_notes():
    t = margin_t1_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("E01", "income_statement", IN_REVENUE), ("E02", "income_statement", IN_OPERATING), ("E03", "notes", BELOW),
        ("E04", "notes", VAGUE)]


def test_it_fires_only_when_debt_ebitdar_t1_removed_one_offs_from_the_margins_year(db_session):
    for t1, why in ((None, "removed no one-off items"), (_t1("operational"), "removed no one-off items"),
                    (_t1(period=FY24), "not the margin's FY2025")):
        result = margin_t1_trigger(_data(), _margin(), ENABLED, leverage_t1=t1)
        assert (result["status"], result["fired"]) == ("not_triggered", False) and why in result["reason"]
    skipped = margin_t1_trigger(_data(), _margin(), SKIPPED, leverage_t1=_t1())
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        margin_t1_trigger(_data(), _margin(), ENABLED, leverage_t1=_t1())
    no_text = margin_t1_trigger({**_data(), "document": []}, _margin(), ENABLED, leverage_t1=_t1())
    assert (no_text["status"], no_text["decided_by"], no_text["unresolved"]) == ("unresolved", "no_text", True)
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_one_off_booked_in_revenue_comes_out_of_revenue_too(db_session):
    result = _run(_says("revenue_adjusted", _place("O1", "revenue", _q("E01", IN_REVENUE)),
                        _place("O2", "other_operating_income", _q("E02", IN_OPERATING))), db_session)
    assert (result["status"], result["revenue_adjustment"], result["unresolved"]) == ("revenue_adjusted", -50e6, False)
    metrics = _margin()
    m = apply_margin_t1(metrics, result)[1]
    col = m["columns"][0]
    assert (col["ebitdar_before_trigger"], col["ebitdar"]) == (500e6, 480e6)  # T1's normalisation: −50 + 30
    assert (col["revenue_before_trigger"], col["revenue"]) == (5000e6, 4950e6)  # the gain was in revenue
    assert col["ratio"] == pytest.approx(480 / 4950)
    assert col["score"] == _col(480e6, 4950e6)["score"]
    assert metrics[1]["columns"][0]["revenue"] == 5000e6  # the input is untouched
    assert m["trigger_summary"] == "trigger EBITDAR Margin T1: revenue adjusted" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"margin_t1": result}) == []
    card = measure_card(m)
    assert [r["label"] for r in card["result_rows"]][:4] == [
        "EBITDAR margin", "EBITDAR margin as calculated (before trigger EBITDAR Margin T1)",
        "Risk score as calculated (before trigger EBITDAR Margin T1)", "Risk score (after trigger EBITDAR Margin T1)"]
    assert "Net operating revenue as reported (before trigger EBITDAR Margin T1)" in [
        r["label"] for r in card["value_rows"]]
    assert margin_t1_card(result)["label"] == "REVENUE ADJUSTED: RECOMPUTED"


def test_booked_outside_revenue_the_margin_takes_t1s_ebitdar_only(db_session):
    result = _run(_says("outside_revenue", _place("O1", "below_operating_income", _q("E03", BELOW)),
                        _place("O2", "other_operating_income", _q("E02", IN_OPERATING))), db_session)
    assert (result["status"], result["revenue_adjustment"]) == ("outside_revenue", 0.0)
    col = apply_margin_t1(_margin(), result)[1]["columns"][0]
    assert (col["ebitdar"], col["revenue"]) == (480e6, 5000e6)


def test_an_unclear_location_is_unresolved_and_escalates_the_margin(db_session):
    result = _run(_says("unresolved", _place("O1", "below_operating_income", _q("E03", BELOW))), db_session)  # O2?
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"] == "where an item was booked isn't clear (O2)"
    metrics = apply_margin_t1(_margin(), result)
    assert metrics[1]["columns"][0]["ebitdar"] == 480e6 and metrics[1]["trigger_escalated"]  # EBITDAR still corrected
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"margin_t1": result})
    assert (e["id"], e["measure"], e["measure_name"]) == ("margin_t1:margin", "margin", "EBITDAR margin")
    claim = _run(_says("revenue_adjusted", _place("O1", "revenue", _q("E04", VAGUE)),
                       _place("O2", "other_operating_income", _q("E02", IN_OPERATING))), db_session)
    assert claim["agent"]["held_back"][0]["reason"] == "the quote doesn't place it in revenue"
    assert (claim["status"], claim["disagreement"]) == ("unresolved", True)


def test_the_margin_alone_falls_back_to_cash_lease_payments():
    def fact(concept, period, value):
        return {"concept": concept, "period_key": period, "value": value, "dimensions": {}, "type": "numeric",
                "unit": "USD"}

    facts = [fact(PRETAX_CONCEPTS[0], FY25, 900), fact("us-gaap:InterestExpense", FY25, 100),
             fact("us-gaap:DepreciationDepletionAndAmortization", FY25, 100), fact("us-gaap:Revenues", FY25, 5000),
             fact("us-gaap:OperatingLeaseLiability", "2025-12-31", 200),
             fact("us-gaap:OperatingLeasePayments", FY25, 30),
             fact("us-gaap:DebtLongtermAndShorttermCombinedAmount", "2025-12-31", 1000)]
    by = {m["key"]: m for m in build_financial_overview({"facts": facts, "labels": {}, "sections": [],
                                                         "document": [], "working_capital": {}})}
    margin, leverage = by["margin"]["columns"][0], by["leverage"]["columns"][0]
    assert (margin["rent"], margin["ebitdar"], margin["rent_basis"]) == (
        30, 1130, "cash lease payments: lease-cost table missing")
    assert margin["ratio"] == pytest.approx(1130 / 5000)
    assert (leverage["rent"], leverage["ebitdar"]) == (None, None)  # Debt/EBITDAR keeps the strict rule
