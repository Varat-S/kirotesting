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

from app.services.parameters.business import business_parameter_definitions
from app.services.parameters.engine import (
    ParameterComputation,
    ParameterEngine,
)
from app.services.parameters.financial import financial_parameter_definitions
from app.services.parameters.metric_adapter import metric_to_parameter
from app.services.parameters.registry import (
    ParameterDefinition,
    ParameterDefinitionRegistry,
    default_parameter_definitions,
)
from app.services.parameters.structuring import structuring_parameter_definitions

__all__ = [
    "ParameterEngine",
    "ParameterComputation",
    "ParameterDefinition",
    "ParameterDefinitionRegistry",
    "default_parameter_definitions",
    "business_parameter_definitions",
    "financial_parameter_definitions",
    "structuring_parameter_definitions",
    "metric_to_parameter",
]
