"""Seeded AI-vs-deterministic conflict + grounding tests (task 6.6; Req 23.9, 23.10).

These drive the Milestone 4 reconciler with ONE AI-extracted fact + ONE
deterministic fact and assert the conflict is detected, routed to the Milestone 5
escalation engine, and the values are NEVER merged. Grounding presence/entailment
is measured SEPARATELY (Req 23.10).
"""

from __future__ import annotations

from app.core.config_registry import ConfigRegistry
from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef
from app.schemas.llm import AnalyticalClaim, EntailmentState
from app.services.analysis.grounding import (
    EvidenceIndex,
    EvidenceItem,
    GroundingEvaluator,
)
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.reconciliation.service import (
    ComparisonMethod,
    Reconciler,
    ToleranceConfig,
)


def _reconciler(db_session, case_id="C1"):
    registry = ConfigRegistry(db_session)
    tol = ToleranceConfig.from_registry(registry)
    audit = AuditLog(db_session)
    rec = Reconciler(tol, session=db_session, audit=audit, case_id=case_id)
    engine = EscalationEngine(db_session, audit=audit, case_id=case_id)
    return rec, engine, audit


def _ai_fact(fact_id, name, value=None, *, raw=None, entity="E1", fy=2024, statement=None):
    return CanonicalFact(
        fact_id=fact_id,
        name=name,
        normalized_value=value,
        raw_value=raw if raw is not None else (statement if statement else (str(value) if value is not None else None)),
        normalized_unit="x" if value is not None else None,
        entity_id=entity,
        fiscal_year=fy,
        period_type="FY",
        status=FactStatus.UNVERIFIED,
        extraction_method=ExtractionMethod.LLM,
        source_refs=[SourceRef(document_id="ai_doc", page=1)],
        created_by="llm:qualitative_extraction",
    )


def _det_fact(fact_id, name, value=None, *, raw=None, entity="E1", fy=2024, statement=None):
    return CanonicalFact(
        fact_id=fact_id,
        name=name,
        normalized_value=value,
        raw_value=raw if raw is not None else (statement if statement else (str(value) if value is not None else None)),
        normalized_unit="x" if value is not None else None,
        entity_id=entity,
        fiscal_year=fy,
        period_type="FY",
        status=FactStatus.UNVERIFIED,
        extraction_method=ExtractionMethod.XBRL,
        source_refs=[SourceRef(document_id="xbrl_doc", taxonomy_concept="X")],
        created_by="xbrl@1",
    )


def _assert_conflict_escalated_not_merged(db_session, rec, engine, result):
    """Shared assertions: conflicting state, both values kept, escalation raised."""
    assert result.resolved_state is FactStatus.CONFLICTING
    # NEVER merged: both observations preserved on the record.
    assert len(result.source_refs) == 2
    row = rec.persist(result)
    assert row.resolved_state == "conflicting"

    escalation = engine.raise_escalation(
        rule_id="R-AI-DET-CONFLICT-01",
        category=EscalationCategory.AI_DETERMINISTIC_CONFLICT,
        severity=Severity.MANDATORY,
        reason=result.detail or "AI vs deterministic conflict.",
        evidence_refs=result.fact_ids,
    )
    assert escalation.status == "open"
    assert engine.has_unresolved_mandatory("C1")
    return row, escalation


def test_numerical_mismatch_detected_and_escalated(db_session):
    rec, engine, _ = _reconciler(db_session)
    ai = _ai_fact("ai1", "net_debt_to_ebitda", 5.0)
    det = _det_fact("det1", "net_debt_to_ebitda", 3.0)

    result = rec.reconcile_pair("net_debt_to_ebitda", ai, det)
    _assert_conflict_escalated_not_merged(db_session, rec, engine, result)
    assert set(result.values) == {5.0, 3.0}


def test_qualitative_contradiction_detected_and_escalated(db_session):
    rec, engine, _ = _reconciler(db_session)
    ai = _ai_fact("ai1", "ownership", statement="Controlled by founder.")
    det = _det_fact("det1", "ownership", statement="Widely held, no >5% holder.")

    result = rec.contradiction("ownership", ai, det)
    assert result.record_kind == "non_numeric"
    _assert_conflict_escalated_not_merged(db_session, rec, engine, result)


