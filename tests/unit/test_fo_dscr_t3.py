"""FO Trigger Agent, DSCR T3: when it fires (|cash taxes paid − current tax expense| above 5% of CFADS, the DSCR
numerator; agent triggers enabled), the passages it reads (the income tax note, MD&A income taxes, liquidity and cash
flows), the verified evidence, the first-match outcome (a quantified one-off backed out of cash taxes paid, a
settlement only if paid in cash; a structural gap, no adjustment; otherwise the score held with low data confidence)
and the escalation of DSCR only when the trigger is unresolved.

Synthetic 10-K text and a scored DSCR column only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.dscr_t2 import apply_t2
from app.services.fo.dscr_t3 import apply_t3, dscr_t3_trigger, t3_passages
from app.services.fo.measures import current_tax_for
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import measure_card, t3_card
from app.services.fo.runner import escalations_for

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
CASH = "At December 31, 2025, we had $1.2 billion of cash and cash equivalents."
SETTLED_IN_CASH = ("In 2025, we paid $100 million to the IRS in cash to settle the examination of our 2019 to 2021 "
                   "tax years.")
SETTLED = "In 2025, we settled the examination of our 2018 tax year for $60 million."
REFUND = "We received a federal income tax refund of $40 million in 2025 related to prior-year overpayments."
FORECAST = "We expect to pay a one-time transition tax installment of $30 million in 2026."
STRUCTURAL = ("Bonus depreciation and the immediate expensing of research and development costs reduced our cash "
              "taxes in 2025.")
UNQUANTIFIED = "Cash taxes in 2025 also reflect a one-time payment related to a prior-year audit settlement."
PAID_TABLE = ["Income taxes paid (net of refunds)", "$300.0"]
LEASES = "Operating lease payments of $20 million were paid in cash during 2025, net of a landlord refund."


def _data():
    mdna = ["Liquidity and Capital Resources", CASH, SETTLED_IN_CASH, SETTLED, REFUND, FORECAST,
            "Critical Accounting Estimates", "We record a liability for uncertain tax positions and refunds."]
    notes = ["NOTE 9 — INCOME TAXES", STRUCTURAL, UNQUANTIFIED, *PAID_TABLE, "NOTE 10 — LEASES", LEASES]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _dscr(cash_taxes=300e6, current_tax=200e6, cash_flow=1_000e6, debt_service=1_050e6):
    """A scored DSCR: cash flow 1,000 (after cash taxes 300) against 1,050 = 0.95x (7); taxes gap 100 = 10% of CFADS."""
    ratio = cash_flow / debt_service
    forward = {"heading": "Next 12M from 2025-12-31", "period": FY25, "basis": "forward", "score": 7, "ratio": ratio,
               "reason": None, "cash_taxes": cash_taxes, "cash_taxes_concept": "us-gaap:IncomeTaxesPaidNet",
               "current_tax": current_tax, "current_tax_concept": "us-gaap:CurrentIncomeTaxExpenseBenefit",
               "cash_flow": cash_flow, "capex": 300e6, "depreciation": 250e6, "debt_service": debt_service,
               "interest": 150e6, "rent": 100e6, "principal": debt_service - 250e6}
    return [{"key": "overall", "columns": [{"score": 7}]},
            {"key": "dscr", "title": "DSCR", "short": "DSCR", "rows": [], "bands": [],
             "columns": [forward, {**forward, "heading": "FY2025", "basis": "historical"}]}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, one_offs=(), structural=(), unquantified=()):
    return {"dscr_t3": {"status": status, "one_offs": list(one_offs), "structural": list(structural),
                        "unquantified": list(unquantified), "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return dscr_t3_trigger(_data(), metrics or _dscr(), ENABLED, session=db_session,
                           backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


SETTLEMENT = {"item_id": "O1", "kind": "settlement", "description": "IRS examination settled",
              "evidence": _quote("X01", SETTLED_IN_CASH, amount="$100 million"), "paid_in_cash": None}
REFUNDED = {"item_id": "O2", "kind": "refund", "description": "Federal refund",
            "evidence": _quote("X03", REFUND, amount="$40 million"), "paid_in_cash": None}
BONUS = {"reason": "bonus_depreciation", "passage_id": "X05", "quote": STRUCTURAL}


def test_the_agent_reads_the_tax_note_and_mdna():
    t3 = t3_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t3["passages"]] == [
        ("X01", "mdna_tax", SETTLED_IN_CASH), ("X02", "mdna_tax", SETTLED), ("X03", "mdna_tax", REFUND),
        ("X04", "mdna_tax", FORECAST), ("X05", "tax_note", STRUCTURAL), ("X06", "tax_note", UNQUANTIFIED),
        ("X07", "tax_note", " | ".join(PAID_TABLE))]
    assert all(t not in p["text"] for p in t3["passages"] for t in (CASH, LEASES))  # no T3 term; the lease note


def test_a_gap_at_or_below_5pct_of_cfads_or_after_a_going_concern_fail_does_not_fire(db_session):
    at = dscr_t3_trigger(_data(), _dscr(current_tax=250e6), ENABLED)  # 50 = exactly 5%
    assert (at["status"], at["fired"], at["agent"]) == ("not_triggered", False, None)
    assert "(5.0% of CFADS), not more than 5%" in at["reason"]
    untagged = dscr_t3_trigger(_data(), _dscr(current_tax=None), ENABLED)
    assert untagged["reason"] == "No current tax expense tagged: trigger T3 not set off"
    skipped = dscr_t3_trigger(_data(), _dscr(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False) and apply_t3(_dscr(), skipped) == _dscr()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_t3_needs_the_agent_and_without_text_holds_with_low_confidence(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        dscr_t3_trigger(_data(), _dscr(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'dscr_t3' response"):
        dscr_t3_trigger(_data(), _dscr(), ENABLED, backend=scripted_backend({"dscr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = dscr_t3_trigger(nothing, _dscr(), ENABLED)
    assert (result["status"], result["decided_by"], result["data_confidence"], result["unresolved"]) == (
        "low_confidence", "no_text", "low", False)
    assert result["reason"] == "No explanation given for the gap: score held, low data confidence"


def test_a_quantified_one_off_is_backed_out_of_cash_taxes(db_session):
    # A settlement paid in cash: the payment is added back. 300 − 100 = 200 cash taxes; 1,100 / 1,050 = 1.05x (5).
    result = _run(_says("one_off_quantified", [SETTLEMENT], [BONUS]), db_session)
    assert (result["status"], result["backed_out"], result["unresolved"]) == ("one_off_quantified", 100e6, False)
    assert any("comes first" in n for n in result["notes"])  # the structural reason doesn't count
    metrics = _dscr()
    _, m = apply_t3(metrics, result)
    col = m["columns"][0]
    assert (col["cash_taxes_reported"], col["cash_taxes"], col["cash_flow_reported"], col["cash_flow"]) == (
        300e6, 200e6, 1_000e6, 1_100e6)
    assert (col["score"], col["tax_adjustment"]["calculated"], col["tax_adjustment"]["recomputed"]) == (5, 7, 5)
    assert m["columns"][1]["score"] == 7 and metrics[1]["columns"][0]["cash_taxes"] == 300e6  # input untouched
    assert m["trigger_summary"] == "trigger T3: recomputed" and "Resolved" in m["trigger_note"]
    # A refund is subtracted: payments 100 − refund 40 = 60 out of cash taxes.
    both = _run(_says("one_off_quantified", [SETTLEMENT, REFUNDED]), db_session)
    assert both["backed_out"] == 60e6 and [e["effect"] for e in both["one_offs"]] == [100e6, -40e6]
    assert apply_t3(_dscr(), both)[1]["columns"][0]["cash_flow"] == 1_060e6


def test_a_settlement_counts_only_if_paid_in_cash(db_session):
    not_cash = {**SETTLEMENT, "evidence": _quote("X02", SETTLED, amount="$60 million")}
    result = _run(_says("one_off_quantified", [not_cash], [BONUS]), db_session)
    assert result["agent"]["held_back"][0]["reason"] == ("a settlement counts only if the filing says it was paid "
                                                         "in cash")
    assert (result["status"], result["backed_out"], result["unresolved"]) == ("structural", 0.0, True)
    # The cash payment shown in another quote makes it count.
    shown = _run(_says("one_off_quantified", [{**not_cash, "paid_in_cash": _quote("X01", "we paid $100 million to "
                                                                                   "the IRS in cash")}]), db_session)
    assert (shown["status"], shown["backed_out"]) == ("one_off_quantified", 60e6)


def test_a_structural_gap_gets_no_adjustment(db_session):
    result = _run(_says("structural", structural=[BONUS, {**BONUS, "reason": "rd_expensing"}]), db_session)
    assert (result["status"], result["unresolved"]) == ("structural", False)
    assert result["reason"] == "The gap is structural: bonus depreciation (X05), R&D expensing (X05): no adjustment"
    _, m = apply_t3(_dscr(), result)
    assert m["columns"][0]["score"] == 7 and "cash_taxes_reported" not in m["columns"][0]
    assert "data_confidence" not in m and m["trigger_summary"] == "trigger T3: no adjustment"


def test_unquantified_or_unexplained_holds_the_score_with_low_data_confidence(db_session):
    result = _run(_says("low_confidence", unquantified=[_quote("X06", UNQUANTIFIED)]), db_session)
    assert (result["status"], result["data_confidence"], result["unresolved"]) == ("low_confidence", "low", False)
    assert result["reason"] == "A one-off is mentioned but not quantified (X06): score held, low data confidence"
    _, m = apply_t3(_dscr(), result)
    assert m["columns"][0]["score"] == 7 and m["data_confidence"] == "low"
    assert measure_card(m)["data_confidence_note"].startswith("Low data confidence (trigger T3)")
    assert t3_card(result)["label"] == "LOW DATA CONFIDENCE: SCORE HELD"
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"dscr_t3": result}) == []


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    forecast = {**SETTLEMENT, "kind": "one_off_payment", "evidence": _quote("X04", FORECAST, amount="$30 million")}
    wrong_kind = {**REFUNDED, "kind": "settlement", "evidence": _quote("X03", REFUND, amount="$40 million")}
    not_nol = {**BONUS, "reason": "nol"}
    result = _run(_says("one_off_quantified", [forecast, wrong_kind], [not_nol]), db_session)
    assert [(h["item_id"], h["fact"], h["reason"]) for h in result["agent"]["held_back"]] == [
        ("O1", "amount", "the quote reads as a forecast, not the year's cash taxes"),
        ("O2", "amount", "the quote doesn't mention a settlement with a tax authority"),
        ("S1", "structural", "the quote doesn't mention NOLs")]
    # Nothing held up, but one-offs were claimed with amounts: a one-off mentioned, not quantified.
    assert (result["status"], result["unresolved"]) == ("low_confidence", True)
    assert result["reason"] == "A one-off is mentioned but not quantified: score held, low data confidence"


def test_an_unresolved_t3_escalates_dscr_and_merges_with_t2(db_session):
    result = _run(_says("structural", [SETTLEMENT]), db_session)  # the agent says structural; a one-off holds up
    assert (result["status"], result["unresolved"]) == ("one_off_quantified", True)
    metrics = apply_t3(_dscr(), result)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"dscr_t3": result})
    assert (e["id"], e["engine_score"], e["calculated_score"], e["disagreement"]) == ("dscr_t3:dscr", 5, 7, True)
    assert "the agent read structural; the verified evidence gives one off quantified" in e["reason"]
    t2 = {"fired": True, "unresolved": True, "score": 9, "column": "Next 12M from 2025-12-31", "status": "no_growth",
          "reason": "x", "capex": {"multiple": 2.0}, "agent": {"stated_status": "committed_growth"}}
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"dscr_t2": t2, "dscr_t3": result})
    assert (e["checks"], e["calculated_score"]) == (["dscr_t2", "dscr_t3"], 9)  # before either trigger


def test_t2_then_t3_both_recompute_and_the_card_shows_each_stage(db_session):
    t2 = {"fired": True, "unresolved": False, "status": "growth_not_quantified", "maintenance_capex": 250e6,
          "reason": "Growth described but not quantified", "capex": {"multiple": 1.6}}
    metrics = apply_t3(apply_t2(_dscr(), t2), _run(_says("one_off_quantified", [SETTLEMENT]), db_session))
    col = metrics[1]["columns"][0]
    # T2: 1,000 + 50 = 1,050 (1.00x, 5); T3: + 100 = 1,150 (1.10x, 5). Cash flow as tagged stays T2's "before".
    assert (col["cash_flow_reported"], col["cash_flow"], col["capex"], col["cash_taxes"]) == (
        1_000e6, 1_150e6, 250e6, 200e6)
    assert [r["label"] for r in measure_card(metrics[1])["result_rows"]] == [
        "DSCR", "DSCR as calculated (before trigger T2)", "DSCR after trigger T2, before trigger T3",
        "Risk score as calculated (before trigger T2)", "Risk score after trigger T2, before trigger T3",
        "Risk score (after trigger T2 and T3)"]


def test_current_tax_expense_is_the_tagged_total_else_its_parts():
    values = {("us-gaap:CurrentFederalTaxExpenseBenefit", FY25): (150e6, "USD"),
              ("us-gaap:CurrentStateAndLocalTaxExpenseBenefit", FY25): (30e6, "USD"),
              ("us-gaap:CurrentForeignTaxExpenseBenefit", FY25): (5e6, "USD")}
    assert current_tax_for(values, {}, FY25)[1] == 185e6
    values[("us-gaap:CurrentIncomeTaxExpenseBenefit", FY25)] = (190e6, "USD")
    assert current_tax_for(values, {}, FY25)[1] == 190e6
    assert current_tax_for({}, {}, FY25) == (None, None)
