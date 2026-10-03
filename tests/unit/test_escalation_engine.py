"""Escalation engine lifecycle tests (Req 11.4, 14.1-14.7; task 5.6)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.orm import Case, Escalation
from app.services.audit.log import AuditLog, EventType
from app.services.escalation.engine import (
    CASE_STATUS_ESCALATED,
    CASE_STATUS_OPEN,
    EscalationEngine,
)
from app.services.escalation.rules import EscalationCategory, RuleConcept, Severity


def _case(db_session: Session, case_id: str = "C1") -> Case:
    case = Case(case_id=case_id, status=CASE_STATUS_OPEN)
    db_session.add(case)
    db_session.flush()
    return case


def test_escalation_has_all_required_fields(db_session: Session) -> None:
    """Req 14.3: escalation carries id/case/severity/rule_id/reason/refs/status."""
    _case(db_session)
    engine = EscalationEngine(db_session, case_id="C1")
    row = engine.raise_escalation(
        rule_id="R-POLICY-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        concept=RuleConcept.POLICY_THRESHOLD,
        reason="policy breach",
        evidence_refs=["metric:net_debt_to_ebitda:2023"],
    )
    assert row.escalation_id and row.case_id == "C1"
    assert row.rule_id == "R-POLICY-LEV-01"
    assert row.severity == Severity.MANDATORY.value
    assert row.evidence_refs == ["metric:net_debt_to_ebitda:2023"]
    assert row.status == "open"
    assert row.resolution is None
    assert row.triggered_at is not None


def test_mandatory_escalation_sets_case_status(db_session: Session) -> None:
    """Req 14.7: an unresolved mandatory escalation drives case status."""
    case = _case(db_session)
    engine = EscalationEngine(db_session, case_id="C1")
    engine.raise_escalation(
        rule_id="R-POLICY-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        reason="policy breach",
    )
    assert case.status == CASE_STATUS_ESCALATED
    assert engine.has_unresolved_mandatory("C1") is True


def test_review_escalation_does_not_escalate_case(db_session: Session) -> None:
    case = _case(db_session)
    engine = EscalationEngine(db_session, case_id="C1")
    engine.raise_escalation(
        rule_id="R-TREND-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.REVIEW,
        reason="historical deterioration",
    )
    assert case.status == CASE_STATUS_OPEN  # review-only does not force escalation


def test_resolution_is_additive_and_nondestructive(db_session: Session) -> None:
    """Req 14.5/14.6: resolution never deletes; it transitions + records."""
    case = _case(db_session)
    audit = AuditLog(db_session)
    engine = EscalationEngine(db_session, audit=audit, case_id="C1")
    row = engine.raise_escalation(
        rule_id="R-POLICY-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        reason="policy breach",
    )
    engine.resolve(
        row.escalation_id,
        resolved_by="analyst-1",
        resolution={"decision": "accepted_with_covenant"},
        reason="covenant added",
    )
    # The record still EXISTS (not deleted) and is now resolved.
    stored = db_session.get(Escalation, row.escalation_id)
    assert stored is not None
    assert stored.status == "resolved"
    assert stored.resolution == {"decision": "accepted_with_covenant"}
    assert stored.resolved_by == "analyst-1"
    # Case status clears once no mandatory escalation remains open (Req 14.7).
    assert case.status == CASE_STATUS_OPEN
    # Both lifecycle events are in the append-only log.
    types = {e.event_type for e in audit.events_for_case("C1")}
    assert EventType.ESCALATION_CREATED.value in types
    assert EventType.ESCALATION_RESOLVED.value in types
    assert EventType.RULE_TRIGGERED.value in types


def test_cannot_double_resolve(db_session: Session) -> None:
    _case(db_session)
    engine = EscalationEngine(db_session, case_id="C1")
    row = engine.raise_escalation(
        rule_id="R-POLICY-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        reason="breach",
    )
    engine.resolve(row.escalation_id, resolved_by="a", resolution={"x": 1})
    with pytest.raises(ValueError):
        engine.resolve(row.escalation_id, resolved_by="a", resolution={"x": 2})


def test_multiple_mandatory_keep_case_escalated_until_all_resolved(
    db_session: Session,
) -> None:
    case = _case(db_session)
    engine = EscalationEngine(db_session, case_id="C1")
    e1 = engine.raise_escalation(
        rule_id="R-POLICY-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        reason="breach 1",
    )
    e2 = engine.raise_escalation(
        rule_id="R-DATA-CONFLICT-01",
        category=EscalationCategory.DATA_INTEGRITY,
        severity=Severity.MANDATORY,
        reason="conflict",
    )
    assert case.status == CASE_STATUS_ESCALATED
    engine.resolve(e1.escalation_id, resolved_by="a", resolution={"x": 1})
    # Still escalated: e2 open.
    assert case.status == CASE_STATUS_ESCALATED
    engine.resolve(e2.escalation_id, resolved_by="a", resolution={"x": 1})
    assert case.status == CASE_STATUS_OPEN
