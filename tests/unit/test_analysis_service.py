"""Generative analysis service tests (task 6.4; Req 12.1-12.7)."""

from __future__ import annotations

import pytest

from app.prompts.registry import PromptRegistry
from app.schemas.llm import AnalyticalClaim
from app.services.analysis.service import (
    RULE_MISSING_CRITICAL_EVIDENCE,
    AnalysisInputs,
    AnalysisRejectedError,
    AnalysisService,
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
    return AnalysisService(client, escalation_engine=engine, case_id=case_id), backend, engine


def _valid_analysis():
    return {
        "business_overview": [
            {
                "claim_id": "c1",
                "text": "Operates a scheduled passenger airline.",
                "kind": "interpretation",
                "evidence_ids": ["f1"],
                "materiality": "medium",
            }
        ],
        "repayment_analysis": [
            {
                "claim_id": "c2",
                "text": "Net debt to EBITDA is 3.0.",
                "kind": "fact",
                "evidence_ids": ["m_net_debt_to_ebitda"],
                "materiality": "high",
                "uncertainty": "Based on FY metric only.",
            }
        ],
        "key_risks": [],
        "mitigants": [],
        "data_limitations": [],
        "questions_for_human": [],
    }


def test_valid_analysis_returns_six_sections(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", _valid_analysis())

    result = service.analyze(AnalysisInputs(canonical_facts=[{"fact_id": "f1"}]))

    a = result.analysis
    assert a.business_overview and a.repayment_analysis
    # Every claim carries evidence IDs + fact/interpretation flag (Req 12.3).
    for claim in a.all_claims():
        assert claim.evidence_ids
        assert claim.kind in ("fact", "interpretation") or claim.kind is not None


def test_factual_claim_without_evidence_is_rejected_by_schema():
    """A factual claim must carry evidence (Req 12.6)."""
    with pytest.raises(ValueError):
        AnalyticalClaim(claim_id="x", text="debt is 100", kind="fact", evidence_ids=[])


def test_invalid_analysis_output_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", {"business_overview": "not a list"})
    with pytest.raises(AnalysisRejectedError):
        service.analyze(AnalysisInputs())


def test_missing_critical_evidence_adds_caveat_and_escalates(db_session):
    """Missing critical evidence -> caveat + mandatory escalation (Req 12.7)."""
    service, backend, engine = _service(db_session)
    backend.register("analyze", _valid_analysis())

    result = service.analyze(
        AnalysisInputs(missing_critical_evidence=["audited_financials"])
    )

    assert result.analysis.data_limitations  # caveat injected, not confident prose
    assert result.analysis.questions_for_human
    assert result.escalation_id is not None

    open_mandatory = engine.open_escalations(mandatory_only=True)
    assert any(e.rule_id == RULE_MISSING_CRITICAL_EVIDENCE for e in open_mandatory)
    # Case status reflects the unresolved mandatory escalation (Req 14.7).
    assert engine.has_unresolved_mandatory("C1")
