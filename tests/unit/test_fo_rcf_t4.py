"""FO Trigger Agent, RCF/Debt T4: when it fires (NCI distributions from PaymentsToMinorityShareholders; agent triggers
enabled), the passages it reads, the verified evidence, the outcome (buyouts quantified: excluded; mixed in but not
quantified: unresolved; no purchases of NCI shown: distributions only), the recomputed RCF / debt and its escalation.

Synthetic 10-K text and a scored RCF measure only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.rcf_t3 import rcf_t3_trigger
from app.services.fo.rcf_t4 import RCF_T4_CONCEPT, apply_rcf_t4, rcf_t4_passages, rcf_t4_trigger
from app.services.fo.reporting import measure_card, rcf_t4_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_cash_flow

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
CASH_LINE = "Distributions to and purchases of noncontrolling interests were $150 million in 2025."
BUYOUT = "In 2025, we purchased additional ownership interests from noncontrolling partners for $40 million."
UNQUANTIFIED = "We also bought out certain physician partners' noncontrolling interests during 2025."
NO_PURCHASES = "There were no purchases of noncontrolling interests in 2025."
DISTRIBUTIONS = "Distributions to noncontrolling interests were $110 million in 2025."
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D": 5.0},
            "benchmarks": {"rcf_to_debt": 0.25}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    text = ["Consolidated Statements of Cash Flows", CASH_LINE, "Consolidated Statements of Changes in Equity",
            DISTRIBUTIONS, "Note 14. Noncontrolling Interests", BUYOUT, UNQUANTIFIED, NO_PURCHASES]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "8", "title": "Financial Statements", "text": "\n".join(text)}]}


def _col(concept=RCF_T4_CONCEPT, nci=150e6):
    """FFO 750 − dividends 100 − NCI 150 (as tagged) = RCF 500 over debt 2,000: 25%."""
    rcf = 750e6 - 100e6 - nci
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "cfo": 770e6, "ffo": 750e6, "dividends": 100e6,
           "nci_distributions": nci, "nci_distributions_concept": concept, "nci_distributions_tagged": True,
           "rcf": rcf, "rcf_cfo": rcf + 20e6, "rcf_to_debt_cfo": (rcf + 20e6) / 2000e6, "adj_debt": 2000e6,
           "nci_income": 100e6, "consolidated_net_income": 800e6, "nci_equity": 300e6, "total_equity": 2000e6,
           "rcf_to_debt": {"ratio": rcf / 2000e6, "reason": None, "no_debt": False}}
    score_cash_flow(col, SETTINGS)
    return col


def _rcf(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "rcf", "title": "RCF to debt", "short": "RCF / Debt", "rows": [], "columns": [_col(**kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D_dso": 3.0}}]


def _q(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _buyout(item_id, pid, quote, amount=None):
    return {"item_id": item_id, "description": "buyout", "evidence": _q(pid, quote, amount=amount)}


def _says(status, buyouts=(), none=()):
    return {"rcf_t4": {"status": status, "buyouts": list(buyouts), "no_purchase_evidence": list(none),
                       "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return rcf_t4_trigger(_data(), metrics or _rcf(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


PARTNERS = _buyout("B1", "M03", BUYOUT, "$40 million")


def test_the_agent_reads_the_statements_and_the_nci_note():
    t = rcf_t4_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("M01", "cash_flow", CASH_LINE), ("M02", "equity", DISTRIBUTIONS), ("M03", "notes", BUYOUT),
        ("M04", "notes", UNQUANTIFIED), ("M05", "notes", NO_PURCHASES)]


def test_it_fires_only_on_payments_to_minority_shareholders_and_never_with_t3(db_session):
    other = rcf_t4_trigger(_data(), _rcf(concept="us-gaap:PaymentsOfDividendsMinorityInterest"), ENABLED)
    assert (other["status"], other["fired"]) == ("not_triggered", False)
    assert "not us-gaap:PaymentsToMinorityShareholders" in other["reason"]
    fired = rcf_t4_trigger({**_data(), "document": []}, _rcf(), ENABLED)
    assert fired["fired_by"] == "distributions to NCI of 150.0 million come from us-gaap:PaymentsToMinorityShareholders"
    assert rcf_t3_trigger(_data(), _rcf(), ENABLED)["fired"] is False  # T3 needs the standard concepts absent
    skipped = rcf_t4_trigger(_data(), _rcf(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert db_session.scalars(select(ModelRun)).first() is None


def test_the_tags_settle_distributions_only_without_the_agent(db_session):
    """The equity statement's distributions (160) cover the cash tagged (150), and no purchase is tagged: no model
    call, distributions only."""
    covered = _rcf()
    covered[1]["columns"][0].update(nci_equity_distributions=160e6, nci_equity_distributions_concept="eq dist",
                                    nci_purchases_tagged=[])
    result = rcf_t4_trigger(_data(), covered, ENABLED)  # no backend: the agent isn't needed
    assert (result["fired"], result["status"], result["decided_by"], result["unresolved"]) == (
        True, "distributions_only", "tags", False)
    assert result["reason"].startswith("The equity statement's distributions to NCI (160.0 million, eq dist)")
    assert apply_rcf_t4(covered, result)[1]["columns"][0]["rcf"] == 500e6
    assert rcf_t4_card(result)["decided_by"].startswith("Code: the equity statement's distributions")
    assert db_session.scalars(select(ModelRun)).first() is None
    for change in ({"nci_equity_distributions": 120e6}, {"nci_purchases_tagged": ["us-gaap:MinorityInterestDecrease"
                                                                                   "FromRedemptions"]}):
        room = _rcf()
        room[1]["columns"][0].update({"nci_equity_distributions": 160e6, "nci_purchases_tagged": [], **change})
        with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):  # room for buyouts: the agent reads
            rcf_t4_trigger(_data(), room, ENABLED)


def test_a_fired_trigger_needs_the_agent_and_without_text_it_is_unresolved(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        rcf_t4_trigger(_data(), _rcf(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'rcf_t4' response"):
        rcf_t4_trigger(_data(), _rcf(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    result = rcf_t4_trigger({**_data(), "document": []}, _rcf(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("unresolved", "no_text", True)


def test_quantified_buyouts_are_excluded(db_session):
    result = _run(_says("buyouts_excluded", [PARTNERS]), db_session)
    assert (result["status"], result["excluded"], result["unresolved"]) == ("buyouts_excluded", 40e6, False)
    metrics = _rcf()
    m = apply_rcf_t4(metrics, result)[1]
    col = m["columns"][0]
    assert (col["nci_distributions_before_trigger"], col["nci_distributions"], col["rcf"]) == (150e6, 110e6, 540e6)
    assert col["rcf_to_debt"]["ratio"] == pytest.approx(0.27) and col["rcf_cfo"] == 560e6
    assert col["nci_distributions_basis"].startswith("distributions only: buyouts of 40.0 million excluded")
    assert metrics[1]["columns"][0]["rcf"] == 500e6  # the input is untouched
    assert m["trigger_summary"] == "trigger RCF/Debt T4: buyouts excluded" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"rcf_t4": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert "RCF / debt as calculated (before trigger RCF/Debt T4)" in labels
    assert rcf_t4_card(result)["label"] == "BUYOUTS EXCLUDED: RECOMPUTED"


def test_no_purchases_means_distributions_only(db_session):
    result = _run(_says("distributions_only", none=[_q("M05", NO_PURCHASES)]), db_session)
    assert (result["status"], result["unresolved"]) == ("distributions_only", False)
    m = apply_rcf_t4(_rcf(), result)[1]
    assert m["columns"][0]["rcf"] == 500e6 and m["trigger_summary"] == "trigger RCF/Debt T4: distributions only"
    vague = _run(_says("distributions_only", none=[_q("M02", DISTRIBUTIONS)]), db_session)  # says nothing about purchases
    assert vague["agent"]["held_back"][0]["reason"] == "the quote doesn't mention purchases of NCI ownership"
    assert (vague["status"], vague["disagreement"]) == ("unresolved", True)


def test_buyouts_mixed_in_but_not_quantified_are_unresolved(db_session):
    unquantified = _buyout("B2", "M04", UNQUANTIFIED)
    result = _run(_says("unresolved", [unquantified], none=[_q("M05", NO_PURCHASES)]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"] == "buyouts are mixed into the tagged amount but not quantified (B2)"
    metrics = apply_rcf_t4(_rcf(), result)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"rcf_t4": result})
    assert (e["id"], e["measure"]) == ("rcf_t4:rcf", "rcf")
    first = _run(_says("buyouts_excluded", [PARTNERS, unquantified]), db_session)  # a quantified one comes first
    assert (first["status"], first["excluded"]) == ("buyouts_excluded", 40e6)
    assert any(n.startswith("B2 also identified without an amount") for n in first["notes"])


def test_buyouts_never_take_the_distributions_below_zero(db_session):
    result = _run(_says("buyouts_excluded", [PARTNERS]), db_session, _rcf(nci=30e6))
    assert result["excluded"] == 30e6 and any("taken to zero" in n for n in result["notes"])
