"""Append-only audit log immutability tests (Req 18.1-18.4)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.orm import AuditEvent
from app.services.audit.log import (
    ActorType,
    AuditImmutabilityError,
    AuditLog,
    EventType,
)


def test_record_populates_all_required_fields(db_session: Session) -> None:
    """Req 18.3: every event carries the full field set."""
    log = AuditLog(db_session)
    event = log.record(
        EventType.CASE_CREATED,
        case_id="DAL_2024",
        actor_type=ActorType.HUMAN,
        actor_id="analyst-1",
        before=None,
        after={"case_id": "DAL_2024"},
        reason="case opened",
        linked_objects=["DAL_2024"],
    )
    assert event.event_id
    assert event.case_id == "DAL_2024"
    assert event.event_type == "case_created"
    assert event.timestamp is not None
    assert event.actor_type == "human"
    assert event.actor_id == "analyst-1"
    assert event.after == {"case_id": "DAL_2024"}
    assert event.reason == "case opened"
    assert event.linked_objects == ["DAL_2024"]


def test_all_required_event_types_present() -> None:
    """Req 18.2: full list of event types supported."""
    required = {
        "case_created",
        "document_ingested",
        "document_superseded",
        "entity_registered",
        "entity_mismatch_detected",
        "fact_extracted",
        "fact_verified",
        "fact_conflict_detected",
        "fact_human_corrected",
        "metric_calculated",
        "metric_definition_changed",
        "benchmark_generated",
        "rule_triggered",
        "config_version_changed",
        "llm_run",
        "challenge_created",
        "escalation_created",
        "escalation_resolved",
        "human_review",
        "snapshot_finalized",
        "memo_generated",
        "case_finalized",
    }
    agentic = {
        # Agentic credit-analysis events (Milestone 1.2+).
        "analysis_run_created",
        "agent_run",
        "parameter_computed",
        "parameter_validated",
        "score_computed",
        "rerun_triggered",
        "descendant_invalidated",
        "candidate_evaluated",
    }
    assert {e.value for e in EventType} == required | {
        "evidence_rejected",
        "mapping_review_required",
        "draft_memo_generated",
        "fact_reconciled",
        "evidence_admitted",
        "sec_filing_discovered",
        "sec_file_downloaded",
        "inline_xbrl_parsed",
        "narrative_evidence_extracted",
        "proxy_linked",
        "subsidiaries_extracted",
    } | agentic


def test_audit_log_has_no_update_or_delete_methods() -> None:
    """Req 18.4: the service exposes only an append path."""
    forbidden = {"update", "delete", "modify", "edit", "remove"}
    public = {name for name in dir(AuditLog) if not name.startswith("_")}
    assert forbidden.isdisjoint(public), public


def test_modifying_persisted_event_is_blocked(db_session: Session) -> None:
    """Req 18.4: a persisted event can never be modified."""
    log = AuditLog(db_session)
    event = log.record(EventType.FACT_EXTRACTED, case_id="DAL_2024")
    db_session.commit()

    event.reason = "tampered"
    with pytest.raises(AuditImmutabilityError):
        db_session.flush()
    db_session.rollback()


def test_deleting_persisted_event_is_blocked(db_session: Session) -> None:
    """Req 18.4: a persisted event can never be deleted."""
    log = AuditLog(db_session)
    event = log.record(EventType.FACT_EXTRACTED, case_id="DAL_2024")
    db_session.commit()

    db_session.delete(event)
    with pytest.raises(AuditImmutabilityError):
        db_session.flush()
    db_session.rollback()

    # The event survives the blocked delete.
    assert db_session.query(AuditEvent).count() == 1
