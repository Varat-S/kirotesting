"""Non-destructive human-in-the-loop review workflow (Req 15.1-15.6; task 7.1).

A reviewer can take any of the required actions (Req 15.1):

* ``verify_source``            -- verify a source document/fact.
* ``resolve_conflict``         -- resolve a conflicting figure.
* ``approve_accounting_adjustment`` -- approve an accounting adjustment.
* ``approve_assumptions``      -- approve analytical assumptions.
* ``confirm_peer_set``         -- confirm the peer cohort.
* ``resolve_contradiction``    -- resolve contradictory evidence.
* ``accept_ai_interpretation`` -- accept an AI interpretation.
* ``reject_ai_interpretation`` -- reject an AI interpretation.
* ``modify_risk_materiality``  -- modify a risk's materiality ranking.
* ``sign_off_recommendation``  -- sign off the final recommendation.

Every action is recorded ADDITIVELY as a :class:`~app.models.orm.HumanReview`
row capturing reviewer, timestamp, prior value, new value (if changed), reason,
linked evidence and an optional comment (Req 15.2). Nothing is overwritten or
deleted, so before/after values remain visible/recoverable (Req 15.3). Each
action emits a ``human_review`` audit event via the append-only
:class:`~app.services.audit.log.AuditLog`.

The gated actions (Req 15.4) -- final recommendation, material accounting
adjustments, resolved conflicts, facility-structure recommendations, policy
exceptions, and judgment-heavy risk rankings -- require explicit human sign-off
before a recommendation can be marked final (Req 15.6). The workflow can also
report the unresolved exceptions that the final output must identify (Req 15.5),
reusing the Milestone 5 escalation engine's open-escalation queries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import HumanReview
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.escalation.engine import EscalationEngine


class ReviewAction(str, Enum):
    """The reviewer actions supported by the review workflow (Req 15.1)."""

    VERIFY_SOURCE = "verify_source"
    RESOLVE_CONFLICT = "resolve_conflict"
    APPROVE_ACCOUNTING_ADJUSTMENT = "approve_accounting_adjustment"
    APPROVE_ASSUMPTIONS = "approve_assumptions"
    CONFIRM_PEER_SET = "confirm_peer_set"
    RESOLVE_CONTRADICTION = "resolve_contradiction"
    ACCEPT_AI_INTERPRETATION = "accept_ai_interpretation"
    REJECT_AI_INTERPRETATION = "reject_ai_interpretation"
    MODIFY_RISK_MATERIALITY = "modify_risk_materiality"
    FACILITY_STRUCTURE_RECOMMENDATION = "facility_structure_recommendation"
    APPROVE_POLICY_EXCEPTION = "approve_policy_exception"
    SIGN_OFF_RECOMMENDATION = "sign_off_recommendation"


# Actions that require explicit human sign-off / approval (Req 15.4): final
# recommendation, material accounting adjustments, resolved conflicts, facility
# structure recommendations, policy exceptions, and judgment-heavy risk rankings.
GATED_ACTIONS: frozenset[ReviewAction] = frozenset(
    {
        ReviewAction.SIGN_OFF_RECOMMENDATION,
        ReviewAction.APPROVE_ACCOUNTING_ADJUSTMENT,
        ReviewAction.RESOLVE_CONFLICT,
        ReviewAction.FACILITY_STRUCTURE_RECOMMENDATION,
        ReviewAction.APPROVE_POLICY_EXCEPTION,
        ReviewAction.MODIFY_RISK_MATERIALITY,
    }
)


class SignOffRequiredError(RuntimeError):
    """Raised when finalization is attempted without the required sign-off."""


@dataclass(frozen=True)
class ReviewView:
    """A read view of a recorded human review."""

    review_id: str
    case_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    reviewer: str
    prior_value: dict | None
    new_value: dict | None
    reason: str | None
    linked_evidence: list[str]
    comment: str | None
    requires_sign_off: bool
    signed_off: bool


class HumanReviewWorkflow:
    """Record reviewer actions non-destructively and gate the final sign-off."""

    def __init__(
        self,
        session: Session,
        *,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._case_id = case_id

    # -- recording a reviewer action ------------------------------------------

    def record_action(
        self,
        action: ReviewAction | str,
        *,
        reviewer: str,
        target_type: str | None = None,
        target_id: str | None = None,
        prior_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        reason: str | None = None,
        linked_evidence: Iterable[str] | None = None,
        comment: str | None = None,
        signed_off: bool = False,
        case_id: str | None = None,
    ) -> HumanReview:
        """Append a human-review action and emit a ``human_review`` event.

        The record is additive: ``prior_value`` and ``new_value`` are both
        retained so before/after stays recoverable (Req 15.2, 15.3). For a
        gated action (Req 15.4), ``signed_off`` must be ``True`` for the action
        to count as an explicit approval; the record is still stored either way
        so the attempt is auditable.
        """
        action_v = action if isinstance(action, ReviewAction) else ReviewAction(action)
        cid = case_id if case_id is not None else self._case_id
        requires_sign_off = action_v in GATED_ACTIONS

        row = HumanReview(
            review_id=str(uuid.uuid4()),
            case_id=cid,
            action=action_v.value,
            target_type=target_type,
            target_id=target_id,
            reviewer=reviewer,
            prior_value=prior_value,
            new_value=new_value,
            reason=reason,
            linked_evidence=list(linked_evidence or []),
            comment=comment,
            requires_sign_off=requires_sign_off,
            signed_off=bool(signed_off) if requires_sign_off else False,
        )
        self._session.add(row)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.HUMAN_REVIEW,
                case_id=cid,
                actor_type=ActorType.HUMAN,
                actor_id=reviewer,
                before=prior_value,
                after=new_value,
                reason=reason,
                linked_objects=[
                    f"review:{row.review_id}",
                    *( [f"{target_type}:{target_id}"] if target_id else [] ),
                    *row.linked_evidence,
                ],
            )
        return row

    def sign_off_recommendation(
        self,
        *,
        reviewer: str,
        prior_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        reason: str | None = None,
        linked_evidence: Iterable[str] | None = None,
        comment: str | None = None,
        case_id: str | None = None,
    ) -> HumanReview:
        """Record an explicit sign-off of the final recommendation (Req 15.6)."""
        return self.record_action(
            ReviewAction.SIGN_OFF_RECOMMENDATION,
            reviewer=reviewer,
            target_type="recommendation",
            prior_value=prior_value,
            new_value=new_value,
            reason=reason,
            linked_evidence=linked_evidence,
            comment=comment,
            signed_off=True,
            case_id=case_id,
        )

    # -- read helpers ---------------------------------------------------------

    def reviews_for_case(self, case_id: str | None = None) -> list[ReviewView]:
        cid = case_id if case_id is not None else self._case_id
        stmt = (
            select(HumanReview)
            .where(HumanReview.case_id == cid)
            .order_by(HumanReview.reviewed_at.asc(), HumanReview.id.asc())
        )
        return [self._view(r) for r in self._session.execute(stmt).scalars().all()]

    def history_for_target(
        self, target_type: str, target_id: str, case_id: str | None = None
    ) -> list[ReviewView]:
        """Return the ordered before/after trail for a single target (Req 15.3)."""
        cid = case_id if case_id is not None else self._case_id
        stmt = (
            select(HumanReview)
            .where(
                HumanReview.case_id == cid,
                HumanReview.target_type == target_type,
                HumanReview.target_id == target_id,
            )
            .order_by(HumanReview.reviewed_at.asc(), HumanReview.id.asc())
        )
        return [self._view(r) for r in self._session.execute(stmt).scalars().all()]

    def has_sign_off(self, case_id: str | None = None) -> bool:
        """Return True if the final recommendation has an explicit sign-off."""
        cid = case_id if case_id is not None else self._case_id
        stmt = select(HumanReview).where(
            HumanReview.case_id == cid,
            HumanReview.action == ReviewAction.SIGN_OFF_RECOMMENDATION.value,
            HumanReview.signed_off.is_(True),
        )
        return self._session.execute(stmt).first() is not None

    def unresolved_exceptions(
        self, case_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Identify unresolved exceptions for the final output (Req 15.5).

        Reuses the Milestone 5 escalation engine's open-escalation query rather
        than rebuilding it; returns a serializable view of each open escalation.
        """
        cid = case_id if case_id is not None else self._case_id
        engine = EscalationEngine(self._session, case_id=cid)
        return [
            {
                "escalation_id": e.escalation_id,
                "rule_id": e.rule_id,
                "severity": e.severity,
                "category": e.category,
                "concept": e.concept,
                "reason": e.reason,
                "mandatory": e.mandatory,
                "evidence_refs": list(e.evidence_refs),
            }
            for e in engine.open_escalations(cid)
        ]

    def has_unresolved_mandatory_exceptions(
        self, case_id: str | None = None
    ) -> bool:
        cid = case_id if case_id is not None else self._case_id
        return EscalationEngine(
            self._session, case_id=cid
        ).has_unresolved_mandatory(cid)

    @staticmethod
    def _view(row: HumanReview) -> ReviewView:
        return ReviewView(
            review_id=row.review_id,
            case_id=row.case_id,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            reviewer=row.reviewer,
            prior_value=row.prior_value,
            new_value=row.new_value,
            reason=row.reason,
            linked_evidence=list(row.linked_evidence),
            comment=row.comment,
            requires_sign_off=row.requires_sign_off,
            signed_off=row.signed_off,
        )
