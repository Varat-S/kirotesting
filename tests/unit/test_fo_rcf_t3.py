"""FO Trigger Agent, RCF/Debt T3: when it fires (no standard NCI-distributions concept tagged while NCI is material;
agent triggers enabled), the passages it reads (the cash flow statement, the equity statement, the notes), the verified
evidence, the outcome (first match: cash paid; declared, a timing difference; none in the year; lumped, unresolved),
the recomputed RCF / debt and its escalation.

Synthetic 10-K text and a scored RCF measure only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.rcf_t3 import apply_rcf_t3, rcf_t3_passages, rcf_t3_trigger
from app.services.fo.reporting import measure_card, rcf_t3_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_cash_flow

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
CASH_PAID = "Distributions to noncontrolling interest holders were $120 million in 2025."
EQUITY_ROW = "Distributions to noncontrolling interests of $120 million were recorded in the equity statement in 2025."
REDEEMABLE = "Distributions to redeemable noncontrolling interests were $15 million in 2025."
DECLARED = "In 2025, the partnerships declared distributions to noncontrolling partners of $90 million."
COMBINED = "Distributions to and purchases of noncontrolling interests totaled $200 million in 2025."
LUMPED = "Other financing activities include distributions to noncontrolling interests."
NONE = "There were no distributions to noncontrolling interests in 2025."
REVENUE = "Revenue increased 5% in 2025 on higher patient volumes."
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D": 5.0},
            "benchmarks": {"rcf_to_debt": 0.25}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    text = ["Consolidated Statements of Cash Flows", CASH_PAID, "Consolidated Statements of Changes in Equity", EQUITY_ROW,
            "Note 12. Redeemable Noncontrolling Interests", REDEEMABLE, DECLARED, COMBINED, LUMPED, NONE,
            "Note 13. Revenue", REVENUE]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "8", "title": "Financial Statements", "text": "\n".join(text)}]}


def _col(tagged=False, nci_income=100e6, net_income=800e6, nci_equity=300e6, total_equity=2000e6):
    """FFO 700 − dividends 100 − NCI 0 (none tagged) = RCF 600 over debt 2,000: 30%."""
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "cfo": 720e6, "ffo": 700e6, "dividends": 100e6,
           "nci_distributions": 0.0, "rcf": 600e6, "rcf_cfo": 620e6, "rcf_to_debt_cfo": 0.31, "adj_debt": 2000e6,
           "nci_distributions_tagged": tagged, "nci_income": nci_income, "nci_income_concept": "nci income",
           "consolidated_net_income": net_income, "consolidated_net_income_concept": "profit loss",
           "nci_equity": nci_equity, "nci_equity_concept": "minority interest", "total_equity": total_equity,
           "total_equity_concept": "total equity",
           "rcf_to_debt": {"ratio": 0.3, "reason": None, "no_debt": False}}
    score_cash_flow(col, SETTINGS)
    return col


def _rcf(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "rcf", "title": "RCF to debt", "short": "RCF / Debt", "rows": [], "columns": [_col(**kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D_dso": 3.0}}]


def _q(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _dist(item_id, holder, basis, pid, quote, amount):
    return {"item_id": item_id, "holder": holder, "basis": basis, "description": holder,
            "evidence": _q(pid, quote, amount=amount)}


def _says(status, distributions=(), purchases=(), none=(), lumped=()):
    return {"rcf_t3": {"status": status, "distributions": list(distributions), "purchases": list(purchases),
                       "none_evidence": list(none), "lumped": list(lumped), "rationale": "Scripted.",
                       "missing_information": []}}


def _run(response, db_session, metrics=None):
    return rcf_t3_trigger(_data(), metrics or _rcf(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


PAID = _dist("D1", "nci", "cash_paid", "N01", CASH_PAID, "$120 million")
MEZZANINE = _dist("D2", "redeemable_nci", "cash_paid", "N03", REDEEMABLE, "$15 million")


def test_the_agent_reads_the_cash_flow_and_equity_statements_and_the_notes():
    t = rcf_t3_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("N01", "cash_flow", CASH_PAID), ("N02", "equity", EQUITY_ROW), ("N03", "notes", REDEEMABLE),
        ("N04", "notes", DECLARED), ("N05", "notes", COMBINED), ("N06", "notes", LUMPED), ("N07", "notes", NONE)]


def test_it_fires_only_when_untagged_and_material(db_session):
    tagged = rcf_t3_trigger(_data(), _rcf(tagged=True), ENABLED)
    assert (tagged["status"], tagged["fired"]) == ("not_triggered", False) and "standard concept" in tagged["reason"]
    small = rcf_t3_trigger(_data(), _rcf(nci_income=20e6), ENABLED)  # 2.5% of net income
    assert small["fired"] is False and "2.5% of consolidated net income" in small["reason"]
    fired = rcf_t3_trigger({**_data(), "document": []}, _rcf(), ENABLED)
    assert fired["fired_by"] == ("no standard NCI-distributions concept tagged, and the NCI share of net income "
                                 "(100.0 million) is 12.5% of consolidated net income, above 5%")
    loss = rcf_t3_trigger({**_data(), "document": []}, _rcf(net_income=-50e6), ENABLED)  # equity test: 15%
    assert loss["fired"] and "NCI equity (300.0 million) is 15.0% of total equity" in loss["fired_by"]
    deficit = rcf_t3_trigger({**_data(), "document": []}, _rcf(net_income=-50e6, total_equity=-100e6), ENABLED)
    assert deficit["fired"] and "(zero or below)" in deficit["fired_by"]
    skipped = rcf_t3_trigger(_data(), _rcf(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fired_trigger_needs_the_agent_and_without_text_it_is_unresolved(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        rcf_t3_trigger(_data(), _rcf(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'rcf_t3' response"):
        rcf_t3_trigger(_data(), _rcf(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    result = rcf_t3_trigger({**_data(), "document": []}, _rcf(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("unresolved", "no_text", True)


def test_cash_paid_including_redeemable_nci_recomputes_rcf(db_session):
    result = _run(_says("cash_paid", [PAID, MEZZANINE], purchases=[_q("N05", COMBINED)]), db_session)
    assert (result["status"], result["amount"], result["unresolved"]) == ("cash_paid", 135e6, False)
    metrics = _rcf()
    m = apply_rcf_t3(metrics, result)[1]
    col = m["columns"][0]
    assert (col["nci_distributions_before_trigger"], col["nci_distributions"], col["rcf"]) == (0.0, 135e6, 465e6)
    assert col["rcf_to_debt"]["ratio"] == pytest.approx(0.2325) and col["rcf_cfo"] == 485e6
    assert col["nci_distributions_basis"] == "cash paid (trigger RCF/Debt T3)"
    assert metrics[1]["columns"][0]["rcf"] == 600e6  # the input is untouched
    assert m["trigger_summary"] == "trigger RCF/Debt T3: NCI distributions (cash paid)" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"rcf_t3": result}) == []
    card = measure_card(m)
    assert "Distributions to NCI as tagged (before trigger RCF/Debt T3 or T4)" in [r["label"] for r in card["value_rows"]]
    assert "Distributions to NCI: cash paid (trigger RCF/Debt T3)" in card["column_notes"][0]["entries"]
    assert rcf_t3_card(result)["label"] == "CASH PAID: RECOMPUTED"


def test_only_declared_amounts_are_used_and_labelled_a_timing_difference(db_session):
    declared = _dist("D1", "nci", "declared", "N04", DECLARED, "$90 million")
    result = _run(_says("declared", [declared]), db_session)
    assert (result["status"], result["amount"]) == ("declared", 90e6)
    assert "a timing difference" in result["reason"]
    col = apply_rcf_t3(_rcf(), result)[1]["columns"][0]
    assert col["nci_distributions_basis"].startswith("declared, not cash paid: a timing difference")
    assert col["rcf"] == 510e6


def test_a_combined_quote_is_held_back_and_lumped_is_unresolved(db_session):
    combined = _dist("D1", "nci", "cash_paid", "N05", COMBINED, "$200 million")
    result = _run(_says("unresolved", [combined], lumped=[_q("N06", LUMPED)]), db_session)
    assert result["agent"]["held_back"][0]["reason"] == (
        "the quote combines distributions with purchases of NCI ownership (purchases are always excluded)")
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"] == ("distributions are lumped into other financing or with buyouts, and the split "
                                        "isn't disclosed")
    metrics = apply_rcf_t3(_rcf(), result)
    assert metrics[1]["columns"][0]["rcf"] == 600e6 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"rcf_t3": result})
    assert (e["id"], e["measure"]) == ("rcf_t3:rcf", "rcf")


def test_no_distributions_in_the_year_is_zero_correct(db_session):
    result = _run(_says("none_in_year", none=[_q("N07", NONE)]), db_session)
    assert (result["status"], result["unresolved"]) == ("none_in_year", False)
    m = apply_rcf_t3(_rcf(), result)[1]
    assert m["columns"][0]["rcf"] == 600e6 and m["trigger_summary"] == "trigger RCF/Debt T3: no NCI distributions"
    nothing = _run(_says("none_in_year"), db_session)  # no quote shows "none": nothing found
    assert (nothing["status"], nothing["disagreement"]) == ("unresolved", True)
