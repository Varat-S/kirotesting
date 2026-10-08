"""Deterministic semantic rubric engine (Remediation items 16-17).

An LLM must never assign an official risk band. Instead a narrow agent emits
STRUCTURED SEMANTIC OBSERVATIONS (e.g. ``market_position="leading"``,
``switching_costs="high"``), and this DETERMINISTIC, versioned rubric maps those
observations to a 1-4 risk-signal ``ParameterResult``. Likewise the covenant
protection score is derived from structured covenant-clause observations, never
an LLM-assigned ``0.7``.

The rubric is configuration (``scoring.json`` ``rubrics``), labelled illustrative,
and reproducible: same observations + rubric version => same signal.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.schemas.agentic import (
    AcceptanceState,
    Method,
    ParameterResult,
    ParameterStatus,
    Topic,
)


class RubricError(ValueError):
    """Raised when an observation value is not in the rubric's allowed set."""


class RubricEngine:
    """Map structured semantic observations to deterministic risk signals."""

    def __init__(self, rubrics: dict[str, Any], *, config_version: int,
                 config_hash: str) -> None:
        self._rubrics = rubrics
        self._version = config_version
        self._hash = config_hash

    def available(self) -> set[str]:
        return set(self._rubrics)

    def score(
        self,
        parameter_id: str,
        observations: dict[str, Any],
        *,
        analysis_run_id: str,
        topic: Topic,
        source_parameter_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
    ) -> ParameterResult:
        """Deterministically derive a 1-4 risk-signal ParameterResult.

        ``observations`` are the agent's structured semantic classifications.
        Unknown observation values raise ``RubricError`` (strict, no silent
        default). If NO observation dimension is present, the signal is
        ``unavailable`` rather than a fabricated band.
        """
        rubric = self._rubrics.get(parameter_id)
        if rubric is None:
            raise RubricError(f"No rubric for {parameter_id!r}.")
        dimensions = rubric["dimensions"]
        aggregate = rubric.get("aggregate", "max")

        bands: list[int] = []
        used: dict[str, int] = {}
        for dim, value in observations.items():
            if dim not in dimensions:
                continue  # ignore observations the rubric does not score
            mapping = dimensions[dim]
            key = str(value).lower() if isinstance(value, bool) else str(value)
            if key not in mapping:
                raise RubricError(
                    f"Observation {dim}={value!r} not in rubric for "
                    f"{parameter_id!r} (allowed: {sorted(mapping)})."
                )
            bands.append(int(mapping[key]))
            used[dim] = int(mapping[key])

        if not bands:
            return self._unavailable(parameter_id, analysis_run_id, topic,
                                     source_parameter_ids)

        if aggregate == "max":
            band = max(bands)  # worst dimension dominates (no washing out)
        else:  # weighted_average -> rounded
            band = max(1, min(4, round(sum(bands) / len(bands))))

        return ParameterResult(
            parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            parameter_id=parameter_id,
            topic=topic,
            value=band,
            value_type="risk_band",
            method=Method.DETERMINISTIC,
            status=ParameterStatus.OK,
            risk_signal=band,
            formula_id=f"rubric:{parameter_id}",
            formula_version=str(self._version),
            source_parameter_ids=list(source_parameter_ids or []),
            evidence_ids=list(evidence_ids or []),
            notes=f"rubric dimensions used: {used}",
            acceptance_state=AcceptanceState.ACCEPTED,
        )

    def _unavailable(self, parameter_id, analysis_run_id, topic, source_ids):
        return ParameterResult(
            parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
            analysis_run_id=analysis_run_id,
            parameter_id=parameter_id,
            topic=topic,
            value=None,
            value_type="risk_band",
            method=Method.DETERMINISTIC,
            status=ParameterStatus.UNAVAILABLE,
            formula_id=f"rubric:{parameter_id}",
            formula_version=str(self._version),
            source_parameter_ids=list(source_ids or []),
            missing_information=["no scored observation dimensions provided"],
            acceptance_state=AcceptanceState.ACCEPTED,
        )
