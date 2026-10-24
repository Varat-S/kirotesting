"""FO Trigger Agent, RCF/Debt T1: when it fires (RCF / debt scoring 9 or more, or negative; agent triggers enabled), the
passages it reads (MD&A and the notes), the verified evidence, the outcome (first match: a quantified, non-recurring
one-off cash cost or special dividend is added back; no one-offs found gets no adjustment; a one-off identified but not
quantified is unresolved), the rescoring of RCF / debt and its escalation.

Synthetic 10-K text and a scored RCF measure only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.rcf_triggers import apply_rcf_t1, rcf_t1_passages, rcf_t1_trigger
from app.services.fo.reporting import measure_card, rcf_t1_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_cash_flow

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
RESTRUCT = "We paid $300 million in cash for restructuring and severance in 2025."
SPECIAL = "In 2025, we paid a special cash dividend of $100 million to shareholders."
LITIGATION = "We made payments related to the settlement of litigation in 2025."
PENSION = "Pension contributions were $40 million in 2025."
RECURS = "We make pension contributions every year."
ACCRUAL = "We recorded a restructuring charge of $50 million in 2025."
DECLARED = "On February 10, 2026, our board declared a special dividend of $200 million, payable in March 2026."
OPERATING = "Cash provided by operating activities was $100 million in 2025."
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D": 5.0},
            "benchmarks": {"rcf_to_debt": 0.25}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    mdna = ["Liquidity and Capital Resources", RESTRUCT, SPECIAL, LITIGATION, PENSION, RECURS, ACCRUAL, DECLARED,
            OPERATING]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)}]}


def _col(ffo=100e6, dividends=150e6, nci=0.0, adj_debt=2000e6):
    """FFO 100 − dividends 150 = RCF −50 over debt + op. leases 2,000: −2.5%."""
    rcf = ffo - dividends - nci
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "cfo": ffo + 20e6, "wc_change": 20e6,
           "ffo": ffo, "ffo_basis": "FFO", "dividends": dividends, "nci_distributions": nci, "rcf": rcf,
           "rcf_cfo": rcf + 20e6, "adj_debt": adj_debt, "debt": adj_debt - 300e6,
           "rcf_to_debt": {"ratio": rcf / adj_debt, "reason": None, "no_debt": False}}
    score_cash_flow(col, SETTINGS)
    return col


def _rcf(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "rcf", "title": "RCF to debt", "short": "RCF / Debt", "rows": [], "columns": [_col(**kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D_dso": 3.0}}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _item(item_id, kind, category, pid, quote, amount, recurring=False, recurring_evidence=None):
    return {"item_id": item_id, "kind": kind, "category": category, "description": category,
            "evidence": _quote(pid, quote, amount=amount), "recurring": recurring,
            "recurring_evidence": recurring_evidence}


def _says(status, one_offs=(), unquantified=()):
    return {"rcf_t1": {"status": status, "one_offs": list(one_offs), "unquantified": list(unquantified),
                       "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return rcf_t1_trigger(_data(), metrics or _rcf(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


SEVERANCE = _item("O1", "cash_cost", "restructuring", "F01", RESTRUCT, "$300 million")
DIVIDEND = _item("O2", "special_dividend", "special_dividend", "F02", SPECIAL, "$100 million")
PENSIONS = _item("O3", "cash_cost", "pension", "F04", PENSION, "$40 million", True, _quote("F05", RECURS))
SETTLEMENT = {"item_id": "U1", "kind": "cash_cost", "category": "litigation", "description": "Settlement",
              "evidence": _quote("F03", LITIGATION)}


def test_the_fixture_sets_the_trigger_off():
    col = _col()
    assert col["rcf_to_debt"]["ratio"] == -0.025 and col["rcf_to_debt"]["score"] >= 9


def test_the_agent_reads_mdna_cash_passages():
    t = rcf_t1_passages(_data())
    assert [(p["passage_id"], p["text"]) for p in t["passages"]] == [
        ("F01", RESTRUCT), ("F02", SPECIAL), ("F03", LITIGATION), ("F04", PENSION), ("F05", RECURS),
        ("F06", ACCRUAL), ("F07", DECLARED), ("F08", OPERATING)]


def test_a_good_score_a_going_concern_fail_or_no_score_do_not_fire(db_session):
    good = rcf_t1_trigger(_data(), _rcf(ffo=600e6, dividends=50e6), ENABLED)
    assert (good["status"], good["fired"]) == ("not_triggered", False) and "below 9" in good["reason"]
    skipped = rcf_t1_trigger(_data(), _rcf(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_rcf_t1(_rcf(), skipped) == _rcf()
    unscored = _rcf()
    unscored[1]["columns"][0]["rcf_to_debt"]["score"] = None
    assert rcf_t1_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_negative_ratio_fires_even_below_the_score(db_session):
    metrics = _rcf()
    metrics[1]["columns"][0]["rcf_to_debt"]["score"] = 8.0  # e.g. a lenient benchmark
    result = rcf_t1_trigger({**_data(), "document": []}, metrics, ENABLED)
    assert result["fired"] and result["fired_by"] == "RCF / debt is -2.5% (below zero)"


def test_a_fired_trigger_needs_the_agent_and_without_text_there_are_no_one_offs(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        rcf_t1_trigger(_data(), _rcf(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'rcf_t1' response"):
        rcf_t1_trigger(_data(), _rcf(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    result = rcf_t1_trigger({**_data(), "document": []}, _rcf(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("no_one_offs", "no_text", False)


def test_a_one_off_cash_cost_and_a_special_dividend_are_added_back(db_session):
    result = _run(_says("added_back", [SEVERANCE, DIVIDEND, PENSIONS]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("added_back", False, False)
    assert (result["cash_costs"], result["special_dividends"]) == (300e6, 100e6)  # the recurring pension isn't
    assert [e["item_id"] for e in result["recurring"]] == ["O3"]
    metrics = _rcf()
    m = apply_rcf_t1(metrics, result)[1]
    col = m["columns"][0]
    assert (col["ffo_before_trigger"], col["ffo"], col["dividends_before_trigger"], col["dividends"]) == (
        100e6, 400e6, 150e6, 50e6)
    assert (col["rcf_before_trigger"], col["rcf"]) == (-50e6, 350e6)
    assert col["rcf_to_debt"]["ratio"] == pytest.approx(0.175)
    assert col["rcf_to_debt"]["score"] == _col(ffo=400e6, dividends=50e6)["rcf_to_debt"]["score"] < 9
    assert metrics[1]["columns"][0]["rcf"] == -50e6  # the input is untouched
    assert m["trigger_summary"] == "trigger RCF/Debt T1: one-offs added back" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"rcf_t1": result}) == []
    assert [r["label"] for r in measure_card(m)["result_rows"]][:4] == [
        "RCF / debt", "RCF / debt as calculated (before trigger RCF/Debt T1)",
        "Risk score as calculated (before trigger RCF/Debt T1)", "Risk score (after trigger RCF/Debt T1)"]
    assert rcf_t1_card(result)["label"] == "ADDED BACK: RECOMPUTED"


def test_recurring_items_only_mean_no_one_offs(db_session):
    result = _run(_says("no_one_offs", [PENSIONS]), db_session)
    assert (result["status"], result["unresolved"]) == ("no_one_offs", False)
    assert result["reason"] == "No one-offs found (recurring: F04): no adjustment"
    m = apply_rcf_t1(_rcf(), result)[1]
    assert m["columns"][0]["rcf"] == -50e6 and m["trigger_summary"] == "trigger RCF/Debt T1: no one-offs"


def test_a_one_off_identified_but_not_quantified_is_unresolved(db_session):
    result = _run(_says("unresolved", unquantified=[SETTLEMENT]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"] == "a one-off is identified but not quantified (U1)"
    metrics = apply_rcf_t1(_rcf(), result)
    assert metrics[1]["columns"][0]["rcf"] == -50e6 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"rcf_t1": result})
    assert (e["id"], e["measure"], e["measure_name"]) == ("rcf_t1:rcf", "rcf", "RCF to debt")
    first = _run(_says("added_back", [SEVERANCE], [SETTLEMENT]), db_session)  # a quantified one comes first
    assert (first["status"], first["cash_costs"], first["unresolved"]) == ("added_back", 300e6, False)
    assert any(n.startswith("U1 also identified without a cash amount") for n in first["notes"])


def test_a_charge_or_a_declared_dividend_is_held_back(db_session):
    charge = _item("O1", "cash_cost", "restructuring", "F06", ACCRUAL, "$50 million")
    declared = _item("O2", "special_dividend", "special_dividend", "F07", DECLARED, "$200 million")
    result = _run(_says("added_back", [charge, declared]), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "the quote doesn't show cash paid (a charge or accrual isn't cash)",
        "the quote doesn't show the dividend was paid"]
    assert (result["status"], result["disagreement"], result["unresolved"]) == ("no_one_offs", True, True)


def test_special_dividends_never_exceed_the_dividends_paid(db_session):
    result = _run(_says("added_back", [DIVIDEND]), db_session, _rcf(dividends=60e6))
    assert result["special_dividends"] == 60e6
    assert any("exceed the dividends paid" in n for n in result["notes"])
