"""FO Trigger Agent, DSO T1: when it fires (|change in DSO| of 5 days or more, either way; agent triggers enabled), the
passages it reads (MD&A, the notes and subsequent events), the verified evidence, the outcome (first match: a quantified
mechanical cause is added back in both years and DSO recomputed; DSO up with a cause and collections recovered after
the year-end is −1; DSO up with a cause and no fix, or a mechanical cause not quantified, is unresolved; no cause gets
no adjustment), the rescoring of DSO and its escalation.

Synthetic 10-K text and a scored DSO measure only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.dso_triggers import apply_dso_t1, dso_t1_passages, dso_t1_trigger
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import dso_t1_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_dso

FY25, FY24 = "2025-01-01--2025-12-31", "2024-01-01--2024-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
SOLD = ("During 2025, we sold $100 million of patient accounts receivable under a factoring arrangement accounted for "
        "as a sale under ASC 860.")
SOLD_PRIOR = "At December 31, 2024, accounts receivable of $50 million had been sold and derecognised."
SECURED = ("Our receivables securitization facility is accounted for as a secured borrowing, and receivables of $200 "
           "million remain on our consolidated balance sheet.")
WRITE_OFF = "In 2025, we wrote off $80 million of receivables owed by a health plan that filed for bankruptcy."
ROUTINE = "Write-offs of uncollectible accounts receivable were $30 million in 2025, consistent with prior years."
MENTION = "From time to time we sell certain accounts receivable to third parties."
DISPUTE = ("Days sales outstanding increased because of payment disputes and claim denials by a large commercial "
           "payer, which slowed the collection of our receivables.")
PLAN = "We expect collections of these receivables to improve during 2026."
RECOVERED = "In January 2026, the disputed claims were resolved and the related receivables were collected in full."
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D_dso": 3.0},
            "benchmarks": {"days_sales_outstanding": 0.0}, "sector": "Hospitals/Healthcare Facilities"}


def _data(subsequent=RECOVERED):
    mdna = ["Liquidity and Capital Resources", SOLD, SOLD_PRIOR, SECURED, MENTION, DISPUTE, PLAN]
    notes = ["NOTE 4 — ACCOUNTS RECEIVABLE", WRITE_OFF, ROUTINE, "NOTE 15 — SUBSEQUENT EVENTS", subsequent]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _cols(receivables=600e6, prior_receivables=500e6, revenue=3650e6, prior_revenue=3650e6):
    """DSO 600 / (3,650 / 365) = 60 days against 500 / 10 = 50 days: up 10 days."""
    prior = {"heading": "FY2024", "period": FY24, "basis": "historical", "as_at": "2024-12-31",
             "receivables": prior_receivables, "revenue": prior_revenue,
             "dso": prior_receivables / (prior_revenue / 365), "prior_dso": None, "change": None,
             "reason": "No prior-year DSO: add the prior year's 10-K as a comparison year"}
    dso = receivables / (revenue / 365)
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "as_at": "2025-12-31",
           "receivables": receivables, "revenue": revenue, "dso": dso, "prior_dso": prior["dso"],
           "change": dso - prior["dso"], "reason": None}
    for c in (col, prior):
        score_dso(c, SETTINGS)
    return [col, prior]


def _dso(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "dso", "title": "Days sales outstanding (DSO)", "short": "DSO change",
             "rows": [("receivables", "Net receivables"), ("revenue", "Net revenue")], "columns": _cols(**kw),
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D": 5.0}}]


def _quote(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _mech(item_id, kind, year, pid, quote, amount, unusual=None):
    return {"item_id": item_id, "kind": kind, "year": year, "description": kind,
            "evidence": _quote(pid, quote, amount=amount), "unusual_evidence": unusual}


def _says(status, mechanical=(), unquantified=(), operational=(), recovery=None):
    return {"dso_t1": {"status": status, "mechanical": list(mechanical), "mechanical_unquantified": list(unquantified),
                       "operational": list(operational), "recovery": recovery, "rationale": "Scripted.",
                       "missing_information": []}}


def _run(response, db_session, metrics=None, data=None):
    return dso_t1_trigger(data or _data(), metrics or _dso(), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def _pid(text, data=None):
    return next(p["passage_id"] for p in dso_t1_passages(data or _data())["passages"] if p["text"] == text)


DISPUTES = {"item_id": "C1", "kind": "payer_dispute", "description": "Payer disputes",
            "evidence": _quote("S05", DISPUTE)}


def test_the_agent_reads_mdna_notes_and_every_subsequent_events_paragraph():
    t = dso_t1_passages(_data())
    assert [(p["passage_id"], p["section"], p["text"]) for p in t["passages"]] == [
        ("S01", "mdna", SOLD), ("S02", "mdna", SOLD_PRIOR), ("S03", "mdna", SECURED), ("S04", "mdna", MENTION),
        ("S05", "mdna", DISPUTE), ("S06", "mdna", PLAN), ("S07", "notes", WRITE_OFF), ("S08", "notes", ROUTINE),
        ("S09", "subsequent_events", RECOVERED)]


def test_a_small_change_a_going_concern_fail_or_no_score_do_not_fire(db_session):
    small = dso_t1_trigger(_data(), _dso(receivables=540e6), ENABLED)  # 54 vs 50 days: +4
    assert (small["status"], small["fired"]) == ("not_triggered", False) and "less than 5" in small["reason"]
    skipped = dso_t1_trigger(_data(), _dso(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert apply_dso_t1(_dso(), skipped) == _dso()
    unscored = _dso()
    unscored[1]["columns"][0]["score"] = None
    assert dso_t1_trigger(_data(), unscored, ENABLED)["status"] == "not_triggered"
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_fall_of_5_days_fires_too(db_session):
    result = dso_t1_trigger({**_data(), "document": []}, _dso(receivables=450e6), ENABLED)  # 45 vs 50 days
    assert result["fired"] and result["fired_by"] == "DSO down 5.0 days (5 or more)"
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("no_cause", "no_text", False)


def test_a_fired_trigger_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        dso_t1_trigger(_data(), _dso(), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'dso_t1' response"):
        dso_t1_trigger(_data(), _dso(), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")


def test_receivables_sold_are_added_back_in_both_years_and_dso_recomputed(db_session):
    sold = _mech("M1", "receivables_sold", "current", "S01", SOLD, "$100 million")
    sold_prior = _mech("M2", "receivables_sold", "prior", "S02", SOLD_PRIOR, "$50 million")
    result = _run(_says("recomputed", [sold, sold_prior], operational=[DISPUTES]), db_session)
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("recomputed", False, False)
    assert (result["added"], result["prior_added"]) == (100e6, 50e6)
    assert any("a quantified mechanical cause comes first" in n for n in result["notes"])
    metrics = _dso()
    m = apply_dso_t1(metrics, result)[1]
    col, prior = m["columns"]
    assert (col["receivables_before_trigger"], col["receivables"]) == (600e6, 700e6)
    assert (prior["receivables_before_trigger"], prior["receivables"]) == (500e6, 550e6)
    assert (col["dso"], col["prior_dso"], col["change"]) == pytest.approx((70.0, 55.0, 15.0))
    assert col["score"] == _cols(receivables=700e6, prior_receivables=550e6)[0]["score"]
    assert col["dso_adjustment"]["calculated"] == metrics[1]["columns"][0]["score"]
    assert metrics[1]["columns"][0]["receivables"] == 600e6  # the input is untouched
    assert m["trigger_summary"] == "trigger DSO T1: mechanical items added back" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"dso_t1": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert labels == ["DSO", "Prior-year DSO", "Change in DSO (scored)",
                      "Change in DSO as calculated (before trigger DSO T1)",
                      "Risk score as calculated (before trigger DSO T1)", "Risk score (after trigger DSO T1)"]
    assert "Net receivables as reported (before trigger DSO T1)" in [r["label"] for r in measure_card(m)["value_rows"]]
    assert dso_t1_card(result)["label"] == "MECHANICAL: RECOMPUTED"


def test_a_secured_borrowing_stays_in_receivables(db_session):
    secured = _mech("M1", "receivables_sold", "current", "S03", SECURED, "$200 million")
    result = _run(_says("recomputed", [secured]), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "a securitisation accounted for as a secured borrowing stays in receivables"]
    assert (result["status"], result["disagreement"], result["unresolved"]) == ("no_cause", True, True)


def test_an_unusual_write_off_is_reversed_but_a_routine_one_is_not(db_session):
    unusual = _mech("M1", "write_off", "current", "S07", WRITE_OFF, "$80 million", _quote("S07", WRITE_OFF))
    result = _run(_says("recomputed", [unusual]), db_session)
    assert (result["status"], result["added"], result["prior_added"]) == ("recomputed", 80e6, 0.0)
    m = apply_dso_t1(_dso(), result)[1]
    assert m["columns"][0]["dso"] == pytest.approx(68.0) and m["columns"][0]["prior_dso"] == pytest.approx(50.0)
    routine = _mech("M1", "write_off", "current", "S08", ROUTINE, "$30 million", _quote("S08", ROUTINE))
    held = _run(_says("no_cause", [routine]), db_session)
    assert [h["reason"] for h in held["agent"]["held_back"]] == ["the quote doesn't show the write-off is unusual"]
    assert (held["status"], held["unresolved"]) == ("no_cause", False)


def test_dso_up_with_a_cause_and_collections_recovered_after_the_year_end_is_minus_one(db_session):
    result = _run(_says("recovered", operational=[DISPUTES], recovery=_quote("S09", RECOVERED)), db_session)
    assert (result["status"], result["unresolved"], result["relief"]) == ("recovered", False, 1)
    metrics = _dso()
    m = apply_dso_t1(metrics, result)[1]
    calculated = metrics[1]["columns"][0]["score"]
    assert m["columns"][0]["score"] == max(1.0, round(calculated - 1, 1))
    assert m["columns"][0]["dso"] == metrics[1]["columns"][0]["dso"]  # the ratio is unchanged
    assert m["trigger_summary"] == "trigger DSO T1: −1, collections recovered"
    assert dso_t1_card(result)["label"] == "RECOVERED: −1"


def test_dso_up_with_a_cause_and_only_a_plan_is_unresolved(db_session):
    result = _run(_says("unresolved", operational=[DISPUTES], recovery=_quote("S06", PLAN)), db_session)
    assert [h["reason"] for h in result["agent"]["held_back"]] == [
        "the quote doesn't show the recovery came after the year-end"]
    assert (result["status"], result["unresolved"], result["disagreement"]) == ("unresolved", True, False)
    assert result["unresolved_why"].startswith("DSO went up with a cause described (C1)")
    metrics = apply_dso_t1(_dso(), result)
    assert metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"dso_t1": result})
    assert (e["id"], e["measure"], e["measure_name"]) == ("dso_t1:dso", "dso", "Days sales outstanding (DSO)")


def test_operational_causes_count_only_when_dso_went_up(db_session):
    down = _dso(receivables=400e6)  # 40 vs 50 days
    result = _run(_says("no_cause", operational=[DISPUTES]), db_session, down)
    assert (result["status"], result["unresolved"]) == ("no_cause", False)
    assert any("count only when DSO went up" in n for n in result["notes"])


def test_a_mechanical_cause_mentioned_but_not_quantified_is_unresolved(db_session):
    mention = {"item_id": "U1", "kind": "receivables_sold", "description": "Sales of receivables",
               "evidence": _quote("S04", MENTION)}
    result = _run(_says("unresolved", unquantified=[mention]), db_session)
    assert (result["status"], result["unresolved"]) == ("unresolved", True)
    assert result["unresolved_why"] == "a mechanical cause is mentioned but not quantified (U1)"


def test_no_cause_found_gets_no_adjustment(db_session):
    result = _run(_says("no_cause"), db_session)
    assert (result["status"], result["unresolved"]) == ("no_cause", False)
    m = apply_dso_t1(_dso(), result)[1]
    assert m["columns"][0]["score"] == _dso()[1]["columns"][0]["score"]
    assert m["trigger_summary"] == "trigger DSO T1: no cause found"
