"""FO Trigger Agent, Debt/EBITDAR T2: when it fires (the Debt / EBITDAR and Net debt / EBITDAR scores 2 or more apart;
agent triggers enabled), the passages it reads (MD&A and the notes), the verified evidence, the outcome (first match: a
quantified acquisition or payout earmark, or trapped cash, comes out of cash; cash earmarked only for debt maturities, or
freely available, gets no adjustment; an earmark or trapping described but not quantified is unresolved), the rescoring
of Net debt / EBITDAR and the escalation of Debt/EBITDAR.

Synthetic 10-K text and a scored Debt/EBITDAR measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.leverage_t2 import apply_leverage_t2, leverage_t2_passages, leverage_t2_trigger
from app.services.fo.leverage_triggers import apply_leverage_t1, leverage_t1_trigger
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import leverage_t2_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_leverage

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
TRAPPED = ("As of December 31, 2025, $400 million of our cash was held by foreign subsidiaries and is subject to "
           "repatriation restrictions.")
ACQUISITION = "We intend to use cash on hand to fund the acquisition of Valley Health, which we expect to close in 2026."
DIVIDEND = "On February 10, 2026, our board declared a special dividend of $200 million, payable in cash in March 2026."
MATURITY = "We set aside $500 million of cash on hand to repay our senior notes due 2026."
BUYBACK = ("Our board authorized share repurchases and other distributions to shareholders of up to $250 million, "
           "funded with cash on hand set aside for that purpose.")
AVAILABLE = "Our cash and cash equivalents are available for general corporate purposes."
REVENUE = "Revenue increased 5% in 2025 on higher patient volumes."
SETTINGS = {"values": {"p": 5.0, "m": 2.0, "delta": 2.5, "cap": 3.0},
            "benchmarks": {"debt_to_ebitdar": 3.0, "net_debt_to_ebitdar": 2.5}, "sector": "Hospitals/Healthcare Facilities"}


def _data():
    mdna = ["Liquidity and Capital Resources", TRAPPED, ACQUISITION, DIVIDEND, MATURITY, BUYBACK, AVAILABLE, REVENUE]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)}]}


def _col(cash, ebitdar=500e6, net_one_offs=0.0):
    """Debt + op. leases 2,000, EBITDAR 500: 4.0x gross; net of cash 1,500: 1.0x."""
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "ebitdar": ebitdar, "debt": 1800e6,
           "adj_debt": 2000e6, "cash": cash, "cash_concept": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
           "adj_net_debt": 2000e6 - cash, "one_off_charges": net_one_offs, "one_off_gains": 0.0,
           "net_one_offs": net_one_offs, "net_one_offs_concept": "tagged one-offs"}
    for key, num in (("debt_to_ebitdar", 2000e6), ("net_debt_to_ebitdar", 2000e6 - cash)):
        col[key] = {"ratio": num / ebitdar, "negative_ebitda": False, "reason": None}
    score_leverage(col, SETTINGS)
    return col


def _leverage(cash=1500e6, **kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "leverage", "title": "Debt to EBITDAR", "short": "Debt / EBITDAR", "rows": [],
             "columns": [_col(cash, **kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "D": 5.0, "D_dso": 3.0}}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, items=(), available=()):
    return {"leverage_t2": {"status": status, "items": list(items), "available": list(available),
                            "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return leverage_t2_trigger(_data(), metrics or _leverage(), ENABLED, session=db_session,
                               backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def _item(item_id, kind, pid, quote, amount=None):
    return {"item_id": item_id, "kind": kind, "description": kind,
            "evidence": _quote(pid, quote, amount=amount)}


ABROAD = _item("E1", "trapped", "C01", TRAPPED, "$400 million")
DEAL = _item("E2", "acquisition", "C02", ACQUISITION)
PAYOUT = _item("E3", "payout", "C03", DIVIDEND, "$200 million")
NOTES_DUE = _item("E4", "debt_maturity", "C04", MATURITY, "$500 million")
REPURCHASE = _item("E5", "payout", "C05", BUYBACK, "$250 million")
FREE = _quote("C06", AVAILABLE)


def test_the_fixture_sets_the_trigger_off():
    col = _leverage()[1]["columns"][0]
    assert col["debt_to_ebitdar"]["score"] - col["net_debt_to_ebitdar"]["score"] >= 2


def test_the_agent_reads_cash_passages():
    t = leverage_t2_passages(_data())
    assert [(p["passage_id"], p["text"]) for p in t["passages"]] == [
        ("C01", TRAPPED), ("C02", ACQUISITION), ("C03", DIVIDEND), ("C04", MATURITY), ("C05", BUYBACK),
        ("C06", AVAILABLE)]


def test_close_scores_a_going_concern_fail_or_no_score_do_not_fire(db_session):
    close = leverage_t2_trigger(_data(), _leverage(cash=100e6), ENABLED)
    assert (close["status"], close["fired"]) == ("not_triggered", False)
    assert "differ by" in close["reason"] and "less than 2" in close["reason"]
    skipped = leverage_t2_trigger(_data(), _leverage(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_leverage_t2(_leverage(), skipped) == _leverage()
    unscored = _leverage()
    unscored[1]["columns"][0]["composite"]["score"] = None
    assert leverage_t2_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fired_trigger_needs_the_agent_and_without_text_cash_is_free(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        leverage_t2_trigger(_data(), _leverage(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'leverage_t2' response"):
        leverage_t2_trigger(_data(), _leverage(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = leverage_t2_trigger(nothing, _leverage(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("freely_available", "no_text", False)


def test_trapped_cash_and_a_payout_come_out_of_cash_and_a_buyback_is_not_an_earmark(db_session):
    result = _run(_says("cash_reduced", [ABROAD, PAYOUT, REPURCHASE]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("cash_reduced", False, False)
    assert result["removed"] == 600e6  # 400 trapped + 200 dividend; the 250 buyback isn't counted
    assert result["agent"]["held_back"][0]["reason"] == "a share repurchase (buyback) authorisation isn't an earmark"
    metrics = _leverage()
    m = apply_leverage_t2(metrics, result)[1]
    col = m["columns"][0]
    assert (col["cash_before_trigger"], col["cash"], col["adj_net_debt"]) == (1500e6, 900e6, 1100e6)
    assert col["net_debt_to_ebitdar"]["ratio"] == pytest.approx(2.2)
    assert col["debt_to_ebitdar"]["ratio"] == 4.0  # the gross ratio doesn't change
    assert col["net_debt_to_ebitdar"]["score"] == _col(900e6)["net_debt_to_ebitdar"]["score"]
    assert col["composite"]["score"] == _col(900e6)["composite"]["score"]
    assert metrics[1]["columns"][0]["cash"] == 1500e6  # the input is untouched
    assert m["trigger_summary"] == "trigger Debt/EBITDAR T2: cash reduced" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"leverage_t2": result}) == []
    assert [r["label"] for r in measure_card(m)["result_rows"]] == [
        "Debt / EBITDAR", "Debt / EBITDAR score", "Net debt / EBITDAR",
        "Net debt / EBITDAR as calculated (before trigger Debt/EBITDAR T2)", "Net debt / EBITDAR score",
        "Composite leverage score as calculated (before trigger Debt/EBITDAR T2)",
        "Composite leverage score (after trigger Debt/EBITDAR T2)"]
    assert leverage_t2_card(result)["label"] == "CASH REDUCED: RECOMPUTED"


def test_cash_never_goes_below_zero(db_session):
    big = _item("E1", "trapped", "C04", MATURITY, "$500 million")  # wrong kind words: held back
    assert _run(_says("cash_reduced", [big]), db_session)["agent"]["held_back"][0]["reason"].startswith(
        "the quote doesn't say the cash is trapped")
    result = _run(_says("cash_reduced", [ABROAD]), db_session, _leverage(cash=1500e6))
    assert result["removed"] == 400e6
    capped = _run(_says("cash_reduced", [ABROAD, PAYOUT]), db_session,
                  [*_leverage()[:1], {**_leverage()[1], "columns": [{**_col(1500e6), "cash": 500e6}]}])
    assert capped["removed"] == 500e6 and any("cash is taken to zero" in n for n in capped["notes"])


def test_debt_maturities_only_and_freely_available_cash_get_no_adjustment(db_session):
    maturities = _run(_says("debt_maturities", [NOTES_DUE]), db_session)
    assert (maturities["status"], maturities["unresolved"], maturities["removed"]) == ("debt_maturities", False, 0.0)
    m = apply_leverage_t2(_leverage(), maturities)[1]
    assert m["columns"][0]["cash"] == 1500e6
    assert m["trigger_summary"] == "trigger Debt/EBITDAR T2: debt maturities only"
    free = _run(_says("freely_available", available=[FREE]), db_session)
    assert (free["status"], free["unresolved"]) == ("freely_available", False)
    assert free["reason"] == "Cash freely available (C06): no adjustment"


def test_an_earmark_described_but_not_quantified_is_unresolved(db_session):
    result = _run(_says("unresolved", [DEAL, NOTES_DUE]), db_session)  # debt maturities aren't "only" any more
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"] == "an earmark or trapped cash is described but not quantified (E2)"
    metrics = apply_leverage_t2(_leverage(), result)
    assert metrics[1]["columns"][0]["cash"] == 1500e6 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t2": result})
    assert (e["id"], e["measure"]) == ("leverage_t2:leverage", "leverage") and "disagreement" not in e
    no_amount = _run(_says("cash_reduced", [_item("E1", "trapped", "C01", TRAPPED, "$450 million")]), db_session)
    assert no_amount["agent"]["held_back"][0]["reason"] == "the amount $450 million is not in the quote"
    assert (no_amount["status"], no_amount["disagreement"]) == ("unresolved", True)  # described, not quantified


def test_a_quantified_earmark_comes_first_and_an_unquantified_one_is_noted(db_session):
    result = _run(_says("cash_reduced", [ABROAD, DEAL]), db_session)
    assert (result["status"], result["removed"], result["unresolved"]) == ("cash_reduced", 400e6, False)
    assert any(n.startswith("E2 also described without an amount: not subtracted") for n in result["notes"])


def test_t1_then_t2_share_one_leverage_decision(db_session):
    """T1 normalises EBITDAR (unresolved here), then T2 takes trapped cash out: both rows show, one escalation."""
    metrics = _leverage(net_one_offs=100e6)
    t1 = leverage_t1_trigger({**_data(), "document": []}, metrics, ENABLED)  # no text: unresolved
    metrics = apply_leverage_t1(metrics, t1)
    t2 = _run(_says("unresolved", [DEAL]), db_session, metrics)
    metrics = apply_leverage_t2(metrics, t2)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"leverage_t1": t1, "leverage_t2": t2})
    assert e["checks"] == ["leverage_t1", "leverage_t2"]
    labels = [r["label"] for r in measure_card(metrics[1])["result_rows"]]
    assert "Net debt / EBITDAR after trigger Debt/EBITDAR T1, before trigger Debt/EBITDAR T2" in labels
    assert labels[-1] == "Composite leverage score (after trigger Debt/EBITDAR T1 and Debt/EBITDAR T2)"
