"""FO going-concern pre-check: passage collection, the keyword rules, and the agent's validated findings.

Synthetic 10-K text only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.precheck import (
    PrecheckAgentError,
    going_concern_passages,
    going_concern_precheck,
    rule_status,
    scripted_backend,
)
from app.services.llm.client import FakeLLMBackend

RISK = ("If we are unable to refinance our debt, there could be substantial doubt about our ability to continue "
        "as a going concern.")
BASIS = ("The accompanying consolidated financial statements have been prepared assuming that the Company will "
         "continue as a going concern.")
DOUBT = ("The Company has suffered recurring losses from operations and has a net capital deficiency that raise "
         "substantial doubt about its ability to continue as a going concern.")
ALLEVIATED = ("Management concluded that its plans alleviate the substantial doubt about the Company's ability to "
              "continue as a going concern.")
FUNDED = ("We believe that our cash and available borrowings will be sufficient to fund our operations for at least "
          "the next 12 months.")


TENURE = "We have served as the Company's auditor since 2010."
OPINION = ("In our opinion, the consolidated financial statements present fairly, in all material respects, the "
           "financial position of the Company.")


def _data(*, auditor=(), item7=(), item1a=(RISK,), notes=(), report=True):
    lines = ["Report of Independent Registered Public Accounting Firm", "To the Shareholders and the Board.",
             *auditor, TENURE] if report else []
    return {
        "meta": {"entity_name": "Example Co", "period_end": "2025-12-31", "fiscal_year": "2025"},
        "facts": [],
        "document": [
            {"item": "1A", "title": "Risk Factors", "text": "\n".join(item1a)},
            {"item": "7", "title": "MD&A", "text": "\n".join([*item7, FUNDED])},
            {"item": "8", "title": "Financial Statements", "text": "\n".join([*lines, *notes])},
        ],
    }


def _finding(pid, cls, quote):
    return {"passage_id": pid, "classification": cls, "quote": quote}


def test_the_whole_auditor_report_is_read_and_mentions_carry_their_section():
    gc = going_concern_passages(_data(auditor=[OPINION, DOUBT]))
    assert [(p["passage_id"], p["text"]) for p in gc["passages"]["auditor"]] == [
        ("A01", OPINION), ("A02", DOUBT), ("A03", TENURE)]  # every paragraph, not only the mentions
    assert [(p["passage_id"], p["item"], p["section"]) for p in gc["passages"]["management"]] == [
        ("M01", "1A", "risk_factors")]
    assert [p["passage_id"] for p in gc["passages"]["funding"]] == ["F01"]
    assert gc["audit_reports"] == ["8"]


def test_the_searched_sections_are_located_and_labelled():
    gc = going_concern_passages(_data(
        item7=["Liquidity and Capital Resources", ALLEVIATED, "Results of Operations", DOUBT],
        notes=["NOTE 1. BASIS OF PRESENTATION", BASIS, "NOTE 2. GOING CONCERN AND LIQUIDITY",
               "These conditions raise substantial doubt about the Company's ability to continue as a going concern.",
               "NOTE 25. SUBSEQUENT EVENTS",
               "On February 10, 2026 the Company refinanced its notes, which alleviates the substantial doubt."]))
    sections = [(p["item"], p["section"]) for p in gc["passages"]["management"]]
    assert sections == [("1A", "risk_factors"), ("7", "mdna_liquidity"), ("7", "other"),
                        ("8", "basis_of_presentation"), ("8", "going_concern_note"), ("8", "subsequent_events")]
    located = {s["key"]: s["located"] for s in gc["sections"]}
    assert located == {"going_concern_note": True, "basis_of_presentation": True, "mdna_liquidity": True,
                       "subsequent_events": True, "auditor_report": True}


def test_keyword_rules_pass_watch_and_fail():
    assert rule_status(going_concern_passages(_data())["passages"])[0] == "pass"  # a risk factor never counts
    assert rule_status(going_concern_passages(_data(notes=[BASIS]))["passages"])[0] == "pass"  # basis wording
    assert rule_status(going_concern_passages(_data(item7=[ALLEVIATED]))["passages"])[:1] == ("watch",)
    status, reason, pid, sentence = rule_status(going_concern_passages(_data(auditor=[OPINION, DOUBT]))["passages"])
    assert (status, pid, sentence) == ("fail", "A02", DOUBT) and "auditor" in reason


def test_without_an_agent_the_check_is_refused_the_rules_never_decide():
    with pytest.raises(PrecheckAgentError, match="Pre-check Agent is required"):
        going_concern_precheck(_data(auditor=[DOUBT]))
    only_covenants = scripted_backend({"covenants": {"status": "compliant"}})
    with pytest.raises(PrecheckAgentError, match="no 'going_concern' response"):
        going_concern_precheck(_data(auditor=[DOUBT]), backend=only_covenants, mode="scripted")


def test_no_report_and_no_mentions_is_a_pass_without_calling_the_agent(db_session):
    data = _data(report=False,
                 item1a=["Our business depends on the volume of patients we treat in our hospitals each year."])
    result = going_concern_precheck(data, session=db_session, backend=FakeLLMBackend(), mode="scripted")
    assert (result["status"], result["decided_by"], result["agent"]) == ("pass", "no_mentions", None)
    assert db_session.scalars(select(ModelRun)).first() is None  # FakeLLMBackend has no script: never called
    assert any("auditor's report was not located" in n for n in result["notes"])


def test_the_agent_always_reads_the_auditor_report(db_session):
    backend = scripted_backend({"status": "pass", "rationale": "Clean opinion; no going-concern paragraph.",
                                "missing_information": [], "findings": []})
    data = _data(auditor=[OPINION], item1a=["Our business depends on the volume of patients we treat each year."])
    result = going_concern_precheck(data, session=db_session, backend=backend, mode="scripted")
    assert (result["status"], result["decided_by"]) == ("pass", "agent")  # no mention, but the report was read
    assert [p["kind"] for p in result["agent"]["inputs"]["passages"]] == ["auditor_report", "auditor_report",
                                                                          "funding_statement"]


def test_going_concern_basis_wording_cannot_carry_a_watch_or_fail(db_session):
    no_doubt = "Management concluded that there is no substantial doubt about the Company's ability to continue."
    backend = scripted_backend({"status": "fail", "rationale": "x", "missing_information": [], "findings": [
        _finding("M02", "management_doubt_not_alleviated", BASIS),  # M01 is the risk factor
        _finding("M03", "management_doubt_not_alleviated", no_doubt)]})
    result = going_concern_precheck(_data(notes=[BASIS, no_doubt]), session=db_session, backend=backend,
                                    mode="scripted")
    assert result["status"] == "pass"
    reasons = [h["reason"] for h in result["agent"]["held_back"]]
    assert "expresses no substantial doubt" in reasons[0] and "no substantial doubt exists" in reasons[1]


def test_agent_fail_on_an_auditor_going_concern_paragraph(db_session):
    backend = scripted_backend({"status": "fail", "rationale": "The auditor's report has a going-concern paragraph.",
                                "missing_information": [],
                                "findings": [_finding("A02", "auditor_going_concern_paragraph", DOUBT),
                                             _finding("M01", "hypothetical_or_risk_factor", RISK)]})
    result = going_concern_precheck(_data(auditor=[OPINION, DOUBT]), session=db_session, backend=backend,
                                    mode="scripted", case_id="EX_FY2025")
    assert (result["status"], result["decided_by"]) == ("fail", "agent")
    assert result["review_required"] and "auditor" in result["reason"]
    assert "Stop" in result["effect"] and "agent triggers are skipped" in result["effect"]
    assert result["rules"]["status"] == "fail"  # the cross-check agrees
    assert [b["passage_id"] for b in result["basis"]] == ["A02"]
    agent = result["agent"]
    assert agent["validation_outcome"] == "valid" and agent["held_back"] == [] and agent["status_consistent"]
    run = db_session.get(ModelRun, agent["model_run_id"])
    assert run.prompt_name == "fo_precheck_going_concern" and run.case_id == "EX_FY2025"
    assert {p["passage_id"] for p in agent["inputs"]["passages"]} == {"A01", "A02", "A03", "M01", "F01"}
    assert next(p for p in agent["inputs"]["passages"] if p["passage_id"] == "M01")["section"] == "Risk factors (Item 1A)"


def test_agent_watch_when_management_says_doubt_is_alleviated(db_session):
    backend = scripted_backend({"status": "watch", "rationale": "Alleviated by plans.", "missing_information": [],
                                "findings": [_finding("M02", "management_doubt_alleviated", ALLEVIATED)]})
    result = going_concern_precheck(_data(item7=[ALLEVIATED]), session=db_session, backend=backend, mode="scripted")
    assert (result["status"], result["decided_by"], result["review_required"]) == ("watch", "agent", False)
    assert "liquidity-and-maturity score is floored at 7 before weighting" in result["effect"]


def test_unverifiable_findings_are_held_back_and_the_status_is_rederived(db_session):
    backend = scripted_backend({"status": "fail", "rationale": "x", "missing_information": [], "findings": [
        _finding("A01", "auditor_going_concern_paragraph", "conditions raise substantial doubt about solvency"),
        _finding("M01", "management_doubt_not_alleviated", RISK),  # a risk factor is no conclusion
        _finding("F01", "auditor_going_concern_paragraph", FUNDED),  # not the auditor's report
        _finding("Z99", "management_doubt_not_alleviated", DOUBT),  # never supplied
    ]})
    result = going_concern_precheck(_data(auditor=[DOUBT]), session=db_session, backend=backend, mode="scripted")
    reasons = [h["reason"] for h in result["agent"]["held_back"]]
    assert len(reasons) == 4 and "not verbatim" in reasons[0] and "Item 1A" in reasons[1]
    assert "auditor passages" in reasons[2] and "not supplied" in reasons[3]
    assert (result["status"], result["agent"]["stated_status"]) == ("pass", "fail")
    assert any("verified findings decide" in n for n in result["notes"])
    assert any("keyword rules read this filing as FAIL" in n for n in result["notes"])  # flagged for the analyst


def test_rejected_agent_output_refuses_the_check(db_session):
    backend = FakeLLMBackend()
    backend.register("analyze", "not json", key="fo_precheck_going_concern")
    with pytest.raises(PrecheckAgentError, match="output was rejected .*the run was not recorded"):
        going_concern_precheck(_data(auditor=[DOUBT]), session=db_session, backend=backend, mode="scripted")
    assert db_session.scalars(select(ModelRun)).one().validation_outcome == "rejected"  # still logged
