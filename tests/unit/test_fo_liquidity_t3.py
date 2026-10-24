"""FO Trigger Agent, Liquidity T3: when it fires (the largest single year of maturities in years 2–5 at 25% or more of
total debt in the maturity table; agent triggers enabled; not after a covenant breach), the passages it reads, the
verified instruments in the wall year, the outcome (more than 50% secured bank debt or mortgages: −1; more than 50%
unsecured bonds: +1; mixed: no change) and the escalation when the agent and the evidence disagree.

Synthetic 10-K text and a scored liquidity measure only; the agent runs on a scripted offline backend.
"""

from __future__ import annotations

import pytest

from app.services.fo.liquidity_t3 import apply_liquidity_t3, liquidity_t3_passages, liquidity_t3_trigger
from app.services.fo.measures import score_liquidity, set_ladder
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import liquidity_t3_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import COVENANT_BLOCK, adjust_for_covenants

ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
TERM = "Our secured term loan of $250 million, collateralized by substantially all of our assets, is due June 30, 2028."
MORTGAGE = "Mortgage loans of $50 million on our hospital properties mature in March 2028."
BONDS = "Our 5.0% senior unsecured notes of $300 million are due September 1, 2028."
LATER = "Our 6.0% senior unsecured notes of $200 million are due in 2031."


def _data():
    notes = ["NOTE 7 — LONG-TERM DEBT", TERM, MORTGAGE, BONDS, LATER]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _liquidity(y3=400e6, covenant=None, table=True):
    """Total 1,000 (to after year 5): year 3 is 400, 40% of total debt."""
    col = {"heading": "Next 12M from 2025-12-31", "as_at": "2025-12-31", "cash": 300e6, "cfo": 200e6,
           "capex": 100e6, "dividends": 0.0, "nci_distributions": 0.0, "debt": 1000e6, "revolver_found": None,
           "revolver_found_source": "Not found"}
    ladder = ({"y1": 100e6, "y2": 100e6, "y3": y3, "y4": 100e6, "y5": 100e6, "after": 600e6 - y3} if table
              else {"y1": 100e6})
    set_ladder(col, ladder, "Tagged maturity table" if table else None, "Tagged")
    score_liquidity(col, None, COVENANT_BLOCK if covenant == "breach" else None)
    m = {"key": "liquidity", "title": "Liquidity and debt maturity", "short": "Liquidity", "rows": [],
         "columns": [col]}
    if covenant:
        adjust_for_covenants(m, covenant)
    return [{"key": "overall", "columns": [{"score": 5}]}, m]


def _inst(iid, kind, text, amount, date):
    pid = next(p["passage_id"] for p in liquidity_t3_passages(_data())["passages"] if p["text"] == text)
    return {"item_id": iid, "description": kind, "kind": kind,
            "evidence": {"passage_id": pid, "quote": text, "amount": amount, "date": date}}


def _says(status, *instruments):
    return {"liquidity_t3": {"status": status, "instruments": list(instruments), "rationale": "Scripted.",
                             "missing_information": []}}


def _run(response, db_session, metrics=None):
    return liquidity_t3_trigger(_data(), metrics or _liquidity(), ENABLED, session=db_session,
                                backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


SECURED = _inst("I1", "secured_bank", TERM, "$250 million", "2028-06-30")
MORT = _inst("I2", "mortgage", MORTGAGE, "$50 million", "2028-03")
UNSECURED = _inst("I3", "unsecured_bond", BONDS, "$300 million", "2028-09-01")


def test_a_wall_below_25_percent_no_table_a_breach_or_a_skip_do_not_fire(db_session):
    low = liquidity_t3_trigger(_data(), _liquidity(y3=200e6), ENABLED)  # 20%
    assert (low["status"], low["fired"]) == ("not_triggered", False) and "20.0% of total debt" in low["reason"]
    assert liquidity_t3_trigger(_data(), _liquidity(table=False), ENABLED)["reason"].startswith("No debt maturity")
    assert "covenant breach" in liquidity_t3_trigger(_data(), _liquidity(covenant="breach"), ENABLED)["reason"]
    assert liquidity_t3_trigger(_data(), _liquidity(), SKIPPED)["status"] == "skipped"
    with pytest.raises(PrecheckAgentError, match="no 'liquidity_t3' response"):
        liquidity_t3_trigger(_data(), _liquidity(), ENABLED, backend=scripted_backend({"icr_t1": {}}),
                             mode="scripted")


def test_a_mostly_secured_wall_is_minus_one(db_session):
    result = _run(_says("secured", SECURED, MORT), db_session)
    assert result["fired_by"] == "Year 3: 400.0 million, 40.0% of total debt (25% or more)"
    assert (result["status"], result["step"], result["unresolved"]) == ("secured", -1, False)
    assert result["secured_share"] == pytest.approx(0.75)
    metrics = _liquidity()
    m = apply_liquidity_t3(metrics, result)[1]
    calculated = metrics[1]["columns"][0]["score"]
    assert m["columns"][0]["score"] == max(1, calculated - 1)
    assert m["trigger_summary"] == "trigger Liquidity T3: −1, secured wall"
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"liquidity_t3": result}) == []
    assert liquidity_t3_card(result)["label"] == "SECURED: −1"


def test_a_mostly_unsecured_wall_is_plus_one_and_a_mixed_one_no_change(db_session):
    result = _run(_says("unsecured", UNSECURED), db_session)  # 300 / 400
    assert (result["status"], result["step"]) == ("unsecured", 1)
    metrics = _liquidity()
    assert apply_liquidity_t3(metrics, result)[1]["columns"][0]["score"] == min(
        10, metrics[1]["columns"][0]["score"] + 1)
    mixed = _run(_says("mixed", MORT), db_session)  # 50 of 400: neither kind is more than half
    assert (mixed["status"], mixed["step"], mixed["unresolved"]) == ("mixed", 0, False)
    held = _run(_says("mixed", SECURED, _inst("I3", "unsecured_bond", BONDS, "$150 million", "2028-09-01")),
                db_session)
    assert held["agent"]["held_back"][0]["reason"] == "the amount $150 million is not in the quote"
    assert (held["status"], held["disagreement"]) == ("secured", True)  # 250 of 400 secured
    [e] = escalations_for(PASS, COMPLIANT, apply_liquidity_t3(_liquidity(), held)[1:], None, None,
                          {"liquidity_t3": held})
    assert e["id"] == "liquidity_t3:liquidity"


def test_instruments_outside_the_wall_year_or_of_the_wrong_kind_are_held_back(db_session):
    later = _inst("I4", "unsecured_bond", LATER, "$200 million", "2031")
    wrong = _inst("I5", "secured_bank", BONDS, "$300 million", "2028-09-01")
    result = _run(_says("mixed", later, wrong), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "the maturity 2031 isn't in the wall year (after 2027-12-31, up to 2028-12-31)",
        "the quote doesn't show secured bank debt"]
    assert (result["status"], result["unresolved"]) == ("mixed", False)
    m = apply_liquidity_t3(_liquidity(), result)[1]
    assert m["trigger_summary"] == "trigger Liquidity T3: mixed, no change"
