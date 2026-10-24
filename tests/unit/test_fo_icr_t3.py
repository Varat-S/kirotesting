"""FO Trigger Agent, ICR T3: when it fires (ICR's EBIT is operating income, not pre-tax income + interest expense; agent
triggers enabled; ICR not n/a), the passages it reads (the income statement, MD&A, the notes), the verified evidence,
the first-match outcome (pre-tax income found: EBIT = pre-tax income + interest expense, recomputed, resolved; not
found: operating income kept, unresolved, the items between operating income and pre-tax income recorded) and the
escalation of ICR.

Synthetic 10-K text and a scored ICR column only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.icr_t3 import apply_icr_t3, icr_t3_passages, icr_t3_trigger
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import icr_t3_card, measure_card
from app.services.fo.runner import escalations_for

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
STATEMENT = ["Operating income", "400", "Interest expense", "(150)", "Other expense, net", "(80)",
             "Non-service pension cost", "(20)", "Income before income taxes", "150", "Provision for income taxes", "(30)"]
MDNA_PRETAX = "Income before income taxes was $150 million in 2025, compared with $210 million in 2024."
OTHER_NOTE = "Other expense, net of $80 million in 2025 consists mainly of foreign exchange losses."
DISCONTINUED = "Loss from discontinued operations before income taxes was $10 million in 2025."
LEASES = "We lease office space under operating leases expiring through 2032."


def _data(statement=STATEMENT):
    mdna = ["Results of Operations", MDNA_PRETAX, DISCONTINUED]
    fs = ["CONSOLIDATED STATEMENTS OF OPERATIONS", "(In millions)", *statement,
          "NOTE 11 — OTHER EXPENSE", OTHER_NOTE, "NOTE 12 — LEASES", LEASES]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(fs)}]}


def _icr(concept="us-gaap:OperatingIncomeLoss", tagged_expense=150e6, not_applicable=None):
    """A scored ICR: (operating income 400 + rent 50) / (interest 150 + rent 50) = 2.25x (2–3x: 6)."""
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "score": 6, "ratio": 450 / 200,
           "reason": None, "ebit": 400e6, "ebit_concept": concept, "rent": 50e6, "ebitr": 450e6, "interest": 150e6,
           "interest_expense": tagged_expense, "interest_expense_concept": "us-gaap:InterestExpense",
           "fixed_charges": 200e6}
    m = {"key": "icr", "title": "ICR", "short": "ICR", "rows": [], "bands": [],
         "columns": [col, {**col, "heading": "FY2024", "period": "2024-01-01--2024-12-31"}]}
    if not_applicable:
        m["not_applicable"] = not_applicable
    return [{"key": "overall", "columns": [{"score": 7}]}, m]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, pretax=None, interest=None, items=()):
    return {"icr_t3": {"status": status, "pretax_income": pretax, "interest_expense": interest,
                       "items_between": list(items), "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None, data=None):
    return icr_t3_trigger(data or _data(), metrics or _icr(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


PRETAX = _quote("K03", "Income before income taxes | 150", amount="150", is_loss=False)
INTEREST = _quote("K03", "Interest expense | (150)", amount="150")
ITEMS = [{"description": "Other expense, net", "kind": "expense",
          "evidence": _quote("K03", "Other expense, net | (80)", amount="80")},
         {"description": "Non-service pension cost", "kind": "expense",
          "evidence": _quote("K03", "Non-service pension cost | (20)", amount="20")}]


def test_the_agent_reads_the_income_statement_mdna_and_the_notes():
    t = icr_t3_passages(_data())
    assert [(p["passage_id"], p["section"], p["kind"]) for p in t["passages"]] == [
        ("K01", "mdna", "text"), ("K02", "mdna", "text"), ("K03", "income_statement", "table"),
        ("K04", "notes", "text")]
    assert t["passages"][2]["text"] == " | ".join(["(In millions)", *STATEMENT]) and t["passages"][2]["scale"] == 1e6
    assert all(LEASES not in p["text"] for p in t["passages"])


def test_a_standard_ebit_an_icr_that_is_na_or_a_going_concern_fail_does_not_fire(db_session):
    standard = icr_t3_trigger(_data(), _icr(concept="EBIT (pre-tax income + interest)"), ENABLED)
    assert (standard["status"], standard["fired"]) == ("not_triggered", False)
    assert standard["reason"] == ("EBIT is EBIT (pre-tax income + interest), not operating income: trigger ICR T3 not "
                                  "set off")
    na = icr_t3_trigger(_data(), _icr(not_applicable="Net interest is income"), ENABLED)
    assert na["reason"] == "ICR is n/a: trigger ICR T3 not set off"
    skipped = icr_t3_trigger(_data(), _icr(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False) and apply_icr_t3(_icr(), skipped) == _icr()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_operating_income_ebit_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        icr_t3_trigger(_data(), _icr(), ENABLED)
    nothing = _data()
    nothing["document"] = []
    result = icr_t3_trigger(nothing, _icr(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("pretax_not_found", "no_text", True)


def test_pre_tax_income_found_rebuilds_ebit_and_resolves(db_session):
    result = _run(_says("pretax_found", PRETAX, INTEREST, ITEMS), db_session)
    assert (result["status"], result["ebit"], result["unresolved"]) == ("pretax_found", 300e6, False)
    assert [(e["description"], e["amount"]) for e in result["items_between"]] == [
        ("Other expense, net", 80e6), ("Non-service pension cost", 20e6)]
    metrics = _icr()
    _, m = apply_icr_t3(metrics, result)
    col = m["columns"][0]
    # (150 + 150 + 50) / (150 + 50) = 1.75x: 7.5. The interest denominator is unchanged.
    assert (col["ebit_reported"], col["ebit"], col["interest"]) == (400e6, 300e6, 150e6)
    assert col["ratio"] == pytest.approx(1.75) and col["score"] == 7.5 and m["columns"][1]["score"] == 6
    assert col["ebit_concept_reported"] == "us-gaap:OperatingIncomeLoss" and "ICR T3" in col["ebit_concept"]
    assert metrics[1]["columns"][0]["ebit"] == 400e6  # the input is untouched
    assert m["trigger_summary"] == "trigger ICR T3: recomputed" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"icr_t3": result}) == []
    assert [r["label"] for r in measure_card(m)["result_rows"]] == [
        "ICR", "ICR as calculated (before trigger ICR T3)", "Risk score as calculated (before trigger ICR T3)",
        "Risk score (after trigger ICR T3)"]
    assert [q["fact"][:22] for q in icr_t3_card(result)["evidence"]] == [
        "Pre-tax income: 150.0 ", "Interest expense: 150.", "Between operating and ", "Between operating and "]


def test_interest_expense_is_the_agents_figure_else_the_tagged_one(db_session):
    tagged = _run(_says("pretax_found", PRETAX), db_session, metrics=_icr(tagged_expense=140e6))
    assert (tagged["ebit"], tagged["interest_expense"]["source"]) == (290e6, "us-gaap:InterestExpense")
    none = _run(_says("pretax_found", PRETAX), db_session, metrics=_icr(tagged_expense=None))
    assert (none["ebit"], none["unresolved"]) == (None, True)
    assert none["unresolved_why"] == ("pre-tax income was found but no interest expense to add back, so EBIT stays "
                                      "operating income")


def test_pre_tax_income_not_found_keeps_operating_income_and_escalates(db_session):
    result = _run(_says("pretax_not_found", items=ITEMS), db_session)
    assert (result["status"], result["ebit"], result["unresolved"], result["disagreement"]) == (
        "pretax_not_found", None, True, False)
    assert result["reason"] == ("Pre-tax income not found: EBIT stays operating income (400.0 million); items between "
                                "operating income and pre-tax income: Other expense, net (expense 80.0 million, K03), "
                                "Non-service pension cost (expense 20.0 million, K03)")
    metrics = apply_icr_t3(_icr(), result)
    assert metrics[1]["columns"][0]["score"] == 6 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"icr_t3": result})
    assert (e["id"], e["measure"], e["engine_score"]) == ("icr_t3:icr", "icr", 6) and "disagreement" not in e


def test_a_pre_tax_loss_counts_only_when_shown_as_one(db_session):
    loss_statement = [c if c != "Income before income taxes" else "Loss before income taxes" for c in STATEMENT]
    loss_statement[loss_statement.index("150")] = "(30)"
    data = _data(loss_statement)
    loss = _run(_says("pretax_found", _quote("K03", "Loss before income taxes | (30)", amount="30", is_loss=True),
                      INTEREST), db_session, data=data)
    assert (loss["pretax"]["amount"], loss["ebit"]) == (-30e6, 120e6)
    unsigned = _run(_says("pretax_found", _quote("K03", "Loss before income taxes | (30)", amount="(30)",
                                                 is_loss=False), INTEREST), db_session, data=data)
    assert unsigned["agent"]["held_back"][0]["reason"] == ("the amount is in parentheses (a loss), but the agent "
                                                           "didn't call it one")
    assert (unsigned["status"], unsigned["unresolved"]) == ("pretax_not_found", True)


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    discontinued = _quote("K02", DISCONTINUED, amount="$10 million", is_loss=True)
    result = _run(_says("pretax_found", discontinued, INTEREST), db_session)
    assert result["agent"]["held_back"][0]["reason"] == ("the quote is for discontinued operations, not continuing "
                                                         "operations")
    not_pretax = _run(_says("pretax_found", _quote("K04", OTHER_NOTE, amount="$80 million", is_loss=False)),
                      db_session)
    assert not_pretax["agent"]["held_back"][0]["reason"] == "the quote doesn't show income before income taxes"
    assert (not_pretax["status"], not_pretax["disagreement"]) == ("pretax_not_found", True)
