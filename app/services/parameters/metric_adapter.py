"""Adapter: existing MetricResult -> ParameterResult (Milestone 7).

The baseline deterministic ``MetricEngine`` already computes the core
financial-statement ratios (revenue growth, operating margin, net debt, net
debt/EBITDA, interest coverage, cash conversion, FCF, liquidity, capex/revenue,
load factor, CASM) with versioned definitions and explicit non-numeric states.
Rather than DUPLICATE those formulas in the parameter engine, this adapter wraps
a ``MetricResult`` as a deterministic ``ParameterResult`` so metric outputs flow
through the single typed parameter layer unchanged.

Deterministic method is preserved: the ParameterResult carries the metric's
``metric_definition_id``/version as ``formula_id``/``formula_version`` and its
input fact IDs, and never an ``agent_run_id``.
"""

from __future__ import annotations

import uuid

from app.schemas.agentic import (
    AcceptanceState,
    Method,
    ParameterResult,
    ParameterStatus,
    Topic,
)
from app.services.metrics.engine import MetricResult, MetricState

_STATE_TO_STATUS = {
    MetricState.OK: ParameterStatus.OK,
    MetricState.NOT_MEANINGFUL: ParameterStatus.UNAVAILABLE,
    MetricState.MISSING_INPUT: ParameterStatus.UNAVAILABLE,
    MetricState.REQUIRES_REVIEW: ParameterStatus.REQUIRES_REVIEW,
}


def metric_to_parameter(
    metric: MetricResult,
    *,
    analysis_run_id: str,
    topic: Topic = Topic.FINANCIAL,
    parameter_id: str | None = None,
) -> ParameterResult:
    """Wrap a computed ``MetricResult`` as a deterministic ``ParameterResult``."""
    status = _STATE_TO_STATUS.get(metric.state, ParameterStatus.REQUIRES_REVIEW)
    return ParameterResult(
        parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
        analysis_run_id=analysis_run_id,
        parameter_id=parameter_id or metric.metric_name,
        topic=topic,
        value=metric.result,
        value_type="ratio",
        method=Method.DETERMINISTIC,
        status=status,
        source_fact_ids=list(metric.input_fact_ids),
        # Reuse the metric's versioned identity as the parameter's formula ident.
        formula_id=metric.metric_definition_id,
        formula_version=str(metric.metric_definition_version),
        notes=metric.detail if status is ParameterStatus.REQUIRES_REVIEW else None,
        missing_information=(
            [metric.detail] if status is ParameterStatus.UNAVAILABLE and metric.detail
            else []
        ),
        acceptance_state=AcceptanceState.ACCEPTED,
    )
