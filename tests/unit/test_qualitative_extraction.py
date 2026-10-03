"""AI qualitative extraction tests (task 6.3; Req 3.3, 3.4, 3.6)."""

from __future__ import annotations

import pytest

from app.prompts.registry import PromptRegistry
from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.llm import ExtractedQualitativeFact
from app.services.audit.log import AuditLog
from app.services.extraction.qualitative import QualitativeExtractor
from app.services.llm.client import FakeLLMBackend, LLMClient


def _extractor(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    return QualitativeExtractor(client, audit=audit, case_id="C1"), backend


def _payload(status="unverified"):
    return {
        "facts": [
            {
                "fact_id": "q1",
                "name": "Ownership structure",
                "topic": "ownership",
                "statement": "Widely held; no single >5% holder.",
                "status": status,
                "confidence": 0.7,
                "source_refs": [{"document_id": "10K", "page": 12}],
            }
        ]
    }


def test_extracted_facts_are_llm_method_and_never_verified(db_session):
    extractor, backend = _extractor(db_session)
    backend.register("extract", _payload())

    facts, run = extractor.extract({}, evidence_ids=["10K"])

    assert run.is_valid
    assert len(facts) == 1
    fact = facts[0]
    assert fact.extraction_method is ExtractionMethod.LLM
    assert fact.status is FactStatus.UNVERIFIED
    assert fact.status is not FactStatus.VERIFIED
    assert fact.source_refs  # >=1 source ref attached (Req 3.5)


def test_schema_forbids_ai_fact_claiming_verified():
    """The response schema itself rejects an AI fact marked 'verified' (Req 3.4)."""
    with pytest.raises(ValueError):
        ExtractedQualitativeFact(
            fact_id="q1",
            name="x",
            topic="management",
            statement="s",
            status="verified",
            source_refs=[{"document_id": "d"}],
        )


def test_fact_without_source_ref_is_rejected():
    with pytest.raises(ValueError):
        ExtractedQualitativeFact(
            fact_id="q1",
            name="x",
            topic="management",
            statement="s",
            status="unverified",
            source_refs=[],
        )


def test_invalid_llm_output_yields_no_facts(db_session):
    extractor, backend = _extractor(db_session)
    backend.register("extract", {"facts": [{"fact_id": "q1"}]})  # invalid shape
    facts, run = extractor.extract({})
    assert facts == []
    assert not run.is_valid


def test_fact_extracted_audit_events_emitted(db_session):
    extractor, backend = _extractor(db_session)
    backend.register("extract", _payload())
    extractor.extract({})
    audit = AuditLog(db_session)
    types = [e.event_type for e in audit.events_for_case("C1")]
    assert "fact_extracted" in types
