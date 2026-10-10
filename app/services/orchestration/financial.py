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
    # Bounded industry-benchmark context (validated comparisons, compatibility
    # flags, source metadata, accepted parameter-result ids). Absent for a case
    # without a sector benchmark, so its prompt inputs stay byte-identical.
    benchmark_context: dict[str, Any] | None = None

    def as_prompt_inputs(self) -> dict[str, Any]:
        inputs = {
            "topic": "financial",
            "parameters": self.parameters,
            "score": self.score,
            "stress_results": self.stress_results,
            "covenant_results": self.covenant_results,
            "open_conflicts": self.open_conflicts,
            "data_limitations": self.data_limitations,
        }
        if self.benchmark_context is not None:
            inputs["benchmark_context"] = self.benchmark_context
        return inputs


def build_financial_orchestrator_input(
    *,
    analysis_run_id: str,
    parameters: list[ParameterResult],
    financial_score: RiskScore,
    stress_results: list[dict[str, Any]] | None = None,
    covenant_results: list[dict[str, Any]] | None = None,
    open_conflicts: list[dict[str, Any]] | None = None,
    data_limitations: list[dict[str, Any]] | None = None,
    benchmark_context: dict[str, Any] | None = None,
) -> FinancialOrchestratorInput:
    """Construct the strict Financial Orchestrator input (item 7).

    ``benchmark_context`` must satisfy the bounded-context contract and every
    parameter-result id it names must be an accepted Financial parameter of this
    run; the workbook, raw cells or an unvalidated aggregate cannot get through.
    """
    if financial_score.kind is not ScoreKind.FINANCIAL:
        raise OrchestratorInputError("Expected a FinancialRiskScore.")
    accepted = _accepted_topic_params(parameters, Topic.FINANCIAL, analysis_run_id)
    if benchmark_context is not None:
        from app.services.orchestration.benchmark_context import (
            BenchmarkContextError,
            validate_benchmark_context,
        )

        try:
            validate_benchmark_context(benchmark_context)
        except BenchmarkContextError as exc:
            raise OrchestratorInputError(str(exc)) from exc
        accepted_ids = {p["parameter_result_id"] for p in accepted}
        for entry in benchmark_context["comparisons"]:
            for part in ("borrower", "industry_aggregate", "difference"):
                node = entry.get(part)
                prid = node.get("parameter_result_id") if node else None
                if prid is not None and prid not in accepted_ids:
                    raise OrchestratorInputError(
                        f"Benchmark comparison {entry['comparison_id']!r} references "
                        f"ParameterResult {prid!r} that is not an accepted Financial "
                        "parameter of this run."
                    )
    return FinancialOrchestratorInput(
        analysis_run_id=analysis_run_id,
        parameters=accepted,
        score=_score_payload(financial_score),
        stress_results=list(stress_results or []),
        covenant_results=list(covenant_results or []),
        open_conflicts=list(open_conflicts or []),
        data_limitations=list(data_limitations or []),
        benchmark_context=benchmark_context,
    )
