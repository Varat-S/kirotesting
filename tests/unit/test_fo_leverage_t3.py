"""FO Trigger Agent, Debt/EBITDAR T3: when it fires (the composite leverage score with leases 2 or more worse than
without; agent triggers enabled), the passages it reads (MD&A and the notes), the verified evidence, the outcome (a master
lease, cross-default or parent guarantee disclosed: +1; none disclosed: unresolved; escalators and cancellable leases get
no adjustment), the composite leverage score and the escalation of Debt/EBITDAR.

Synthetic 10-K text and a scored Debt/EBITDAR measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.leverage_t2 import apply_leverage_t2, leverage_t2_trigger
from app.services.fo.leverage_t3 import apply_leverage_t3, leverage_t3_passages, leverage_t3_trigger, without_leases
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import leverage_t3_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_leverage

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
MASTER = ("We lease 42 of our hospitals from Medical Properties Trust, a real estate investment trust, under a master "
          "lease agreement that expires in 2034.")
CROSS = "A default under any lease in the master lease is a cross-default under each of the other leases."
GUARANTEE = "The parent company has guaranteed the obligations of its subsidiaries under these leases."
ESCALATOR = "Rent under the leases escalates annually by the lesser of 2.5% or the increase in the CPI."
CANCEL = "Certain of our equipment leases can be cancelled on 90 days' notice without penalty."
TERM = "The weighted-average remaining lease term of our operating leases is 11.2 years."
DEBT_GUARANTEE = "Our senior notes are guaranteed by our domestic subsidiaries."
REVENUE = "Revenue increased 5% in 2025 on higher patient volumes."
SETTINGS = {"values": {"p": 5.0, "m": 2.0, "delta": 2.5, "cap": 3.0},
            "benchmarks": {"debt_to_ebitdar": 3.0, "net_debt_to_ebitdar": 2.5}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    notes = ["Note 9. Leases", MASTER, CROSS, GUARANTEE, ESCALATOR, CANCEL, TERM, DEBT_GUARANTEE, REVENUE]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _col(debt=1000e6, op_leases=1500e6, rent=150e6, cash=100e6, ebitdar=500e6):
    """Debt 1,000 + op. leases 1,500 over EBITDAR 500: 5.0x (net 4.8x), composite 7.4; without leases debt 1,000 over
    EBITDA 350: 2.9x (net 2.6x), composite 5.1."""
    adj = debt + op_leases
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "ebitdar": ebitdar, "debt": debt,
           "op_leases": op_leases, "op_leases_concept": "us-gaap:OperatingLeaseLiability", "rent": rent,
           "rent_concept": "us-gaap:OperatingLeaseCost", "adj_debt": adj, "cash": cash,
           "cash_concept": "us-gaap:CashAndCashEquivalentsAtCarryingValue", "adj_net_debt": adj - cash,
           "one_off_charges": 0.0, "one_off_gains": 0.0, "net_one_offs": 0.0, "net_one_offs_concept": "none tagged"}
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


def _says(status, items=()):
    return {"leverage_t3": {"status": status, "items": list(items), "rationale": "Scripted.",
                            "missing_information": []}}


def _run(response, db_session, metrics=None):
    return leverage_t3_trigger(_data(), metrics or _leverage(), ENABLED, session=db_session,
                               backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def _item(item_id, kind, pid, quote, counterparty=None):
    return {"item_id": item_id, "kind": kind, "counterparty": counterparty, "description": kind,
            "evidence": {"passage_id": pid, "quote": quote}}


MPT = _item("E1", "master_lease", "R01", MASTER, "Medical Properties Trust")
XDEFAULT = _item("E2", "cross_default", "R02", CROSS)
PARENT = _item("E3", "parent_guarantee", "R03", GUARANTEE)
ESCALATES = _item("E4", "escalator", "R04", ESCALATOR)
CANCELLABLE = _item("E5", "cancellable", "R05", CANCEL)
REMAINING = _item("E6", "term", "R06", TERM)


def test_the_fixture_sets_the_trigger_off():
    col = _col()
    assert col["composite"]["score"] - without_leases(col, SETTINGS)["composite"]["score"] >= 2
    w = without_leases(col, SETTINGS)
    assert (w["ebitda"], w["debt"], w["net_debt"]) == (350e6, 1000e6, 900e6)
    assert w["debt_to_ebitdar"]["ratio"] == pytest.approx(1000 / 350)


def test_the_agent_reads_lease_passages():
    t = leverage_t3_passages(_data())
    assert [(p["passage_id"], p["text"]) for p in t["passages"]] == [
        ("R01", MASTER), ("R02", CROSS), ("R03", GUARANTEE), ("R04", ESCALATOR), ("R05", CANCEL), ("R06", TERM)]


def test_light_leases_a_going_concern_fail_or_no_score_do_not_fire(db_session):
    light = leverage_t3_trigger(_data(), _leverage(op_leases=200e6, rent=20e6), ENABLED)
    assert (light["status"], light["fired"]) == ("not_triggered", False)
    assert "less than 2" in light["reason"]
    skipped = leverage_t3_trigger(_data(), _leverage(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_leverage_t3(_leverage(), skipped) == _leverage()
    unscored = _leverage()
    unscored[1]["columns"][0]["composite"]["score"] = None
    assert leverage_t3_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fired_trigger_needs_the_agent_and_without_text_it_is_unresolved(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        leverage_t3_trigger(_data(), _leverage(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'leverage_t3' response"):
        leverage_t3_trigger(_data(), _leverage(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = leverage_t3_trigger(nothing, _leverage(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("not_disclosed", "no_text", True)


def test_a_master_lease_adds_one_to_the_composite(db_session):
    result = _run(_says("disclosed", [MPT, XDEFAULT, ESCALATES]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("disclosed", False, False)
    assert [e["kind"] for e in result["debt_like"]] == ["master_lease", "cross_default"]
    assert [e["kind"] for e in result["other"]] == ["escalator"]
    assert result["inputs"]["score"] == 7.4 and result["inputs"]["score_without"] == 5.1
    assert result["reason"].startswith("Debt-like leases disclosed (master lease with Medical Properties Trust (R01)")
    metrics = _leverage()
    m = apply_leverage_t3(metrics, result)[1]
    col = m["columns"][0]
    assert col["composite"]["score"] == 8.4 and col["lease_adjustment"]["calculated"] == 7.4
    assert col["debt_to_ebitdar"]["score"] == _col()["debt_to_ebitdar"]["score"]  # the ratios' scores don't change
    assert metrics[1]["columns"][0]["composite"]["score"] == 7.4  # the input is untouched
    assert m["trigger_summary"] == "trigger Debt/EBITDAR T3: +1, debt-like leases disclosed"
    assert not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"leverage_t3": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert labels[-3:] == ["Composite leverage score without leases (trigger Debt/EBITDAR T3)",
                           "Composite leverage score as calculated (before trigger Debt/EBITDAR T3)",
                           "Composite leverage score (after trigger Debt/EBITDAR T3)"]
    card = leverage_t3_card(result)
    assert card["label"] == "DISCLOSED: +1"
    assert card["evidence"][0]["fact"] == "Master lease with Medical Properties Trust: +1"
    assert card["evidence"][-1]["fact"] == "Rent escalator: no lease adjustment"


def test_the_uplift_is_capped_at_ten(db_session):
    metrics = _leverage()
    metrics[1]["columns"][0]["composite"]["score"] = 9.6
    result = _run(_says("disclosed", [PARENT]), db_session, metrics)
    assert apply_leverage_t3(metrics, result)[1]["columns"][0]["composite"]["score"] == 10


def test_escalators_cancellable_leases_and_terms_alone_are_not_disclosed(db_session):
    result = _run(_says("not_disclosed", [ESCALATES, CANCELLABLE, REMAINING]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("not_disclosed", True, False)
    assert result["unresolved_why"] == ("no master lease, cross-default or parent guarantee is disclosed (only "
                                        "cancellable lease, lease term, rent escalator, which get no lease adjustment)")
    metrics = apply_leverage_t3(_leverage(), result)
    assert metrics[1]["columns"][0]["composite"]["score"] == 7.4 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t3": result})
    assert (e["id"], e["measure"]) == ("leverage_t3:leverage", "leverage") and "disagreement" not in e
    assert leverage_t3_card(result)["label"] == "NOT DISCLOSED: UNRESOLVED"


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    debt = _item("E1", "parent_guarantee", "R03", "guaranteed the obligations")  # not about a lease
    wrong = _item("E2", "master_lease", "R04", ESCALATOR)  # no master lease in the quote
    result = _run(_says("disclosed", [debt, wrong]), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "the quote isn't about a lease", "the quote doesn't mention a master lease"]
    assert (result["status"], result["disagreement"], result["unresolved"]) == ("not_disclosed", True, True)
    assert result["unresolved_why"] == "the agent's answer and the verified evidence disagree"


def test_t2_then_t3_share_one_leverage_decision(db_session):
    """T2 (no cash text: freely available) leaves cash; T3 is unresolved: only T3 escalates, its rows shown."""
    metrics = _leverage(cash=1400e6)
    t2 = leverage_t2_trigger({**_data(), "document": []}, metrics, ENABLED)
    metrics = apply_leverage_t2(metrics, t2)
    t3 = leverage_t3_trigger({**_data(), "document": []}, metrics, ENABLED)
    assert t3["fired"] and t3["status"] == "not_disclosed"
    metrics = apply_leverage_t3(metrics, t3)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t2": t2, "leverage_t3": t3})
    assert (t2["fired"], t2["unresolved"]) == (True, False) and e["id"] == "leverage_t3:leverage"
    labels = [r["label"] for r in measure_card(metrics[1])["result_rows"]]
    assert labels[-1].startswith("Composite leverage score (after trigger") and labels[-1].endswith("Debt/EBITDAR T3)")
