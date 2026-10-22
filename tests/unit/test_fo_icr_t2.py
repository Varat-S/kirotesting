"""FO Trigger Agent, ICR T2: when it fires (ICR's interest from a net-interest or operating-interest tag; agent triggers
enabled), ICR n/a when net interest is income, the passages it reads (the debt note, MD&A, the other notes), the
verified evidence, the interest base it re-derives (gross disclosed; else net + interest income; else, for an
operating tag, as tagged; else unresolved), the undesignated swap mark-to-market it removes (and ICR T1 then doesn't),
the operating-tag flag, and the escalation of ICR when unresolved or when the agent and the evidence disagree.

Synthetic 10-K text and a scored ICR column only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.icr_t2 import apply_icr_t2, icr_t2_passages, icr_t2_trigger
from app.services.fo.icr_triggers import icr_t1_trigger
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import icr_t2_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import headline_score

FY25 = "2025-01-01--2025-12-31"
NET, OPERATING = "us-gaap:InterestIncomeExpenseNonoperatingNet", "us-gaap:InterestExpenseOperating"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
NET_TEXT = "Interest expense, net was $100 million in 2025, compared with $90 million in 2024."
GROSS_TEXT = "Interest expense on our borrowings was $180 million in 2025."
SWAP_TEXT = ("Interest expense includes a $20 million loss from the change in fair value of interest rate swaps that are "
             "not designated as hedges.")
INCOME_TEXT = "Interest income on cash and investments was $60 million in 2025."
WAGES = "Our operating expenses increased in 2025 because of higher wages."
TAX = "Our effective tax rate was 21% in 2025."


def _data():
    mdna = ["Results of Operations", NET_TEXT, WAGES]
    notes = ["NOTE 7 — LONG-TERM DEBT", GROSS_TEXT, SWAP_TEXT, "NOTE 12 — OTHER INCOME", INCOME_TEXT,
             "NOTE 13 — INCOME TAXES", TAX]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _icr(concept=NET, net_income=None):
    """A scored ICR: (EBIT 300 + rent 50) / (interest 100 net + 10 capitalised + rent 50) = 2.19x (2–3x: 6)."""
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "score": 6, "ratio": 350 / 160,
           "reason": None, "ebit": 300e6, "rent": 50e6, "ebitr": 350e6, "interest": 110e6,
           "interest_concept": f"{concept} + capitalised", "interest_capitalised": 10e6, "interest_expense": 100e6,
           "interest_expense_concept": concept, "net_interest_income": None, "fixed_charges": 160e6}
    if net_income is not None:
        col.update(score=None, ratio=None, interest=None, fixed_charges=None, interest_expense=None,
                   interest_expense_concept=None, net_interest_income=net_income)
    prior = {**col, "heading": "FY2024", "period": "2024-01-01--2024-12-31", "score": 7.5, "ratio": 1.8}
    return [{"key": "overall", "columns": [{"score": 7}]},
            {"key": "icr", "title": "ICR", "short": "ICR", "rows": [], "bands": [], "columns": [col, prior]}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, gross=None, income=None, swap=None):
    return {"icr_t2": {"status": status, "gross_interest": gross, "interest_income": income,
                       "swap_mark_to_market": swap, "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return icr_t2_trigger(_data(), metrics or _icr(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


GROSS = _quote("J02", GROSS_TEXT, amount="$180 million")
INCOME = _quote("J04", INCOME_TEXT, amount="$60 million")
SWAP = {"direction": "loss", "evidence": _quote("J03", SWAP_TEXT, amount="$20 million"), "undesignated": None}


def test_the_agent_reads_the_debt_note_mdna_and_the_other_notes():
    t = icr_t2_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("J01", "mdna", NET_TEXT), ("J02", "debt_note", GROSS_TEXT), ("J03", "debt_note", SWAP_TEXT),
        ("J04", "notes", INCOME_TEXT)]


def test_a_gross_tag_or_a_going_concern_fail_does_not_fire(db_session):
    gross = icr_t2_trigger(_data(), _icr(concept="us-gaap:InterestExpense"), ENABLED)
    assert (gross["status"], gross["fired"]) == ("not_triggered", False)
    assert gross["reason"] == ("Interest is from us-gaap:InterestExpense, a gross interest tag: trigger ICR T2 not "
                               "set off")
    skipped = icr_t2_trigger(_data(), _icr(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False) and apply_icr_t2(_icr(), skipped) == _icr()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_net_interest_income_makes_icr_not_applicable_without_the_agent(db_session):
    result = icr_t2_trigger(_data(), _icr(net_income=40e6), ENABLED)  # no backend: no agent is needed
    assert (result["status"], result["fired"], result["unresolved"], result["agent"]) == (
        "not_applicable", True, False, None)
    assert result["reason"] == ("Net interest is income (40.0 million): ICR is n/a, flagged and not scored as weak "
                                "cover")
    metrics = apply_icr_t2(_icr(net_income=40e6), result)
    m = metrics[1]
    # Not scored: the prior year's 7.5 doesn't stand in, and the composite leaves ICR out.
    assert m["not_applicable"] and headline_score(m) == (None, None)
    card = measure_card(m)
    assert card["headline"]["text"] == "n/a" and card["flag_notes"][0].startswith("ICR n/a (trigger ICR T2)")
    assert escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"icr_t2": result}) == []
    # ICR T1 doesn't fire on an ICR that is n/a.
    assert icr_t1_trigger(_data(), metrics, ENABLED)["status"] == "not_triggered"


def test_a_net_tag_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        icr_t2_trigger(_data(), _icr(), ENABLED)
    nothing = _data()
    nothing["document"] = []
    result = icr_t2_trigger(nothing, _icr(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("unresolved", "no_text", True)


def test_gross_interest_disclosed_recomputes_icr(db_session):
    result = _run(_says("gross_disclosed", GROSS, INCOME), db_session)  # gross comes first
    assert (result["status"], result["gross_used"], result["unresolved"]) == ("gross_disclosed", 180e6, False)
    metrics = _icr()
    _, m = apply_icr_t2(metrics, result)
    col = m["columns"][0]
    # (300 + 50) / (180 + 10 capitalised + 50) = 1.46x: 8.5. EBIT is unchanged.
    assert (col["interest_reported"], col["interest"], col["ebit"]) == (110e6, 190e6, 300e6)
    assert col["ratio"] == pytest.approx(350 / 240) and col["score"] == 8.5
    assert col["interest_adjustment"]["calculated"] == 6 and metrics[1]["columns"][0]["interest"] == 110e6
    assert m["trigger_summary"] == "trigger ICR T2: recomputed" and "flag_notes" not in m


def test_only_net_interest_given_gross_is_net_plus_interest_income(db_session):
    result = _run(_says("gross_from_income", income=INCOME), db_session)
    assert (result["status"], result["gross_used"]) == ("gross_from_income", 160e6)
    assert result["reason"].startswith("Only net interest given: gross = 100.0 million net + 60.0 million interest "
                                       "income (J04) = 160.0 million")
    assert apply_icr_t2(_icr(), result)[1]["columns"][0]["score"] == 7.5  # 350 / 220 = 1.59x


def test_an_undesignated_swap_is_removed_and_icr_t1_does_not_remove_it_again(db_session):
    result = _run(_says("gross_disclosed", GROSS, swap=SWAP), db_session)
    assert (result["gross_used"], result["swap"]["amount"]) == (160e6, 20e6)  # 180 − 20
    metrics = apply_icr_t2(_icr(), result)
    col = metrics[1]["columns"][0]
    assert (col["interest"], col["swap_mtm_removed"], col["score"]) == (170e6, 20e6, 7.5)
    t1 = icr_t1_trigger(_data(), [metrics[0], {**metrics[1], "columns": [{**col, "score": 8.5}, col]}], ENABLED,
                        backend=scripted_backend({"icr_t1": {
                            "status": "adjusted", "one_offs": [], "swap_mark_to_market": SWAP,
                            "capitalised_interest": None, "rationale": "Scripted.", "missing_information": []}}),
                        session=db_session, mode="scripted")
    assert t1["swap"] is None and t1["status"] == "nothing_found"
    assert t1["agent"]["held_back"][0]["reason"] == "already removed by ICR trigger T2"


def test_an_operating_tag_is_flagged(db_session):
    kept = _run(_says("kept_as_tagged", income=INCOME), db_session, metrics=_icr(concept=OPERATING))
    assert (kept["status"], kept["interest_change"], kept["flags"], kept["unresolved"]) == (
        "kept_as_tagged", 0.0, ["operating_interest"], False)
    m = apply_icr_t2(_icr(concept=OPERATING), kept)[1]
    assert m["columns"][0]["score"] == 6 and m["flag_notes"][0].startswith("ICR is less meaningful")
    assert icr_t2_card(kept)["flags"] == ["ICR less meaningful: operating interest (often a captive finance arm)"]
    recomputed = _run(_says("gross_disclosed", GROSS), db_session, metrics=_icr(concept=OPERATING))
    assert recomputed["gross_used"] == 180e6 and "operating_interest" in recomputed["flags"]


def test_neither_gross_nor_income_is_unresolved_and_escalates_icr(db_session):
    result = _run(_says("unresolved"), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    metrics = apply_icr_t2(_icr(), result)
    assert metrics[1]["columns"][0]["score"] == 6 and metrics[1]["trigger_escalated"]
    assert ("Unresolved: neither gross interest expense nor interest income is disclosed."
            in metrics[1]["trigger_note"])
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"icr_t2": result})
    assert (e["id"], e["measure"], e["engine_score"], e["calculated_score"]) == ("icr_t2:icr", "icr", 6, 6)
    assert "disagreement" not in e
    t1 = {"fired": True, "unresolved": True, "disagreement": False, "score": 6, "column": "FY2025",
          "status": "nothing_found", "reason": "Nothing found"}
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"icr_t2": result, "icr_t1": t1})
    assert e["checks"] == ["icr_t2", "icr_t1"]  # one ICR decision for both


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    net_figure = _quote("J01", NET_TEXT, amount="$100 million")  # "Interest expense, net": not gross
    result = _run(_says("gross_disclosed", net_figure), db_session)
    assert result["agent"]["held_back"][0]["reason"] == "the quote describes a net figure, not gross interest expense"
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, True)
    low = GROSS_TEXT.replace("$180 million", "$80 million")
    data = _data()
    data["document"][1]["text"] = data["document"][1]["text"].replace(GROSS_TEXT, low)
    below = icr_t2_trigger(data, _icr(), ENABLED, session=db_session, mode="scripted",
                           backend=scripted_backend(_says("gross_disclosed", _quote("J02", low, amount="$80 million"))))
    assert below["agent"]["held_back"][0]["reason"] == ("80.0 million is less than the net interest expense tagged "
                                                        "(100.0 million)")
