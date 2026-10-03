"""Challenge layer (task 6.5; Req 13.1-13.5).

A second-pass critic that is strictly NON-DESTRUCTIVE: it NEVER mutates or
overwrites the original :class:`AnalysisResponse` (Req 13.3). It:

* runs the ``challenge`` prompt through the single LLMClient and uses its
  schema-validated output (Req 13.2); challenges carry ``claim_id, issue_type,
  severity, reason, evidence_refs``;
* logs every accept/reject decision on a challenge (Req 13.4);
* routes HIGH-severity challenges into the escalation queue via the Milestone 5
  engine with an explicit ``rule_id`` (Req 13.5) and a ``challenge_created``
  audit event.

The challenge tests themselves (support / metric-contradiction / omitted-risk /
mitigant-relevance / missing-evidence / alternatives / overstated-certainty) are
enumerated in :class:`~app.schemas.llm.IssueType`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.llm import (
    AnalysisResponse,
    Challenge,
    ChallengeResponse,
    ChallengeSeverity,
)
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.llm.client import LLMClient, ModelRunResult

# Explicit rule id for a high-severity challenge entering the escalation queue.
RULE_HIGH_SEVERITY_CHALLENGE = "R-CHALLENGE-HIGH-01"


class ChallengeRejectedError(ValueError):
    """Raised when the challenge LLM output fails schema validation."""


@dataclass
class ChallengeOutcome:
    """The result of a challenge pass (additive; analysis left untouched)."""

    challenges: list[Challenge]
    run: ModelRunResult
    escalation_ids: list[str] = field(default_factory=list)

    def high_severity(self) -> list[Challenge]:
        return [c for c in self.challenges if c.severity is ChallengeSeverity.HIGH]


class ChallengeService:
    """Run the critic pass over an analysis without mutating it."""

    def __init__(
        self,
        client: LLMClient,
        *,
        escalation_engine: EscalationEngine | None = None,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._escalation = escalation_engine
        self._audit = audit
        self._case_id = case_id

    def challenge(
        self,
        analysis: AnalysisResponse,
        inputs: dict | None = None,
        *,
        case_version: int | None = None,
        key: str | None = None,
    ) -> ChallengeOutcome:
        """Produce challenges against ``analysis`` without changing it.

        ``analysis`` is read-only input here: this method returns a NEW outcome
        object and never writes back to ``analysis`` (Req 13.3). High-severity
        challenges are escalated (Req 13.5).
        """
        payload = dict(inputs or {})
        payload["analysis"] = analysis.model_dump(mode="json")
        evidence_ids = [c.claim_id for c in analysis.all_claims()]

        run = self._client.challenge(
            payload,
            evidence_ids=evidence_ids,
            case_id=self._case_id,
            case_version=case_version,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            raise ChallengeRejectedError(
                "Challenge LLM output failed schema validation and was rejected "
                "before use (Req 13.2)."
            )

        response = ChallengeResponse.model_validate(run.parsed)
        escalation_ids: list[str] = []
        for challenge in response.challenges:
            if self._audit is not None:
                self._audit.record(
                    EventType.CHALLENGE_CREATED,
                    case_id=self._case_id,
                    actor_type=ActorType.MODEL,
                    actor_id=run.model_id,
                    after={
                        "claim_id": challenge.claim_id,
                        "issue_type": challenge.issue_type.value,
                        "severity": challenge.severity.value,
                        "reason": challenge.reason,
                        "evidence_refs": list(challenge.evidence_refs),
                    },
                    reason=challenge.reason,
                    linked_objects=[f"claim:{challenge.claim_id}", f"model_run:{run.run_id}"],
                )
            if challenge.severity is ChallengeSeverity.HIGH and self._escalation:
                escalation = self._escalation.raise_escalation(
                    rule_id=RULE_HIGH_SEVERITY_CHALLENGE,
                    category=EscalationCategory.AI_DETERMINISTIC_CONFLICT,
                    severity=Severity.MANDATORY,
                    reason=(
                        f"High-severity challenge ({challenge.issue_type.value}) on "
                        f"claim {challenge.claim_id!r}: {challenge.reason}"
                    ),
                    evidence_refs=[challenge.claim_id, *challenge.evidence_refs],
                    case_id=self._case_id,
                )
                escalation_ids.append(escalation.escalation_id)

        return ChallengeOutcome(
            challenges=list(response.challenges),
            run=run,
            escalation_ids=escalation_ids,
        )

    # -- accept/reject logging (Req 13.4) -------------------------------------

    def log_decision(
        self,
        challenge: Challenge,
        *,
        decision: str,
        decided_by: str,
        reason: str | None = None,
    ) -> None:
        """Log a human accept/reject decision on a challenge (Req 13.4).

        ``decision`` is ``accepted`` or ``rejected``. Logging is additive and
        does not alter the analysis or the challenge record.
        """
        if decision not in {"accepted", "rejected"}:
            raise ValueError("decision must be 'accepted' or 'rejected'.")
        if self._audit is None:
            return
        self._audit.record(
            EventType.HUMAN_REVIEW,
            case_id=self._case_id,
            actor_type=ActorType.HUMAN,
            actor_id=decided_by,
            before={"challenge": challenge.model_dump(mode="json")},
            after={"decision": decision},
            reason=reason or f"Challenge {decision} by reviewer.",
            linked_objects=[f"claim:{challenge.claim_id}"],
        )
