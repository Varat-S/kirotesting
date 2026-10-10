"""Human acceptance and immutability of the ``AcceptedBusinessOverview`` handoff.

A BO run leaves its handoff ``pending``. Only an explicit reviewer action can
accept or reject it; that action is recorded through the existing
``HumanReviewWorkflow`` (additive ``human_reviews`` row + audit event). Once
accepted, the stored artifact can no longer be edited or deleted, and its
content hash is checked on every read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.models.orm import AcceptedBusinessOverviewRow
from app.schemas.bo import AcceptedBusinessOverview
from app.services.audit.log import AuditLog
from app.services.bo.reporting import handoff_content_hash
from app.services.review.workflow import HumanReviewWorkflow, ReviewAction


class HandoffError(ValueError):
    pass


class HandoffImmutableError(HandoffError):
    pass


@event.listens_for(Session, "before_flush")
def _accepted_handoff_is_immutable(session, *_):
    for row in session.dirty | session.deleted:
        if not isinstance(row, AcceptedBusinessOverviewRow):
            continue
        history = inspect(row).attrs.human_review_status.history
        was = history.deleted[0] if history.deleted else row.human_review_status
        if was == "accepted" and (row in session.deleted or session.is_modified(row)):
            raise HandoffImmutableError(
                "An accepted Business Overview handoff cannot be edited or deleted; "
                "run a new analysis to supersede it.")


def latest_for_case(session: Session, case_id: str) -> AcceptedBusinessOverviewRow | None:
    return session.execute(
        select(AcceptedBusinessOverviewRow)
        .where(AcceptedBusinessOverviewRow.case_id == case_id)
        .order_by(AcceptedBusinessOverviewRow.created_at.desc())
    ).scalars().first()


def read_handoff(session: Session, handoff_id: str) -> AcceptedBusinessOverview:
    """Load a handoff and verify it has not drifted from its recorded hash."""
    row = session.get(AcceptedBusinessOverviewRow, handoff_id)
    if row is None:
        raise HandoffError(f"No Business Overview handoff {handoff_id!r}.")
    if handoff_content_hash(row.payload) != row.content_hash:
        raise HandoffError("Business Overview handoff failed its integrity check.")
    return AcceptedBusinessOverview.model_validate(row.payload)


def review_handoff(
    session: Session,
    handoff_id: str,
    *,
    reviewer: str,
    reason: str,
    decision: str = "accept",
    acknowledge_unresolved: bool = False,
) -> dict[str, Any]:
    """Record the reviewer's explicit decision on a pending handoff."""
    if decision not in ("accept", "reject"):
        raise HandoffError("decision must be 'accept' or 'reject'.")
    if not reviewer.strip() or not reason.strip():
        raise HandoffError("Reviewer and reason are required.")
    row = session.get(AcceptedBusinessOverviewRow, handoff_id)
    if row is None:
        raise HandoffError(f"No Business Overview handoff {handoff_id!r}.")
    if row.human_review_status != "pending":
        raise HandoffError(
            f"This handoff is already {row.human_review_status}; decisions are "
            "not revised in place.")
    payload = dict(row.payload)
    if decision == "accept":
        if payload["business_topic_conclusion_id"] is None:
            raise HandoffError(
                "There is no validated Business conclusion to accept.")
        if payload["unresolved_challenges"] and not acknowledge_unresolved:
            raise HandoffError(
                "Unresolved challenges remain; accepting requires an explicit "
                "acknowledgement and they stay recorded on the handoff.")
    status = "accepted" if decision == "accept" else "rejected"
    reviewed_at = datetime.now(timezone.utc)
    review = HumanReviewWorkflow(
        session, audit=AuditLog(session), case_id=row.case_id,
    ).record_action(
        ReviewAction.ACCEPT_AI_INTERPRETATION if decision == "accept"
        else ReviewAction.REJECT_AI_INTERPRETATION,
        reviewer=reviewer, target_type="business_overview", target_id=handoff_id,
        prior_value={"human_review_status": "pending"},
        new_value={"human_review_status": status,
                   "content_hash": row.content_hash,
                   "conclusion_status": payload["conclusion_status"],
                   "unresolved_challenges": len(payload["unresolved_challenges"])},
        reason=reason, signed_off=decision == "accept",
    )
    payload.update(human_review_status=status, reviewer=reviewer,
                   reviewed_at=reviewed_at.isoformat())
    row.payload = payload
    row.human_review_status = status
    session.flush()
    return {"handoff_id": handoff_id, "human_review_status": status,
            "review_id": review.review_id, "content_hash": row.content_hash}