def test_wrong_entity_is_definition_mismatch_not_merged(db_session):
    rec, engine, _ = _reconciler(db_session)
    ai = _ai_fact("ai1", "total_debt", 100.0, entity="WRONG")
    det = _det_fact("det1", "total_debt", 100.0, entity="E1")

    result = rec.reconcile_pair("total_debt", ai, det)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert "entity" in result.mismatch_dimensions  # wrong entity, not merged
    _assert_conflict_escalated_not_merged(db_session, rec, engine, result)


def test_wrong_period_is_definition_mismatch_not_merged(db_session):
    rec, engine, _ = _reconciler(db_session)
    ai = _ai_fact("ai1", "revenue", 100.0, fy=2023)
    det = _det_fact("det1", "revenue", 100.0, fy=2024)

    result = rec.reconcile_pair("revenue", ai, det)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert "period" in result.mismatch_dimensions  # wrong period, not merged
    _assert_conflict_escalated_not_merged(db_session, rec, engine, result)


def test_stale_ai_evidence_is_flagged_and_not_verified(db_session):
    """A stale AI fact never silently merges into a verified value."""
    rec, engine, _ = _reconciler(db_session)
    stale = _ai_fact("ai1", "revenue", 90.0)
    stale.status = FactStatus.STALE
    det = _det_fact("det1", "revenue", 100.0)

    # Even though values differ, the AI side is stale: reconciliation keeps it
    # non-verified and the conflict is preserved for review.
    result = rec.reconcile_pair("revenue", stale, det)
    assert result.resolved_state is not FactStatus.VERIFIED
    rec.persist(result)
    # Escalate the data-integrity concern explicitly.
    escalation = engine.raise_escalation(
        rule_id="R-STALE-EVIDENCE-01",
        category=EscalationCategory.DATA_INTEGRITY,
        severity=Severity.REVIEW,
        reason="AI evidence is stale; not eligible to verify a value.",
        evidence_refs=[stale.fact_id, det.fact_id],
    )
    assert escalation.status == "open"


def test_missing_critical_evidence_never_becomes_zero(db_session):
    """Missing critical evidence stays 'missing' (never numerical zero)."""
    rec, _, _ = _reconciler(db_session)
    result = rec.reconcile_field("liquidity", [])
    assert result.resolved_state is FactStatus.MISSING
    assert result.values == []  # not [0]


def test_grounding_presence_vs_entailment_measured_separately(db_session):
    """Citation presence, entailment, unsupported and contradiction are distinct."""
    evidence = EvidenceIndex(
        [
            EvidenceItem(evidence_id="m_true", value=3.0),
            EvidenceItem(evidence_id="s_support", text="n/a", supports=EntailmentState.SUPPORTED),
            EvidenceItem(evidence_id="s_unsupported", text="n/a", supports=EntailmentState.UNSUPPORTED),
        ]
    )
    evaluator = GroundingEvaluator(evidence, session=db_session, case_id="C1")

    # 1) citation present + entailed -> grounded
    grounded = evaluator.evaluate_claim(
        AnalyticalClaim(claim_id="g1", text="Leverage is 3.0.", kind="fact", evidence_ids=["m_true"])
    )
    # 2) citation present + contradicted -> contradictory, NOT grounded
    contradicted = evaluator.evaluate_claim(
        AnalyticalClaim(claim_id="g2", text="Leverage is 9.0.", kind="fact", evidence_ids=["m_true"])
    )
    # 3) citation present + unsupported narrative -> unsupported, NOT grounded
    unsupported = evaluator.evaluate_claim(
        AnalyticalClaim(claim_id="g3", text="Brand is dominant.", kind="interpretation", evidence_ids=["s_unsupported"])
    )
    # 4) no citation -> not verifiable, NOT grounded
    no_cite = evaluator.evaluate_claim(
        AnalyticalClaim(claim_id="g4", text="Outlook is stable.", kind="interpretation", evidence_ids=[])
    )

    # citation presence measured independently of entailment:
    assert grounded.citation_present and contradicted.citation_present and unsupported.citation_present
    assert no_cite.citation_present is False

    # entailment states measured separately:
    assert grounded.entailment_state is EntailmentState.SUPPORTED
    assert contradicted.entailment_state is EntailmentState.CONTRADICTORY
    assert unsupported.entailment_state is EntailmentState.UNSUPPORTED
    assert no_cite.entailment_state is EntailmentState.NOT_VERIFIABLE

    # grounded ONLY when both pass:
    assert grounded.is_grounded is True
    assert contradicted.is_grounded is False
    assert unsupported.is_grounded is False
    assert no_cite.is_grounded is False
