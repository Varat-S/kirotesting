"""Business Orchestrator input contract (Remediation item 7).

The Business Orchestrator consumes EXACTLY: accepted validated Business
ParameterResults, the BusinessRiskScore, and the relevant open conflicts / data
limitations / evidence-quality notes. It must NOT receive arbitrary raw
documents and MUST NOT recalculate anything. This module builds that strict
input packet; anything outside the contract is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.agentic import ParameterResult, RiskScore, ScoreKind, Topic


class OrchestratorInputError(ValueError):
    """Raised when the orchestrator input contract is violated."""


@dataclass(frozen=True)
class BusinessOrchestratorInput:
    analysis_run_id: str
    parameters: list[dict[str, Any]]
    score: dict[str, Any]
    open_conflicts: list[dict[str, Any]]
    data_limitations: list[dict[str, Any]]
    # Additional VALIDATED structure (Business Overview v2): hypothesis records,
    # contradictions, duplicate groups, resilience findings, deterministic
    # context. Only the named keys are accepted, so raw documents cannot ride in.
    extras: dict[str, Any] = field(default_factory=dict)

    def as_prompt_inputs(self) -> dict[str, Any]:
        return {
            "topic": "business",
            "parameters": self.parameters,
            "score": self.score,
            "open_conflicts": self.open_conflicts,
            "data_limitations": self.data_limitations,
            **self.extras,
        }


# The only extra input sections the Business Orchestrator may receive.
ALLOWED_ORCHESTRATOR_EXTRAS = frozenset({
    "data_notice", "sections", "hypotheses", "audit_only_hypotheses",
    "duplicate_groups", "contradictions", "resilience_findings", "indicators",
    "coverage", "score_explanation", "challenge_feedback",
})


def build_business_orchestrator_input(
    *,
    analysis_run_id: str,
    parameters: list[ParameterResult],
    business_score: RiskScore,
    open_conflicts: list[dict[str, Any]] | None = None,
    data_limitations: list[dict[str, Any]] | None = None,
    extras: dict[str, Any] | None = None,
) -> BusinessOrchestratorInput:
    """Construct the strict Business Orchestrator input (item 7)."""
    if business_score.kind is not ScoreKind.BUSINESS:
        raise OrchestratorInputError("Expected a BusinessRiskScore.")
    unknown = set(extras or {}) - ALLOWED_ORCHESTRATOR_EXTRAS
    if unknown:
        raise OrchestratorInputError(
            f"Unsupported orchestrator input section(s): {sorted(unknown)}."
        )
    accepted = _accepted_topic_params(parameters, Topic.BUSINESS, analysis_run_id)
    return BusinessOrchestratorInput(
        analysis_run_id=analysis_run_id,
        parameters=accepted,
        score=_score_payload(business_score),
        open_conflicts=list(open_conflicts or []),
        data_limitations=list(data_limitations or []),
        extras=dict(extras or {}),
    )


def _accepted_topic_params(
    parameters: list[ParameterResult], topic: Topic, run_id: str
) -> list[dict[str, Any]]:
    out = []
    for p in parameters:
        if p.topic is not topic:
            continue
        if p.analysis_run_id != run_id:
            raise OrchestratorInputError(
                f"Parameter {p.parameter_result_id} from a different run."
            )
        if p.acceptance_state.value != "accepted":
            continue  # never feed a superseded/rejected parameter
        out.append(
            {
                "parameter_result_id": p.parameter_result_id,
                "parameter_id": p.parameter_id,
                "value": p.value,
                "status": p.status.value,
                "risk_signal": p.risk_signal,
                "evidence_quality": (p.evidence_quality.value
                                     if p.evidence_quality else None),
                "evidence_ids": list(p.evidence_ids),
            }
        )
    return out


def _score_payload(score: RiskScore) -> dict[str, Any]:
    return {
        "score_id": score.score_id,
        "kind": score.kind.value,
        "status": score.status.value,
        "band": score.band,
    }
