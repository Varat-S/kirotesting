"""FO Trigger Agent, Liquidity T1: when it fires (the revolver availability at the year-end not tagged or found in the
10-K text; agent triggers enabled; not after a covenant breach or with a manual revolver figure), the passages it reads
(the debt note and MD&A liquidity), the verified evidence, the outcome (first match, per facility: availability
stated is used; otherwise commitment − drawn − letters of credit, the lower of that and the borrowing base for an
asset-based revolver; no revolver gets no adjustment; a revolver whose availability can't be worked out is
unresolved), the rescoring of liquidity and debt maturity and its escalation.

Synthetic 10-K text and a scored liquidity measure only; the agent runs on a scripted offline backend (no network, no
model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.liquidity_triggers import apply_liquidity_t1, liquidity_t1_passages, liquidity_t1_trigger
from app.services.fo.measures import score_liquidity
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import liquidity_t1_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import COVENANT_BLOCK, adjust_for_covenants

ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
NOT_FOUND = "Not tagged and not found in the 10-K text: left out (conservative)"
AVAILABLE = "As of December 31, 2025, we had $400 million available for borrowing under our revolving credit facility."
LATER = "As of February 15, 2026, we had $450 million available for borrowing under our revolving credit facility."
COMMIT = "Our revolving credit facility provides for commitments of $500 million and matures on March 1, 2030."
DRAWN = "At December 31, 2025, borrowings of $80 million were outstanding under the revolving credit facility."
NONE_DRAWN = "At December 31, 2025, there were no borrowings outstanding under the revolving credit facility."
LCS = "Letters of credit of $20 million were issued under the revolving credit facility at December 31, 2025."
ABL = ("Our asset-based revolving credit facility provides for commitments of $300 million, subject to a borrowing "
       "base calculated monthly.")
ABL_DRAWN = ("At December 31, 2025, there were no borrowings outstanding under the asset-based revolving credit "
             "facility.")
ABL_LCS = "At December 31, 2025, letters of credit of $10 million were outstanding under the asset-based facility."
BASE = "At December 31, 2025, the borrowing base under the asset-based revolving credit facility was $250 million."
SOON = "The revolving credit facility expires on June 30, 2026, and we intend to replace it before then."
TERM_LOAN = "Our term loan bears interest at a rate equal to SOFR plus 1.75%, payable quarterly in arrears."


def _data():
    mdna = ["Liquidity and Capital Resources", AVAILABLE, LATER]
    notes = ["NOTE 7 — LONG-TERM DEBT", TERM_LOAN, COMMIT, DRAWN, NONE_DRAWN, LCS, ABL, ABL_DRAWN, ABL_LCS, BASE,
             SOON]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _col(override=None, blocked=None, found=None):
    """Sources 100 cash + 200 CFO against 150 due + 100 CapEx + 50 dividends: coverage 1.0x (score 7). Refinancing:
    100 cash + 2 × 50 FCF against 600 weighted maturities: 0.33x (score 9). The section scores 9."""
    col = {"heading": "Next 12M from 2025-12-31", "as_at": "2025-12-31", "cash": 100e6, "cfo": 200e6, "y1": 150e6,
           "capex": 100e6, "dividends": 50e6, "nci_distributions": 0.0, "weighted_maturities": 600e6,
           "concentration": 0.5, "revolver_found": found, "revolver_found_source": "Tagged" if found else NOT_FOUND}
    score_liquidity(col, override, blocked)
    return col


def _liquidity(covenant=None, **kw):
    m = {"key": "liquidity", "title": "Liquidity and debt maturity", "short": "Liquidity",
         "rows": [("cash", "Cash"), ("revolver", "Revolver availability"), ("cfo", "Operating cash flow (CFO)")],
         "columns": [_col(**kw)]}
    if covenant:
        adjust_for_covenants(m, covenant)
    return [{"key": "overall", "columns": [{"score": 5}]}, m]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _facility(fid="R1", evidence=None, asset_based=False, **figures):
    return {"facility_id": fid, "description": "Revolving credit facility", "asset_based": asset_based,
            "evidence": evidence or _quote(_pid(COMMIT), COMMIT),
            **{k: figures.get(k) for k in ("availability", "commitment", "drawn", "letters_of_credit",
                                           "borrowing_base", "maturity")}}


def _says(status, *facilities):
    return {"liquidity_t1": {"status": status, "facilities": list(facilities), "rationale": "Scripted.",
                             "missing_information": []}}


def _run(response, db_session, metrics=None, data=None):
    return liquidity_t1_trigger(data or _data(), metrics or _liquidity(), ENABLED, session=db_session,
                                backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def _pid(text):
    return next(p["passage_id"] for p in liquidity_t1_passages(_data())["passages"] if p["text"] == text)


def _fig(text, amount, date="2025-12-31"):
    return _quote(_pid(text), text, amount=amount, date=date)


STATED = _facility(availability=_quote("L01", AVAILABLE, amount="$400 million", date="2025-12-31"))
COMPUTED = _facility(commitment=_quote("L03", COMMIT, amount="$500 million"), drawn=_fig(DRAWN, "$80 million"),
                     letters_of_credit=_fig(LCS, "$20 million"),
                     maturity=_quote("L03", COMMIT, date="2030-03-01"))


def test_the_agent_reads_the_debt_note_and_mdna_liquidity():
    t = liquidity_t1_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("L01", "mdna_liquidity", AVAILABLE), ("L02", "mdna_liquidity", LATER), ("L03", "debt_note", COMMIT),
        ("L04", "debt_note", DRAWN), ("L05", "debt_note", NONE_DRAWN), ("L06", "debt_note", LCS),
        ("L07", "debt_note", ABL), ("L08", "debt_note", ABL_DRAWN), ("L09", "debt_note", ABL_LCS),
        ("L10", "debt_note", BASE), ("L11", "debt_note", SOON)]  # the term loan isn't read


def test_a_found_revolver_a_covenant_breach_a_manual_figure_or_a_skip_do_not_fire(db_session):
    found = liquidity_t1_trigger(_data(), _liquidity(found=300e6), ENABLED)
    assert (found["status"], found["fired"]) == ("not_triggered", False) and "found (300.0 million" in found["reason"]
    breach = liquidity_t1_trigger(_data(), _liquidity(covenant="breach", blocked=COVENANT_BLOCK), ENABLED)
    assert (breach["status"], breach["fired"]) == ("not_triggered", False) and "covenant breach" in breach["reason"]
    manual = liquidity_t1_trigger(_data(), _liquidity(override=250.0), ENABLED)
    assert (manual["status"], manual["fired"]) == ("not_triggered", False) and "manually" in manual["reason"]
    skipped = liquidity_t1_trigger(_data(), _liquidity(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_liquidity_t1(_liquidity(), skipped) == _liquidity()
    unscored = _liquidity()
    unscored[1]["columns"][0]["score"] = None
    assert liquidity_t1_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_no_revolver_passage_means_no_revolver_without_the_agent(db_session):
    result = liquidity_t1_trigger({**_data(), "document": []}, _liquidity(), ENABLED)
    assert result["fired"] and result["fired_by"].startswith("revolver availability at 2025-12-31 not found")
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("no_revolver", "no_text", False)
    m = apply_liquidity_t1(_liquidity(), result)[1]
    assert m["columns"][0]["score"] == 9 and m["trigger_summary"] == "trigger Liquidity T1: no revolver"


def test_a_fired_trigger_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        liquidity_t1_trigger(_data(), _liquidity(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'liquidity_t1' response"):
        liquidity_t1_trigger(_data(), _liquidity(), ENABLED, backend=scripted_backend({"icr_t1": {}}),
                             mode="scripted")


def test_a_stated_availability_is_put_in_and_both_scores_recomputed(db_session):
    result = _run(_says("stated", STATED), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("stated", False, False)
    assert result["availability"] == 400e6
    metrics = _liquidity()
    m = apply_liquidity_t1(metrics, result)[1]
    col = m["columns"][0]
    assert (col["revolver"], col["coverage"], col["refinancing"]) == pytest.approx((400e6, 700 / 300, 1.0))
    assert (col["coverage_score"], col["refinancing_score"], col["score"]) == (1, 3, 3)
    assert col["revolver_source"].startswith("Trigger Liquidity T1: R1: 400.0 million available as stated (L01)")
    adj = col["liquidity_adjustment"]
    assert (adj["calculated"], adj["recomputed"], adj["coverage_score_calculated"]) == (9, 3, 7)
    assert metrics[1]["columns"][0]["revolver_found"] is None  # the input is untouched
    assert m["trigger_summary"] == "trigger Liquidity T1: availability stated" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"liquidity_t1": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert labels[-2:] == ["Section score before trigger Liquidity T1", "Section score (after trigger Liquidity T1)"]
    card = liquidity_t1_card(result)
    assert (card["label"], card["availability"], card["facilities"][0]["route"]) == (
        "AVAILABILITY STATED", "400.0", "availability stated")


def test_a_figure_after_the_year_end_is_not_the_year_ends(db_session):
    later = _facility(availability=_quote("L02", LATER, amount="$450 million", date="2026-02-15"))
    result = _run(_says("stated", later), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "the date 2026-02-15 isn't the year-end (2025-12-31)"]
    assert (result["status"], result["disagreement"], result["unresolved"]) == ("unresolved", True, True)
    [e] = escalations_for(PASS, COMPLIANT, apply_liquidity_t1(_liquidity(), result)[1:], None, None,
                          {"liquidity_t1": result})
    assert (e["id"], e["measure"], e["measure_name"]) == ("liquidity_t1:liquidity", "liquidity",
                                                          "Liquidity and debt maturity")


def test_not_stated_commitment_less_drawn_less_letters_of_credit(db_session):
    result = _run(_says("computed", COMPUTED), db_session)
    assert (result["status"], result["unresolved"], result["availability"]) == ("computed", False, 400e6)
    [f] = result["facilities"]
    assert (f["route"], f["maturity"]["date"], f["maturity"]["within_12_months"]) == ("computed", "2030-03-01", False)
    assert result["source"] == ("R1: 500.0 committed − 80.0 drawn − 20.0 letters of credit = 400.0 million "
                                "(L03, L04, L06)")
    assert apply_liquidity_t1(_liquidity(), result)[1]["columns"][0]["score"] == 3


def test_nothing_drawn_counts_only_with_a_quote_saying_so(db_session):
    nil = _facility(commitment=_quote("L03", COMMIT, amount="$500 million"),
                    drawn=_fig(NONE_DRAWN, None), letters_of_credit=_fig(LCS, "$20 million"))
    result = _run(_says("computed", nil), db_session)
    assert (result["status"], result["availability"]) == ("computed", 480e6)
    unsaid = _facility(commitment=_quote("L03", COMMIT, amount="$500 million"), drawn=_fig(DRAWN, None),
                       letters_of_credit=_fig(LCS, "$20 million"))
    held = _run(_says("unresolved", unsaid), db_session)
    assert [h["reason"] for h in held["agent"]["held_back"]] == [
        "no amount given and the quote doesn't say none is outstanding"]
    assert (held["status"], held["unresolved"], held["disagreement"]) == ("unresolved", True, False)
    assert held["unresolved_why"] == ("a revolver exists but its availability can't be worked out (R1: no "
                                      "availability stated and no verified drawn)")


def test_an_asset_based_revolver_takes_the_lower_of_that_and_the_borrowing_base(db_session):
    abl = dict(fid="A1", evidence=_quote(_pid(ABL), ABL), commitment=_quote(_pid(ABL), ABL, amount="$300 million"),
               drawn=_fig(ABL_DRAWN, None), letters_of_credit=_fig(ABL_LCS, "$10 million"))
    result = _run(_says("computed", _facility(**abl, borrowing_base=_fig(BASE, "$250 million"))), db_session)
    [f] = result["facilities"]
    assert (f["asset_based"], f["route"], f["availability"]) == (True, "computed_abl", 250e6)  # 290 vs 250
    no_base = _run(_says("unresolved", _facility(**abl)), db_session)  # asset-based: read from the quote
    assert (no_base["status"], no_base["unresolved"]) == ("unresolved", True)
    assert "an asset-based revolver with no verified borrowing base" in no_base["unresolved_why"]


def test_two_facilities_are_summed(db_session):
    abl = _facility("A1", _quote(_pid(ABL), ABL), True, commitment=_quote(_pid(ABL), ABL, amount="$300 million"),
                    drawn=_fig(ABL_DRAWN, None), letters_of_credit=_fig(ABL_LCS, "$10 million"),
                    borrowing_base=_fig(BASE, "$250 million"))
    result = _run(_says("computed", STATED, abl), db_session)
    assert (result["status"], result["availability"]) == ("computed", 650e6)


def test_letters_of_credit_not_given_is_unresolved(db_session):
    no_lcs = _facility(commitment=_quote("L03", COMMIT, amount="$500 million"), drawn=_fig(DRAWN, "$80 million"))
    result = _run(_says("unresolved", no_lcs), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"].endswith("(R1: no availability stated and no verified letters of credit)")
    metrics = apply_liquidity_t1(_liquidity(), result)
    assert metrics[1]["columns"][0]["score"] == 9 and metrics[1]["trigger_escalated"]
    assert liquidity_t1_card(result)["label"] == "UNRESOLVED"


def test_no_revolver_in_the_debt_note_gets_no_adjustment(db_session):
    result = _run(_says("no_revolver"), db_session)
    assert (result["status"], result["unresolved"]) == ("no_revolver", False)
    m = apply_liquidity_t1(_liquidity(), result)[1]
    assert m["columns"][0]["score"] == 9 and m["columns"][0]["revolver"] is None
    term_loan = _run(_says("no_revolver", _facility(evidence=_quote("L03", "provides for commitments of $500 "
                                                                            "million"))), db_session)
    assert [h["reason"] for h in term_loan["agent"]["held_back"]] == [
        "the quote doesn't name a revolving credit facility"]
    assert (term_loan["status"], term_loan["unresolved"]) == ("no_revolver", False)


def test_a_revolver_maturing_within_12_months_is_noted_but_counted(db_session):
    soon = {**STATED, "maturity": _quote(_pid(SOON), SOON, date="2026-06-30")}
    result = _run(_says("stated", soon), db_session)
    assert result["status"] == "stated" and result["availability"] == 400e6
    assert any("matures within 12 months of the year-end (2026-06-30)" in n for n in result["notes"])
    assert liquidity_t1_card(result)["facilities"][0]["matures_soon"]


def test_a_covenant_relief_is_applied_again_after_the_recomputation(db_session):
    metrics = _liquidity(covenant="relief")
    assert metrics[1]["columns"][0]["score"] == 10  # 9 + 1
    result = liquidity_t1_trigger(_data(), metrics, ENABLED, session=db_session,
                                  backend=scripted_backend(_says("stated", STATED)), mode="scripted")
    m = apply_liquidity_t1(metrics, result)[1]
    col = m["columns"][0]
    assert (col["score"], col["covenant_adjustment"]["calculated"], col["liquidity_adjustment"]["calculated"]) == (
        4, 3, 10)
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert labels[-3:] == ["Section score before trigger Liquidity T1",
                           "Section score after trigger Liquidity T1, before the covenant adjustment",
                           "Section score (after trigger Liquidity T1 and the covenant adjustment)"]


def test_the_agents_answer_is_recomputed_but_not_applied(db_session):
    result = _run(_says("computed", COMPUTED), db_session)
    assert result["agent_reading"]["applies"]["availability"] == 400e6
