"""Challenge layer tests (task 6.5; Req 13.1-13.5)."""

from __future__ import annotations

import pytest

from app.prompts.registry import PromptRegistry
from app.schemas.llm import AnalysisResponse
from app.services.analysis.challenge import (
    RULE_HIGH_SEVERITY_CHALLENGE,
    ChallengeRejectedError,
    ChallengeService,
)
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.llm.client import FakeLLMBackend, LLMClient


def _service(db_session, case_id="C1"):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    engine = EscalationEngine(db_session, audit=audit, case_id=case_id)
    return (
        ChallengeService(client, escalation_engine=engine, audit=audit, case_id=case_id),
        backend,
        engine,
        audit,
    )


def _analysis():
    return AnalysisResponse.model_validate(
        {
            "business_overview": [
                {
                    "claim_id": "c1",
                    "text": "Strong market position.",
                    "kind": "interpretation",
                    "evidence_ids": ["f1"],
                }
            ],
            "repayment_analysis": [],
            "key_risks": [],
            "mitigants": [],
            "data_limitations": [],
            "questions_for_human": [],
        }
    )


def _challenges(severity="high"):
    return {
        "challenges": [
            {
                "claim_id": "c1",
                "issue_type": "overstated_certainty",
                "severity": severity,
                "reason": "Certainty overstated given single data point.",
                "evidence_refs": ["f1"],
            }
        ]
    }


def test_challenge_is_non_destructive(db_session):
    """The challenge pass never mutates the original analysis (Req 13.3)."""
    service, backend, _, _ = _service(db_session)
    backend.register("challenge", _challenges(severity="medium"))

    analysis = _analysis()
    before = analysis.model_dump(mode="json")
    outcome = service.challenge(analysis)

    assert analysis.model_dump(mode="json") == before  # unchanged
    assert len(outcome.challenges) == 1


def test_high_severity_challenge_enters_escalation_queue(db_session):
    """A high-severity challenge creates a mandatory escalation (Req 13.5)."""
    service, backend, engine, _ = _service(db_session)
    backend.register("challenge", _challenges(severity="high"))

    outcome = service.challenge(_analysis())

    assert outcome.escalation_ids
    open_mandatory = engine.open_escalations(mandatory_only=True)
    assert any(e.rule_id == RULE_HIGH_SEVERITY_CHALLENGE for e in open_mandatory)


def test_low_severity_challenge_does_not_escalate(db_session):
    service, backend, engine, _ = _service(db_session)
    backend.register("challenge", _challenges(severity="low"))
    outcome = service.challenge(_analysis())
    assert outcome.escalation_ids == []
    assert engine.open_escalations() == []


def test_accept_reject_decision_is_logged(db_session):
    service, backend, _, audit = _service(db_session)
    backend.register("challenge", _challenges(severity="medium"))
    outcome = service.challenge(_analysis())

    service.log_decision(
        outcome.challenges[0], decision="rejected", decided_by="analyst1"
    )
    events = [e.event_type for e in audit.events_for_case("C1")]
    assert "human_review" in events
    assert "challenge_created" in events


def test_invalid_challenge_output_is_rejected(db_session):
    service, backend, _, _ = _service(db_session)
    backend.register("challenge", {"challenges": [{"claim_id": "c1"}]})  # invalid
    with pytest.raises(ChallengeRejectedError):
        service.challenge(_analysis())
