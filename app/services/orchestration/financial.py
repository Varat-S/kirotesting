"""Financial Orchestrator input contract (Remediation item 7).

The Financial Orchestrator consumes EXACTLY: accepted validated Financial
ParameterResults, the FinancialRiskScore, deterministic stress results, covenant
results, and the relevant conflicts / data limitations. It must NOT receive
arbitrary raw documents and MUST NOT recalculate ratios. This module builds that
strict input packet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.agentic import ParameterResult, RiskScore, ScoreKind, Topic
from app.services.orchestration.business import (
    OrchestratorInputError,
    _accepted_topic_params,
    _score_payload,
)


@dataclass(frozen=True)
class FinancialOrchestratorInput:
    analysis_run_id: str
    parameters: list[dict[str, Any]]
    score: dict[str, Any]
    stress_results: list[dict[str, Any]]
    covenant_results: list[dict[str, Any]]
    open_conflicts: list[dict[str, Any]]
    data_limitations: list[dict[str, Any]]

    def as_prompt_inputs(self) -> dict[str, Any]:
        return {
            "topic": "financial",
            "parameters": self.parameters,
            "score": self.score,
            "stress_results": self.stress_results,
            "covenant_results": self.covenant_results,
            "open_conflicts": self.open_conflicts,
            "data_limitations": self.data_limitations,
        }


def build_financial_orchestrator_input(
    *,
    analysis_run_id: str,
    parameters: list[ParameterResult],
    financial_score: RiskScore,
    stress_results: list[dict[str, Any]] | None = None,
    covenant_results: list[dict[str, Any]] | None = None,
    open_conflicts: list[dict[str, Any]] | None = None,
    data_limitations: list[dict[str, Any]] | None = None,
) -> FinancialOrchestratorInput:
    """Construct the strict Financial Orchestrator input (item 7)."""
    if financial_score.kind is not ScoreKind.FINANCIAL:
        raise OrchestratorInputError("Expected a FinancialRiskScore.")
    accepted = _accepted_topic_params(parameters, Topic.FINANCIAL, analysis_run_id)
    return FinancialOrchestratorInput(
        analysis_run_id=analysis_run_id,
        parameters=accepted,
        score=_score_payload(financial_score),
        stress_results=list(stress_results or []),
        covenant_results=list(covenant_results or []),
        open_conflicts=list(open_conflicts or []),
        data_limitations=list(data_limitations or []),
    )
