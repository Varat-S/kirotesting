"""Unit tests for the non-destructive human-review workflow (task 7.1, Req 15)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.orm import AuditEvent, Case
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.review.workflow import (
    GATED_ACTIONS,
    HumanReviewWorkflow,
    ReviewAction,
)


def _case(session: Session, case_id: str = "DAL_2024") -> Case:
    case = Case(case_id=case_id)
    session.add(case)
    session.flush()
    return case


def test_action_is_recorded_with_full_field_set(db_session: Session) -> None:
    """Req 15.2: reviewer, timestamp, prior/new value, reason, evidence, comment."""
    _case(db_session)
    audit = AuditLog(db_session)
    wf = HumanReviewWorkflow(db_session, audit=audit, case_id="DAL_2024")

    review = wf.record_action(
        ReviewAction.VERIFY_SOURCE,
        reviewer="analyst@bank",
        target_type="fact",
        target_id="fact-1",
        prior_value={"status": "unverified"},
        new_value={"status": "verified"},
        reason="Tied to audited 10-K.",
        linked_evidence=["doc:10k#p42"],
        comment="Looks good.",
    )

    assert review.reviewer == "analyst@bank"
    assert review.reviewed_at is not None
    assert review.prior_value == {"status": "unverified"}
    assert review.new_value == {"status": "verified"}
    assert review.reason == "Tied to audited 10-K."
    assert review.linked_evidence == ["doc:10k#p42"]
    assert review.comment == "Looks good."


def test_edits_are_non_destructive_with_recoverable_before_after(
    db_session: Session,
) -> None:
    """Req 15.3: before/after remain visible; nothing overwritten (task 7.3)."""
    _case(db_session)
    wf = HumanReviewWorkflow(db_session, case_id="DAL_2024")

    wf.record_action(
        ReviewAction.MODIFY_RISK_MATERIALITY,
        reviewer="r1",
        target_type="risk",
        target_id="risk-7",
        prior_value={"materiality": "medium"},
        new_value={"materiality": "high"},
        reason="Fuel exposure understated.",
        signed_off=True,
    )
    wf.record_action(
        ReviewAction.MODIFY_RISK_MATERIALITY,
        reviewer="r2",
        target_type="risk",
        target_id="risk-7",
        prior_value={"materiality": "high"},
        new_value={"materiality": "medium"},
        reason="Hedging program mitigates.",
        signed_off=True,
    )

    trail = wf.history_for_target("risk", "risk-7")
    assert len(trail) == 2
    # Both before/after states remain recoverable in order.
    assert trail[0].prior_value == {"materiality": "medium"}
    assert trail[0].new_value == {"materiality": "high"}
    assert trail[1].prior_value == {"materiality": "high"}
    assert trail[1].new_value == {"materiality": "medium"}


def test_each_action_emits_a_human_review_event(db_session: Session) -> None:
    """Req 15: every human action emits an append-only human_review event."""
    _case(db_session)
    audit = AuditLog(db_session)
    wf = HumanReviewWorkflow(db_session, audit=audit, case_id="DAL_2024")

    wf.record_action(
        ReviewAction.CONFIRM_PEER_SET,
        reviewer="analyst",
        reason="Cohort confirmed.",
    )

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.event_type == "human_review")
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_id == "analyst"


def test_all_required_actions_supported(db_session: Session) -> None:
    """Req 15.1: the full reviewer action set is representable."""
    _case(db_session)
    wf = HumanReviewWorkflow(db_session, case_id="DAL_2024")
    for action in ReviewAction:
        row = wf.record_action(action, reviewer="r", signed_off=True)
        assert row.action == action.value


def test_gated_actions_flagged_requires_sign_off(db_session: Session) -> None:
    """Req 15.4: gated actions are flagged as requiring sign-off."""
    _case(db_session)
    wf = HumanReviewWorkflow(db_session, case_id="DAL_2024")

    gated = wf.record_action(
        ReviewAction.APPROVE_ACCOUNTING_ADJUSTMENT, reviewer="r", signed_off=True
    )
    non_gated = wf.record_action(ReviewAction.VERIFY_SOURCE, reviewer="r")

    assert gated.requires_sign_off is True
    assert gated.signed_off is True
    assert non_gated.requires_sign_off is False
    assert ReviewAction.APPROVE_ACCOUNTING_ADJUSTMENT in GATED_ACTIONS
    assert ReviewAction.VERIFY_SOURCE not in GATED_ACTIONS


def test_has_sign_off_only_true_after_explicit_sign_off(db_session: Session) -> None:
    """Req 15.6: explicit sign-off of the final recommendation is detectable."""
    _case(db_session)
    wf = HumanReviewWorkflow(db_session, case_id="DAL_2024")

    assert wf.has_sign_off() is False
    # A non-signed-off gated action does not count as sign-off.
    wf.record_action(ReviewAction.RESOLVE_CONFLICT, reviewer="r", signed_off=False)
    assert wf.has_sign_off() is False

    wf.sign_off_recommendation(reviewer="credit.officer", reason="Approved.")
    assert wf.has_sign_off() is True


def test_unresolved_exceptions_reports_open_escalations(db_session: Session) -> None:
    """Req 15.5: the final output identifies unresolved exceptions."""
    _case(db_session)
    audit = AuditLog(db_session)
    engine = EscalationEngine(db_session, audit=audit, case_id="DAL_2024")
    engine.raise_escalation(
        rule_id="R-DATA-01",
        category=EscalationCategory.DATA_INTEGRITY,
        severity=Severity.MANDATORY,
        reason="Conflicting EBITDA.",
    )

    wf = HumanReviewWorkflow(db_session, audit=audit, case_id="DAL_2024")
    unresolved = wf.unresolved_exceptions()
    assert len(unresolved) == 1
    assert unresolved[0]["rule_id"] == "R-DATA-01"
    assert unresolved[0]["mandatory"] is True
    assert wf.has_unresolved_mandatory_exceptions() is True
