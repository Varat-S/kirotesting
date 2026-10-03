"""Metrics service: deterministic metric library and versioned
metric-definition registry with explicit bad-denominator states.

Public API (Milestone 5):

* :mod:`definitions` -- versioned metric-definition registry (task 5.1).
* :mod:`engine` -- deterministic metric engine with explicit bad-denominator
  states ``not_meaningful|missing_input|requires_review`` (task 5.2).
* :mod:`trends` -- deterministic historical trend layer with configured
  adverse-direction definitions and visible structural breaks (task 5.3).
"""

from app.services.metrics.definitions import (
    DEFAULT_METRIC_DEFINITIONS,
    ILLUSTRATIVE_LABEL,
    MetricDefinitionRegistry,
    ResolvedMetricDefinition,
)
from app.services.metrics.engine import (
    ENGINE_VERSION,
    FORMULA_LIBRARY,
    MetricEngine,
    MetricInput,
    MetricResult,
    MetricState,
)
from app.services.metrics.trends import (
    DEFAULT_ADVERSE_DIRECTIONS,
    Direction,
    TrendAnalyzer,
    TrendPoint,
    TrendResult,
)

__all__ = [
    "DEFAULT_ADVERSE_DIRECTIONS",
    "DEFAULT_METRIC_DEFINITIONS",
    "ENGINE_VERSION",
    "FORMULA_LIBRARY",
    "ILLUSTRATIVE_LABEL",
    "Direction",
    "MetricDefinitionRegistry",
    "MetricEngine",
    "MetricInput",
    "MetricResult",
    "MetricState",
    "ResolvedMetricDefinition",
    "TrendAnalyzer",
    "TrendPoint",
    "TrendResult",
]
