"""Event-based escalation engine (Req 11.4, 14.1-14.7; task 5.6).

The engine routes material exceptions to human review via EXPLICIT, event-based
rules. There is NO single opaque risk score (Req 14.1): each escalation names
its exact ``rule_id`` and the discrete event that triggered it.

An escalation record carries every required field (Req 14.3): ``escalation_id,
case_id, severity, rule_id, reason, triggered_at, evidence_refs[], status,
resolution``. It is categorized (Req 14.2) as one of: ``data_integrity``,
``financial_rules``, ``ai_deterministic_conflict``, ``evidence``.

Lifecycle guarantees:

* **Cannot vanish unresolved (Req 14.5).** An open escalation persists until a
  human resolves it. Resolution is ADDITIVE (Req 14.6): the record transitions
  ``open -> resolved`` with who/when/why recorded and emits
  ``escalation_resolved``; it is NEVER deleted.
* **Case status reflects unresolved mandatory escalations (Req 14.7).** While
  any mandatory escalation is open, the case status is driven to
  ``escalated``; it clears only when all mandatory escalations are resolved.

The engine consumes signals produced upstream (reconciliation conflicts,
entity mismatches, missing-critical-source completeness) AND new Milestone 5
signals (policy breach, peer anomaly, historical deterioration, bad-denominator
``requires_review`` metrics). Every raise emits ``rule_triggered`` +
``escalation_created``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.orm import Case, Escalation
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.escalation.rules import (
    EscalationCategory,
    RuleConcept,
    RuleOutcome,
    Severity,
)

# Status a case takes while a mandatory escalation is unresolved (Req 14.7).
CASE_STATUS_ESCALATED = "escalated"
CASE_STATUS_OPEN = "open"


@dataclass(frozen=True)
class EscalationView:
    """A read view of a persisted escalation."""

    escalation_id: str
    case_id: str | None
    severity: str
    category: str
    concept: str | None
    rule_id: str
    reason: str
    mandatory: bool
    status: str
    evidence_refs: list[str]
    resolution: dict | None


class EscalationEngine:
    """Persist, resolve, and reflect escalations for a case."""

    def __init__(
        self,
        session: Session,
        *,
        audit: AuditLog | None = None,
        case_id: str | None = None,
        deterministic_ids: bool = False,
    ) -> None:
        self._session = session
        self._audit = audit
        self._case_id = case_id
        self._deterministic_ids = deterministic_ids

    # -- raising --------------------------------------------------------------

    def raise_escalation(
        self,
        *,
        rule_id: str,
        category: EscalationCategory | str,
        severity: Severity | str,
        reason: str,
        concept: RuleConcept | str | None = None,
        rule_version: int | None = None,
        evidence_refs: Iterable[str] | None = None,
        case_id: str | None = None,
    ) -> Escalation:
        """Create an OPEN escalation and emit rule_triggered + escalation_created.

        A ``mandatory`` severity escalation drives the case status to
        ``escalated`` (Req 14.7).
        """
        cat_v = (
            category.value
            if isinstance(category, EscalationCategory)
            else str(category)
        )
        sev_v = severity.value if isinstance(severity, Severity) else str(severity)
        concept_v = concept.value if isinstance(concept, RuleConcept) else concept
        cid = case_id if case_id is not None else self._case_id
        mandatory = sev_v == Severity.MANDATORY.value

        escalation_id = str(uuid.uuid4())
        if self._deterministic_ids:
            from app.core.hashing import content_hash

            escalation_id = content_hash(
                {
                    "case": cid,
                    "rule": rule_id,
                    "version": rule_version,
                    "reason": reason,
                    "refs": sorted(evidence_refs or []),
                }
            )
            existing = self._session.get(Escalation, escalation_id)
            if existing is not None:
                return existing
        row = Escalation(
            escalation_id=escalation_id,
            case_id=cid,
            severity=sev_v,
            category=cat_v,
            concept=concept_v,
            rule_id=rule_id,
            rule_version=rule_version,
            reason=reason,
            mandatory=mandatory,
            triggered_at=utcnow(),
            evidence_refs=list(evidence_refs or []),
            status="open",
            resolution=None,
        )
        self._session.add(row)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.RULE_TRIGGERED,
                case_id=cid,
                actor_type=ActorType.SYSTEM,
                after={
                    "rule_id": rule_id,
                    "rule_version": rule_version,
                    "category": cat_v,
                    "severity": sev_v,
                    "concept": concept_v,
                },
                reason=f"Rule {rule_id!r} triggered.",
                linked_objects=list(row.evidence_refs),
            )
            self._audit.record(
                EventType.ESCALATION_CREATED,
                case_id=cid,
                actor_type=ActorType.SYSTEM,
                after=self._payload(row),
                reason=reason,
                linked_objects=[f"escalation:{row.escalation_id}", *row.evidence_refs],
            )

        if mandatory:
            self._reflect_case_status(cid)
        return row

    def raise_from_outcome(
        self, outcome: RuleOutcome, *, case_id: str | None = None
    ) -> Escalation | None:
        """Raise an escalation from a triggered :class:`RuleOutcome`.

        Returns ``None`` when the outcome did not trigger (no escalation is
        fabricated for a non-event).
        """
        if not outcome.triggered:
            return None
        return self.raise_escalation(
            rule_id=outcome.rule_id,
            category=outcome.category,
            severity=outcome.severity,
            concept=outcome.concept,
            rule_version=outcome.rule_version,
            reason=outcome.reason,
            evidence_refs=outcome.evidence_refs,
            case_id=case_id,
        )

    # -- resolving (additive, never destructive) ------------------------------

    def resolve(
        self,
        escalation_id: str,
        *,
        resolved_by: str,
        resolution: dict[str, Any],
        reason: str | None = None,
    ) -> Escalation:
        """Resolve an escalation additively (Req 14.6).

        The record transitions ``open -> resolved`` recording who/when/why and
        the resolution payload; it is NEVER deleted. Emits
        ``escalation_resolved``. Re-resolving an already-resolved escalation is
        rejected (the resolution event is not duplicated).
        """
        row = self._session.get(Escalation, escalation_id)
        if row is None:
            raise ValueError(f"No escalation with id {escalation_id!r}.")
        if row.status == "resolved":
            raise ValueError(
                f"Escalation {escalation_id!r} is already resolved; resolution is "
                "additive and cannot be re-applied."
            )

        before = self._payload(row)
        row.status = "resolved"
        row.resolution = resolution
        row.resolved_by = resolved_by
        row.resolved_at = utcnow()
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.ESCALATION_RESOLVED,
                case_id=row.case_id,
                actor_type=ActorType.HUMAN,
                actor_id=resolved_by,
                before=before,
                after=self._payload(row),
                reason=reason or "Escalation resolved by human reviewer.",
                linked_objects=[f"escalation:{row.escalation_id}"],
            )

        # Case status may clear once no mandatory escalation remains open.
        self._reflect_case_status(row.case_id)
        return row

    # -- case-status reflection (Req 14.7) ------------------------------------

    def open_escalations(
        self, case_id: str | None = None, *, mandatory_only: bool = False
    ) -> list[Escalation]:
        cid = case_id if case_id is not None else self._case_id
        stmt = select(Escalation).where(
            Escalation.case_id == cid, Escalation.status == "open"
        )
        if mandatory_only:
            stmt = stmt.where(Escalation.mandatory.is_(True))
        return list(self._session.execute(stmt).scalars().all())

    def has_unresolved_mandatory(self, case_id: str | None = None) -> bool:
        return bool(self.open_escalations(case_id, mandatory_only=True))

    def _reflect_case_status(self, case_id: str | None) -> None:
        """Drive the case status from unresolved mandatory escalations.

        Only toggles between ``open`` and ``escalated`` so it never clobbers a
        richer workflow status set elsewhere.
        """
        if case_id is None:
            return
        case = self._session.get(Case, case_id)
        if case is None:
            return
        has_mandatory = self.has_unresolved_mandatory(case_id)
        if has_mandatory:
            if case.status != CASE_STATUS_ESCALATED:
                case.status = CASE_STATUS_ESCALATED
                self._session.flush()
        elif case.status == CASE_STATUS_ESCALATED:
            case.status = CASE_STATUS_OPEN
            self._session.flush()

    # -- read helpers ---------------------------------------------------------

    def escalations_for_case(self, case_id: str | None = None) -> list[EscalationView]:
        cid = case_id if case_id is not None else self._case_id
        stmt = (
            select(Escalation)
            .where(Escalation.case_id == cid)
            .order_by(Escalation.triggered_at.asc(), Escalation.escalation_id.asc())
        )
        return [self._view(r) for r in self._session.execute(stmt).scalars().all()]

    @staticmethod
    def _payload(row: Escalation) -> dict[str, Any]:
        return {
            "escalation_id": row.escalation_id,
            "case_id": row.case_id,
            "severity": row.severity,
            "category": row.category,
            "concept": row.concept,
            "rule_id": row.rule_id,
            "rule_version": row.rule_version,
            "reason": row.reason,
            "mandatory": row.mandatory,
            "evidence_refs": list(row.evidence_refs),
            "status": row.status,
            "resolution": row.resolution,
        }

    @staticmethod
    def _view(row: Escalation) -> EscalationView:
        return EscalationView(
            escalation_id=row.escalation_id,
            case_id=row.case_id,
            severity=row.severity,
            category=row.category,
            concept=row.concept,
            rule_id=row.rule_id,
            reason=row.reason,
            mandatory=row.mandatory,
            status=row.status,
            evidence_refs=list(row.evidence_refs),
            resolution=row.resolution,
        )
