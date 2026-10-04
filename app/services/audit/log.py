"""Append-only audit log (Requirement 18).

The audit log is append-only by construction:

* :class:`AuditLog` exposes only an append path (:meth:`record`); there is no
  update or delete method anywhere in the service (Req 18.4).
* A SQLAlchemy ``before_flush`` guard (:func:`install_immutability_guard`)
  raises :class:`AuditImmutabilityError` if any already-persisted
  :class:`AuditEvent` is modified or deleted, so immutability cannot be bypassed
  by writing to the ORM directly.

Every event carries the full field set required by Req 18.3:
``event_id, case_id, event_type, timestamp, actor_type, actor_id, before,
after, reason, linked_objects``.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Iterable

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.orm import AuditEvent


class AuditImmutabilityError(RuntimeError):
    """Raised when code attempts to modify or delete a persisted audit event."""


class ActorType(str, Enum):
    """Who caused an event (Req 18.3 ``actor_type``)."""

    HUMAN = "human"
    SYSTEM = "system"
    MODEL = "model"


class EventType(str, Enum):
    """All audit event types required by Requirement 18.2."""

    CASE_CREATED = "case_created"
    EVIDENCE_REJECTED = "evidence_rejected"
    MAPPING_REVIEW_REQUIRED = "mapping_review_required"
    DRAFT_MEMO_GENERATED = "draft_memo_generated"
    DOCUMENT_INGESTED = "document_ingested"
    DOCUMENT_SUPERSEDED = "document_superseded"
    ENTITY_REGISTERED = "entity_registered"
    ENTITY_MISMATCH_DETECTED = "entity_mismatch_detected"
    FACT_EXTRACTED = "fact_extracted"
    FACT_RECONCILED = "fact_reconciled"
    FACT_VERIFIED = "fact_verified"
    FACT_CONFLICT_DETECTED = "fact_conflict_detected"
    FACT_HUMAN_CORRECTED = "fact_human_corrected"
    METRIC_CALCULATED = "metric_calculated"
    METRIC_DEFINITION_CHANGED = "metric_definition_changed"
    BENCHMARK_GENERATED = "benchmark_generated"
    RULE_TRIGGERED = "rule_triggered"
    CONFIG_VERSION_CHANGED = "config_version_changed"
    LLM_RUN = "llm_run"
    CHALLENGE_CREATED = "challenge_created"
    ESCALATION_CREATED = "escalation_created"
    ESCALATION_RESOLVED = "escalation_resolved"
    HUMAN_REVIEW = "human_review"
    SNAPSHOT_FINALIZED = "snapshot_finalized"
    MEMO_GENERATED = "memo_generated"
    CASE_FINALIZED = "case_finalized"


def install_immutability_guard(session: Session) -> None:
    """Attach a ``before_flush`` guard that forbids mutating/deleting events.

    Idempotent per session. New :class:`AuditEvent` inserts are allowed; any
    attempt to UPDATE or DELETE an existing one raises
    :class:`AuditImmutabilityError`.
    """

    if getattr(session, "_audit_guard_installed", False):
        return

    @event.listens_for(session, "before_flush")
    def _before_flush(sess: Session, flush_context, instances) -> None:  # noqa: ANN001
        for obj in sess.dirty:
            if isinstance(obj, AuditEvent) and sess.is_modified(obj):
                raise AuditImmutabilityError(
                    "Audit events are append-only and cannot be modified "
                    f"(event_id={obj.event_id!r})."
                )
        for obj in sess.deleted:
            if isinstance(obj, AuditEvent):
                raise AuditImmutabilityError(
                    "Audit events are append-only and cannot be deleted "
                    f"(event_id={obj.event_id!r})."
                )

    session._audit_guard_installed = True  # type: ignore[attr-defined]


class AuditLog:
    """Append-only audit log over a SQLAlchemy session.

    Exposes only an append path plus read helpers. No update/delete methods
    exist (Req 18.4). Installs the immutability guard on construction.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        install_immutability_guard(session)

    def record(
        self,
        event_type: EventType | str,
        *,
        case_id: str | None = None,
        actor_type: ActorType | str = ActorType.SYSTEM,
        actor_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str | None = None,
        linked_objects: Iterable[Any] | None = None,
    ) -> AuditEvent:
        """Append a new audit event and return it.

        ``event_type``/``actor_type`` accept enums or their string values; the
        stored value is always the canonical string.
        """
        et = event_type.value if isinstance(event_type, EventType) else str(event_type)
        at = actor_type.value if isinstance(actor_type, ActorType) else str(actor_type)

        audit_event = AuditEvent(
            event_id=str(uuid.uuid4()),
            case_id=case_id,
            event_type=et,
            timestamp=utcnow(),
            actor_type=at,
            actor_id=actor_id,
            before=before,
            after=after,
            reason=reason,
            linked_objects=list(linked_objects) if linked_objects is not None else [],
        )
        self._session.add(audit_event)
        self._session.flush()
        return audit_event

    def events_for_case(self, case_id: str) -> list[AuditEvent]:
        """Return all events for a case, oldest first (read-only helper)."""
        return list(
            self._session.query(AuditEvent)
            .filter(AuditEvent.case_id == case_id)
            .order_by(AuditEvent.timestamp.asc(), AuditEvent.event_id.asc())
            .all()
        )
