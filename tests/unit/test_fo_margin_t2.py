"""FO Trigger Agent, EBITDAR Margin T2: when it fires (the margin scores 9 or more, or 2 or less; more than one
reportable segment; a segment with 30% or more of revenue whose margin is 5 points or more from all segments'), the
segment table code reads from the segment note's tags, the direction (the margin without the outlier, closer to p or
not), the agent's verified findings, the outcome (1 point toward p, never past; no adjustment; unresolved), and its
escalation.

Synthetic segment tags and 10-K text and a scored margin measure only; the agent runs on a scripted offline backend
(no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.margin_t2 import (
    apply_margin_t2,
    direction,
    find_outlier,
    margin_t2_passages,
    margin_t2_trigger,
    moved_score,
    segment_table,
)
from app.services.fo.parsing import LABEL_ROLE
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.reporting import margin_t2_card, measure_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_margin

FY25 = "2025-01-01--2025-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D": 5.0},
            "benchmarks": {"ebitdar_margin": 0.12}, "sector": "Hospitals/Healthcare Facilities"}
AXIS = "us-gaap:StatementBusinessSegmentsAxis"
OPERATING = {"srt:ConsolidationItemsAxis": "us-gaap:OperatingSegmentsMember"}
PROFIT = "ex:SegmentAdjustedEBITDA"

SAME = "The chief operating decision maker evaluates the performance of each reportable segment using segment EBITDA."
NOT_COMPARABLE = "Segment profit for Clinics is measured on a different basis than for Hospitals."
SINGLE = "We manage our operations as a single reportable segment."
BUSINESS = "The Clinics segment has a lower-margin business model because its revenue includes pass-through drug costs."
YEAR = "Clinics margins declined in 2025 due to lower volumes."
UNNAMED = "Segment margin there reflects a pass-through pricing structure."


def _fact(concept, value, member=None, period=FY25, **dims):
    return {"concept": concept, "period_key": period, "value": value, "type": "numeric", "unit": "USD",
            "dimensions": ({AXIS: member} if member else {}) | dims}


def _facts(hospitals=(3000e6, 300e6), clinics=(2000e6, -100e6), profit=PROFIT):
    out = []
    for member, (rev, prof) in (("ex:HospitalsMember", hospitals), ("ex:ClinicsMember", clinics)):
        out += [_fact("us-gaap:Revenues", rev, member, **OPERATING),
                _fact("us-gaap:NetIncomeLossAttributableToNoncontrollingInterest", 5e6, member),
                _fact("ex:SegmentLaborCostsExpense", rev / 2, member, **OPERATING),
                _fact("us-gaap:Revenues", rev / 3, member, **{"srt:ProductOrServiceAxis": "ex:DrugsMember"})]
        if profit and prof is not None:
            out.append(_fact(profit, prof, member, **OPERATING))
    return out + [_fact("us-gaap:Revenues", 400e6, "us-gaap:CorporateAndOtherMember"),
                  _fact("us-gaap:Revenues", 5400e6)]


LABELS = {"ex:HospitalsMember": {LABEL_ROLE: "Hospitals [Member]"},
          "ex:ClinicsMember": {LABEL_ROLE: "Clinics Segment [Member]"},
          PROFIT: {LABEL_ROLE: "Segment adjusted EBITDA"}}


def _data(facts=None, document=True):
    mdna = ["Results of Operations", BUSINESS, YEAR, UNNAMED]
    note = ["Note 12. Segment Information", SAME, NOT_COMPARABLE, SINGLE]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": _facts() if facts is None else facts, "labels": LABELS, "sections": [],
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(note)}] if document else []}


def _col(ebitdar=50e6, revenue=5000e6):
    col = {"heading": "FY2025", "period": FY25, "basis": "historical", "ebitdar": ebitdar, "revenue": revenue,
           "ratio": ebitdar / revenue, "reason": None}
    score_margin(col, SETTINGS)
    return col


def _margin(**kw):
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "margin", "title": "EBITDAR margin", "short": "EBITDAR margin", "rows": [], "columns": [_col(**kw)],
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D_dso": 3.0}}]


def _q(pid, quote):
    return {"passage_id": pid, "quote": quote}


def _says(status, measure=("same_measure", None), explanation=("none", None), segments=("multiple", None)):
    return {"margin_t2": {"status": status,
                          "reportable_segments": {"finding": segments[0], "evidence": segments[1]},
                          "profit_measure": {"finding": measure[0], "evidence": measure[1]},
                          "outlier_explanation": {"finding": explanation[0], "evidence": explanation[1]},
                          "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, data=None, metrics=None):
    return margin_t2_trigger(data or _data(), metrics or _margin(), ENABLED, session=db_session,
                             backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025")


def _pid(text):
    return next(p["passage_id"] for p in margin_t2_passages(_data(), ["Hospitals", "Clinics"])["passages"]
                if p["text"] == text)


def test_code_reads_the_reportable_segments_from_the_segment_note_tags():
    t = segment_table(_facts(), LABELS, FY25)
    assert [(s["name"], s["revenue"], s["profit"]) for s in t["segments"]] == [
        ("Hospitals", 3000e6, 300e6), ("Clinics", 2000e6, -100e6)]  # corporate, other axes and the total left out
    assert (t["profit_concept"], t["revenue"], t["profit"]) == (PROFIT, 5000e6, 200e6)
    assert t["margin"] == pytest.approx(0.04)
    o = find_outlier(t)  # both qualify (Hospitals +6 points); Clinics (−9 points) is further
    assert (o["name"], o["share"], o["rest_margin"]) == ("Clinics", 0.4, pytest.approx(0.10))
    standard = segment_table(_facts() + [_fact("us-gaap:OperatingIncomeLoss", 1, m, **OPERATING)
                                         for m in ("ex:HospitalsMember", "ex:ClinicsMember")], LABELS, FY25)
    assert standard["profit_concept"] == "us-gaap:OperatingIncomeLoss"  # a standard concept first
    assert segment_table(_facts(profit=None), LABELS, FY25)["profit_concept"] is None
    assert segment_table(_facts(), {}, FY25)["segments"][1]["name"] == "Clinics"  # no label: the member's name


def test_the_direction_compares_the_margin_without_the_outlier_with_p():
    col = _col()  # 1% margin, score 9.2
    for hospitals, clinics, want in (((3000e6, 300e6), (2000e6, -100e6), "closer"),  # without Clinics: 7%
                                     ((3000e6, 60e6), (2000e6, 400e6), "as_extreme"),  # without Clinics: −6.2%
                                     ((3250e6, 285e6), (1750e6, 15e6), "neither")):  # 3.8%: under a point closer
        t = segment_table(_facts(hospitals, clinics), LABELS, FY25)
        assert direction(col, SETTINGS, t, find_outlier(t))["direction"] == want
    assert (moved_score(9.2, 5), moved_score(1.6, 5), moved_score(5.6, 5), moved_score(4.5, 5)) == (8.2, 2.6, 5, 5)


def test_it_fires_only_at_an_extreme_score_with_a_dispersed_large_segment(db_session):
    mid = margin_t2_trigger(_data(), _margin(ebitdar=500e6), ENABLED)  # 10%: score 6.1
    assert (mid["status"], mid["decided_by"], mid["fired"]) == ("not_triggered", "score", False)
    one = margin_t2_trigger(_data([f for f in _facts() if "Clinics" not in str(f["dimensions"])]), _margin(), ENABLED)
    assert (one["status"], one["decided_by"]) == ("not_triggered", "segments") and "One reportable" in one["reason"]
    close = margin_t2_trigger(_data(_facts((3000e6, 180e6), (2000e6, 100e6))), _margin(), ENABLED)  # 6% and 5%
    assert (close["status"], close["fired"]) == ("not_triggered", False) and "5.6%" in close["reason"]
    skipped = margin_t2_trigger(_data(), _margin(), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        margin_t2_trigger(_data(), _margin(), ENABLED)
    no_text = margin_t2_trigger(_data(document=False), _margin(), ENABLED)
    assert (no_text["status"], no_text["decided_by"], no_text["unresolved"]) == ("unresolved", "no_text", True)
    no_profit = margin_t2_trigger(_data(_facts(profit=None)), _margin(), ENABLED)
    assert (no_profit["fired"], no_profit["status"], no_profit["decided_by"]) == (True, "unresolved", "segments")
    assert "no segment profit is tagged" in no_profit["unresolved_why"]
    assert margin_t2_card(no_profit)["agent_not_used"] == "Not called: no segment profit tagged for each segment."
    assert db_session.scalars(select(ModelRun)).first() is None


def test_the_agent_reads_the_segment_note_and_mdna():
    t = margin_t2_passages(_data(), ["Hospitals", "Clinics"])
    assert [(p["section"], p["text"]) for p in t["passages"]] == [
        ("mdna", BUSINESS), ("mdna", YEAR), ("mdna", UNNAMED), ("segment_note", SAME), ("segment_note", NOT_COMPARABLE),
        ("segment_note", SINGLE)]


def test_closer_and_explained_by_the_business_moves_one_point_toward_p(db_session):
    result = _run(_says("moved_toward_p", ("same_measure", _q(_pid(SAME), SAME)),
                        ("business_model", _q(_pid(BUSINESS), BUSINESS))), db_session)
    assert (result["status"], result["recomputed"], result["unresolved"]) == ("moved_toward_p", 8.2, False)
    assert result["inputs"]["outlier"]["name"] == "Clinics" and result["inputs"]["direction"] == "closer"
    metrics = _margin()
    m = apply_margin_t2(metrics, result)[1]
    col = m["columns"][0]
    assert (col["score"], col["ratio"]) == (8.2, 0.01)  # the ratio is unchanged
    assert col["margin_t2_adjustment"]["calculated"] == 9.2 and metrics[1]["columns"][0]["score"] == 9.2
    assert m["trigger_summary"] == "trigger EBITDAR Margin T2: 1 point toward p" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"margin_t2": result}) == []
    assert [r["label"] for r in measure_card(m)["result_rows"]] == [
        "EBITDAR margin", "Risk score as calculated (before trigger EBITDAR Margin T2)",
        "Risk score (after trigger EBITDAR Margin T2)"]
    card = margin_t2_card(result)
    assert card["label"] == "MOVED 1 POINT TOWARD P" and [s["outlier"] for s in card["segments"]] == [False, True]
    assert len(card["evidence"]) == 2


def test_as_extreme_or_explained_by_the_year_gets_no_adjustment(db_session):
    extreme = _data(_facts((3000e6, 60e6), (2000e6, 400e6)))
    result = _run(_says("no_adjustment", ("same_measure", _q(_pid(SAME), SAME))), db_session, data=extreme)
    assert (result["status"], result["unresolved"]) == ("no_adjustment", False)
    assert "just as extreme" in result["reason"]
    year = _run(_says("no_adjustment", ("same_measure", _q(_pid(SAME), SAME)),
                      ("year_performance", _q(_pid(YEAR), YEAR))), db_session)
    assert (year["status"], year["inputs"]["direction"]) == ("no_adjustment", "closer")
    col = apply_margin_t2(_margin(), year)[1]["columns"][0]
    assert col["score"] == 9.2 and col["margin_t2_adjustment"]["recomputed"] is None


def test_unresolved_cases_escalate_the_margin(db_session):
    single = _run(_says("unresolved", ("same_measure", _q(_pid(SAME), SAME)),
                        ("business_model", _q(_pid(BUSINESS), BUSINESS)), ("single", _q(_pid(SINGLE), SINGLE))),
                  db_session)
    assert single["unresolved_why"] == "the filing reports a single reportable segment"
    differ = _run(_says("unresolved", ("not_comparable", _q(_pid(NOT_COMPARABLE), NOT_COMPARABLE))), db_session)
    assert differ["unresolved_why"] == "segment profit isn't measured the same way across segments"
    unclear = _run(_says("unresolved", ("unclear", None), ("business_model", _q(_pid(BUSINESS), BUSINESS))),
                   db_session)
    assert "doesn't show segment profit is measured the same way" in unclear["unresolved_why"]
    unexplained = _run(_says("unresolved", ("same_measure", _q(_pid(SAME), SAME))), db_session)  # closer, none
    assert unexplained["unresolved_why"].startswith("the results don't point either way")
    metrics = apply_margin_t2(_margin(), unexplained)
    assert metrics[1]["columns"][0]["score"] == 9.2 and metrics[1]["trigger_escalated"]
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"margin_t2": unexplained})
    assert (e["id"], e["measure"], e["calculated_score"]) == ("margin_t2:margin", "margin", 9.2)


def test_explanations_must_name_the_outlier_and_say_what_they_claim(db_session):
    unnamed = _run(_says("moved_toward_p", ("same_measure", _q(_pid(SAME), SAME)),
                         ("business_model", _q(_pid(UNNAMED), UNNAMED))), db_session)
    assert unnamed["agent"]["held_back"][0]["reason"] == "the cited passage doesn't name the Clinics segment"
    assert (unnamed["status"], unnamed["disagreement"], unnamed["unresolved"]) == ("unresolved", True, True)
    claim = _run(_says("moved_toward_p", ("same_measure", _q(_pid(SAME), SAME)),
                       ("business_model", _q(_pid(YEAR), YEAR))), db_session)
    assert claim["agent"]["held_back"][0]["reason"] == "the quote doesn't explain the margin by how the business works"
    single = _run(_says("unresolved", ("same_measure", _q(_pid(SAME), SAME)), ("none", None),
                        ("single", _q(_pid(SAME), SAME))), db_session)
    assert single["agent"]["held_back"][0]["fact"] == "single" and single["findings"]["reportable_segments"] == "multiple"
