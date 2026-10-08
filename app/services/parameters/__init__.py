"""Deterministic Parameter Engine (Milestone 7).

Extends the existing deterministic metric stack (``MetricEngine`` /
``MetricDefinitionRegistry`` / ``TrendAnalyzer`` / ``PeerBenchmarker``) with
versioned, non-financial-metric calculations — concentration / HHI, trends,
stress and structuring measures — all producing typed ``ParameterResult``s with
``method="deterministic"``. No component here calls an LLM; a model-proposed
calculation with no registered formula yields a
``ParameterResult(status="proposed_new_calculation")`` that cannot enter scoring.
"""

from __future__ import annotations

from app.services.parameters.engine import (
    ParameterComputation,
    ParameterEngine,
)
from app.services.parameters.registry import (
    ParameterDefinition,
    ParameterDefinitionRegistry,
    default_parameter_definitions,
)

__all__ = [
    "ParameterEngine",
    "ParameterComputation",
    "ParameterDefinition",
    "ParameterDefinitionRegistry",
    "default_parameter_definitions",
]
