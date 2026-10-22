"""FO Trigger Agent, ICR T1: when it fires (ICR score 8.5 or more, below 1.5x; agent triggers enabled), the passages it
reads (MD&A and the notes), the verified evidence, the two adjustments that apply together (quantified one-offs in EBIT:
charges added back, gains removed; an undesignated swap's mark-to-market removed from interest incurred), capitalised
interest as a cross-check, and the escalation of ICR when nothing is found or the agent and the evidence disagree.

Synthetic 10-K text and a scored ICR column only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.icr_triggers import apply_icr_t1, icr_t1_passages, icr_t1_trigger
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import icr_t1_card, measure_card
from app.services.fo.runner import escalations_for

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
IMPAIRMENT = "In 2025, we recorded a goodwill impairment charge of $150 million in our Retail segment."
GAIN = "We recognized a gain on the sale of our Denver facility of $30 million in 2025."
WAGES = "Our operating expenses increased in 2025 because of higher wages and benefits."
FORECAST = "We expect restructuring charges of $40 million in 2026."
TESTING = "We test goodwill for impairment annually, or more often when indicators of impairment exist."
UNDESIGNATED = ("Our interest rate swaps are not designated as hedges, and changes in their fair value are recorded in "
                "interest expense.")
SWAP_LOSS = "In 2025, the change in fair value of the swaps was a loss of $60 million."
CAPITALISED = "We capitalized interest of $25 million in 2025 on facilities under construction."
TAX = "Our effective tax rate was 21% in 2025."


def _data():
    mdna = ["Results of Operations", IMPAIRMENT, GAIN, WAGES, FORECAST, "Critical Accounting Estimates", TESTING]
    notes = ["NOTE 8 — DERIVATIVES", UNDESIGNATED, SWAP_LOSS, "NOTE 9 — PROPERTY AND EQUIPMENT", CAPITALISED,
             "NOTE 10 — INCOME TAXES", TAX]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _icr(score=10, capitalised=None):
    """A scored ICR: (EBIT 200 + rent 50) / (interest 250 + rent 50) = 0.83x (below 1.0x: 10)."""
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "score": score, "ratio": 250 / 300,
           "reason": None, "ebit": 200e6, "ebit_concept": "EBIT", "rent": 50e6, "ebitr": 250e6, "interest": 250e6,
           "interest_concept": "us-gaap:InterestExpense + capitalised: none tagged (taken as 0)",
           "interest_capitalised": capitalised, "fixed_charges": 300e6}
    return [{"key": "overall", "columns": [{"score": 7}]},
            {"key": "icr", "title": "ICR", "short": "ICR", "rows": [], "bands": [],
             "columns": [col, {**col, "heading": "FY2024", "period": "2024-01-01--2024-12-31"}]}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _says(status, one_offs=(), swap=None, capitalised=None):
    return {"icr_t1": {"status": status, "one_offs": list(one_offs), "swap_mark_to_market": swap,
                       "capitalised_interest": capitalised, "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None):
    return icr_t1_trigger(_data(), metrics or _icr(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


CHARGE = {"item_id": "O1", "kind": "charge", "description": "Goodwill impairment",
          "evidence": _quote("I01", IMPAIRMENT, amount="$150 million")}
SALE = {"item_id": "O2", "kind": "gain", "description": "Gain on sale",
        "evidence": _quote("I02", GAIN, amount="$30 million")}
SWAP = {"direction": "loss", "evidence": _quote("I05", SWAP_LOSS, amount="$60 million"),
        "undesignated": _quote("I04", UNDESIGNATED)}


def test_the_agent_reads_mdna_and_the_notes():
    t = icr_t1_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("I01", "mdna", IMPAIRMENT), ("I02", "mdna", GAIN), ("I03", "mdna", FORECAST), ("I04", "notes", UNDESIGNATED),
        ("I05", "notes", SWAP_LOSS), ("I06", "notes", CAPITALISED)]
    assert all(x not in p["text"] for p in t["passages"] for x in (WAGES, TESTING, TAX))  # no term; boilerplate


def test_below_8_5_or_after_a_going_concern_fail_the_trigger_does_not_fire(db_session):
    low = icr_t1_trigger(_data(), _icr(score=7.5), ENABLED)
    assert (low["status"], low["fired"], low["reason"]) == (
        "not_triggered", False, "ICR scored 7.5, below 8.5: trigger ICR T1 not set off")
    skipped = icr_t1_trigger(_data(), _icr(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False) and apply_icr_t1(_icr(), skipped) == _icr()
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fired_trigger_needs_the_agent_and_without_text_nothing_is_found(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        icr_t1_trigger(_data(), _icr(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'icr_t1' response"):
        icr_t1_trigger(_data(), _icr(), ENABLED, backend=scripted_backend({"dscr_t1": {}}), mode="scripted")
    nothing = _data()
    nothing["document"] = []
    result = icr_t1_trigger(nothing, _icr(), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("nothing_found", "no_text", True)


def test_one_offs_and_the_swap_mark_to_market_are_both_corrected(db_session):
    result = _run(_says("adjusted", [CHARGE, SALE], SWAP), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("adjusted", False, False)
    assert (result["ebit_change"], result["interest_change"]) == (120e6, -60e6)  # +150 charge − 30 gain; − 60 loss
    metrics = _icr()
    _, m = apply_icr_t1(metrics, result)
    col = m["columns"][0]
    # (200 + 120 + 50) / (250 − 60 + 50) = 370 / 240 = 1.54x: 7.5.
    assert (col["ebit_reported"], col["ebit"], col["interest_reported"], col["interest"]) == (
        200e6, 320e6, 250e6, 190e6)
    assert col["ratio"] == pytest.approx(370 / 240) and col["score"] == 7.5 and m["columns"][1]["score"] == 10
    assert metrics[1]["columns"][0]["ebit"] == 200e6  # the input is untouched
    assert m["trigger_summary"] == "trigger ICR T1: adjusted" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"icr_t1": result}) == []
    assert [r["label"] for r in measure_card(m)["result_rows"]] == [
        "ICR", "ICR as calculated (before trigger ICR T1)", "Risk score as calculated (before trigger ICR T1)",
        "Risk score (after trigger ICR T1)"]


def test_a_swap_counts_only_when_undesignated_and_in_interest_expense(db_session):
    no_proof = _run(_says("adjusted", swap={**SWAP, "undesignated": None}), db_session)
    assert no_proof["agent"]["held_back"][0]["reason"] == ("no verified quote says the swaps are not designated as "
                                                           "hedges")
    assert (no_proof["status"], no_proof["unresolved"]) == ("nothing_found", True)
    gain = _run(_says("adjusted", swap={**SWAP, "direction": "gain"}), db_session)  # a gain goes back into interest
    assert gain["interest_change"] == 60e6
    assert apply_icr_t1(_icr(), gain)[1]["columns"][0]["interest"] == 310e6


def test_nothing_found_escalates_icr(db_session):
    result = _run(_says("nothing_found"), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("nothing_found", True, False)
    metrics = apply_icr_t1(_icr(), result)
    m = metrics[1]
    assert m["columns"][0]["score"] == 10 and m["trigger_escalated"]
    assert "Unresolved: nothing found to explain the weak cover." in m["trigger_note"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"icr_t1": result})
    assert (e["id"], e["measure"], e["engine_score"], e["calculated_score"]) == ("icr_t1:icr", "icr", 10, 10)
    assert "disagreement" not in e and e["reason"].startswith("ICR scored 10 in FY2025: Nothing found")
    assert icr_t1_card(result)["label"] == "NOTHING FOUND: UNRESOLVED"


def test_a_disagreement_escalates_icr_even_with_an_adjustment(db_session):
    result = _run(_says("nothing_found", [CHARGE]), db_session)  # the agent says nothing found; a charge holds up
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("adjusted", True, True)
    metrics = apply_icr_t1(_icr(), result)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"icr_t1": result})
    assert e["disagreement"] and "the agent read nothing found; the verified evidence gives adjusted" in e["reason"]
    assert e["engine_score"] == 8.5  # (350 + 50) / 300 = 1.33x


def test_capitalised_interest_is_a_cross_check_only(db_session):
    found = _quote("I06", CAPITALISED, amount="$25 million")
    untagged = _run(_says("nothing_found", capitalised=found), db_session, metrics=_icr(capitalised=0.0))
    assert any("Capitalised interest of 25.0 million is disclosed (I06) but not tagged" in n for n in untagged["notes"])
    assert (untagged["status"], untagged["interest_change"]) == ("nothing_found", 0.0)  # not added
    tagged = _run(_says("nothing_found", capitalised=found), db_session, metrics=_icr(capitalised=25e6))
    assert not any("Capitalised interest" in n for n in tagged["notes"])


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    forecast = {**CHARGE, "evidence": _quote("I03", FORECAST, amount="$40 million")}
    wrong_kind = {**SALE, "kind": "charge", "evidence": _quote("I02", "We recognized a gain on the sale of our "
                                                                      "Denver facility of $30 million",
                                                           amount="$30 million")}
    no_one_off = {**CHARGE, "item_id": "O3", "evidence": _quote("I05", SWAP_LOSS, amount="$60 million")}
    result = _run(_says("adjusted", [forecast, wrong_kind, no_one_off]), db_session)
    assert [(h["item_id"], h["reason"]) for h in result["agent"]["held_back"]] == [
        ("O1", "the quote reads as a forecast, not the year's figure"),
        ("O2", "the quote doesn't show a charge or loss"),
        ("O3", "the quote names no one-off item (impairment, restructuring, litigation, a gain or loss on a sale…)")]
    assert (result["status"], result["unresolved"]) == ("nothing_found", True)
