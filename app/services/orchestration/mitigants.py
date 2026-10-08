"""Mitigant eligibility barrier (Remediation item 10).

The four risk-to-mitigant agents require an explicit, accepted deterministic
precondition — not just the two topic challengers. They become eligible only
when ALL of the following hold:

* an accepted BusinessConclusion exists,
* an accepted FinancialConclusion exists,
* an accepted ObligorRiskScore exists AND is not ``unavailable``.

Deterministic scoring is not an LLM agent, so this barrier is enforced here (by
the orchestrator/scheduler) rather than as a DAG AgentDefinition dependency. If
the precondition is not met, the mitigant agents do NOT run and the structuring
path records an explicit blocked/unavailable state (human review where required).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.agentic import RiskScore, ScoreKind, ScoreStatus, Topic, TopicConclusion


@dataclass(frozen=True)
class MitigantEligibility:
    eligible: bool
    reason: str


def mitigants_eligible(
    *,
    analysis_run_id: str,
    conclusions: list[TopicConclusion],
    obligor_score: RiskScore | None,
) -> MitigantEligibility:
    """Decide whether the risk-to-mitigant agents may run (item 10)."""
    def _accepted(topic: Topic) -> bool:
        return any(
            c.topic is topic
            and c.analysis_run_id == analysis_run_id
            and c.acceptance_state.value == "accepted"
            for c in conclusions
        )

    if not _accepted(Topic.BUSINESS):
        return MitigantEligibility(False, "no accepted BusinessConclusion")
    if not _accepted(Topic.FINANCIAL):
        return MitigantEligibility(False, "no accepted FinancialConclusion")
    if obligor_score is None:
        return MitigantEligibility(False, "no ObligorRiskScore")
    if obligor_score.kind is not ScoreKind.OBLIGOR:
        return MitigantEligibility(False, "score is not an ObligorRiskScore")
    if obligor_score.analysis_run_id != analysis_run_id:
        return MitigantEligibility(False, "ObligorRiskScore belongs to another run")
    if obligor_score.acceptance_state.value != "accepted":
        return MitigantEligibility(False, "ObligorRiskScore not accepted")
    if obligor_score.status is ScoreStatus.UNAVAILABLE:
        return MitigantEligibility(False, "ObligorRiskScore unavailable")
    return MitigantEligibility(True, "business+financial accepted; obligor score ready")
