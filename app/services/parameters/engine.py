"""Deterministic Parameter Engine (Milestone 7).

Computes a registered deterministic parameter from resolved numeric inputs and
emits a typed ``ParameterResult`` with ``method="deterministic"``, a versioned
``formula_id`` / ``formula_version`` and the exact source fact IDs. Explicit
non-numeric states (missing/not-meaningful/requires-review) map to a
``ParameterStatus`` so a bad denominator or missing input never yields a
misleading number.

A ``parameter_id`` with NO registered definition yields a
``ParameterResult(status="proposed_new_calculation")`` which, by construction,
cannot enter scoring (Req 3.5). The engine NEVER executes LLM-generated code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.schemas.agentic import (
    AcceptanceState,
    Method,
    ParameterResult,
    ParameterStatus,
)
from app.services.metrics.engine import MetricState
from app.services.parameters.formulas import (
    FORMULA_LIBRARY,
    PARAMETER_ENGINE_VERSION,
)
from app.services.parameters.registry import ParameterDefinitionRegistry

_STATE_TO_STATUS = {
    MetricState.OK: ParameterStatus.OK,
    MetricState.NOT_MEANINGFUL: ParameterStatus.UNAVAILABLE,
    MetricState.MISSING_INPUT: ParameterStatus.UNAVAILABLE,
    MetricState.REQUIRES_REVIEW: ParameterStatus.REQUIRES_REVIEW,
}


@dataclass(frozen=True)
class ParameterComputation:
    """A computed parameter plus its explicit calculation state (for inspection)."""

    result: ParameterResult
    state: MetricState
    detail: str | None


class ParameterEngine:
    """Compute deterministic parameters as typed ``ParameterResult``s."""

    engine_version = PARAMETER_ENGINE_VERSION

    def __init__(
        self,
        registry: ParameterDefinitionRegistry | None = None,
        *,
        near_zero_floor: float = 1.0,
    ) -> None:
        self._registry = registry or ParameterDefinitionRegistry()
        self._floor = float(near_zero_floor)

    def compute(
        self,
        parameter_id: str,
        inputs: dict[str, Any],
        *,
        analysis_run_id: str,
        source_fact_ids: list[str] | None = None,
        input_hash: str | None = None,
    ) -> ParameterComputation:
        """Compute ``parameter_id`` from ``inputs``; emit a ParameterResult."""
        definition = self._registry.get(parameter_id)
        if definition is None:
            return self._proposed_new_calculation(
                parameter_id, analysis_run_id, inputs, source_fact_ids
            )

        formula = FORMULA_LIBRARY[definition.formula_id]
        value, state, detail = formula(inputs, self._floor)
        status = _STATE_TO_STATUS[state]
        result = ParameterResult(
            parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            parameter_id=parameter_id,
            topic=definition.topic,
            value=value,
            value_type=definition.value_type,
            method=Method.DETERMINISTIC,
            status=status,
            formula_id=definition.formula_id,
            formula_version=definition.formula_version,
            source_fact_ids=list(source_fact_ids or []),
            input_hash=input_hash,
            missing_information=([detail] if status is ParameterStatus.UNAVAILABLE
                                 and detail else []),
            notes=detail if status is ParameterStatus.REQUIRES_REVIEW else None,
            acceptance_state=AcceptanceState.ACCEPTED,
        )
        return ParameterComputation(result=result, state=state, detail=detail)

    def _proposed_new_calculation(
        self,
        parameter_id: str,
        analysis_run_id: str,
        inputs: dict[str, Any],
        source_fact_ids: list[str] | None,
    ) -> ParameterComputation:
        result = ParameterResult(
            parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            parameter_id=parameter_id,
            topic=_infer_topic(inputs),
            value=None,
            value_type="unknown",
            method=Method.DETERMINISTIC,
            status=ParameterStatus.PROPOSED_NEW_CALCULATION,
            formula_id="unregistered",
            formula_version="0",
            source_fact_ids=list(source_fact_ids or []),
            notes=(
                f"No registered deterministic formula for {parameter_id!r}; "
                "requires implementation + review before it can affect analysis."
            ),
            acceptance_state=AcceptanceState.ACCEPTED,
        )
        return ParameterComputation(
            result=result, state=MetricState.REQUIRES_REVIEW, detail=result.notes
        )


def _infer_topic(inputs: dict[str, Any]):
    from app.schemas.agentic import Topic

    topic = inputs.get("topic")
    try:
        return Topic(topic) if topic else Topic.FINANCIAL
    except ValueError:
        return Topic.FINANCIAL
