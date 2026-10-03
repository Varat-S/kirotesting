"""Golden deterministic metric examples (Req 8.1, 8.5, 8.6; task 5.7).

These are the authoritative worked examples the metric engine MUST match 100%
and reproduce identically on re-run under the same definition/engine versions.
Each example states its inputs, the metric, and the expected result OR explicit
state (bad-denominator states are first-class expected outcomes).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.schemas.enums import FactStatus
from app.services.metrics.definitions import MetricDefinitionRegistry
from app.services.metrics.engine import MetricEngine, MetricInput, MetricState

# Each golden case: metric id, {input: value|None(with status)}, expected.
# A value of None with a status marks a non-value input.
GOLDEN_CASES: list[dict] = [
    {
        "id": "net_debt_to_ebitda_normal",
        "metric": "net_debt_to_ebitda",
        "inputs": {"total_debt": 7000.0, "unrestricted_cash": 1000.0, "ebitda": 2000.0},
        "expected_result": 3.0,
    },
    {
        "id": "operating_margin_normal",
        "metric": "operating_margin",
        "inputs": {"operating_income": 250.0, "revenue": 1000.0},
        "expected_result": 0.25,
    },
    {
        "id": "net_debt_additive",
        "metric": "net_debt",
        "inputs": {"total_debt": 5000.0, "unrestricted_cash": 1500.0},
        "expected_result": 3500.0,
    },
    {
        "id": "interest_coverage_normal",
        "metric": "interest_coverage",
        "inputs": {"ebitda": 1800.0, "interest_expense": 600.0},
        "expected_result": 3.0,
    },
    {
        "id": "revenue_growth_normal",
        "metric": "revenue_growth",
        "inputs": {"revenue": 1100.0, "revenue_prior": 1000.0},
        "expected_result": 0.1,
    },
    {
        "id": "free_cash_flow_normal",
        "metric": "free_cash_flow",
        "inputs": {"cfo": 900.0, "capex": 400.0},
        "expected_result": 500.0,
    },
    {
        "id": "capex_to_revenue_normal",
        "metric": "capex_to_revenue",
        "inputs": {"capex": 150.0, "revenue": 1000.0},
        "expected_result": 0.15,
    },
    {
        "id": "load_factor_normal",
        "metric": "load_factor",
        "inputs": {"rpm": 850.0, "asm": 1000.0},
        "expected_result": 0.85,
    },
    {
        "id": "leverage_zero_ebitda_requires_review",
        "metric": "net_debt_to_ebitda",
        "inputs": {"total_debt": 7000.0, "unrestricted_cash": 1000.0, "ebitda": 0.0},
        "expected_state": MetricState.REQUIRES_REVIEW,
    },
    {
        "id": "coverage_missing_interest_missing_input",
        "metric": "interest_coverage",
        "inputs": {"ebitda": 1800.0, "interest_expense": ("missing", None)},
        "expected_state": MetricState.MISSING_INPUT,
    },
]


def _build_inputs(raw: dict) -> dict[str, MetricInput]:
    out: dict[str, MetricInput] = {}
    for name, spec in raw.items():
        if isinstance(spec, tuple):  # ("missing", None) style non-value input
            out[name] = MetricInput.missing(name)
        else:
            out[name] = MetricInput(name, float(spec), FactStatus.VERIFIED, name)
    return out


@pytest.fixture()
def registry(db_session: Session) -> MetricDefinitionRegistry:
    reg = MetricDefinitionRegistry(db_session)
    reg.register_defaults()
    return reg


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c["id"])
def test_golden_metric_examples(
    case: dict, registry: MetricDefinitionRegistry
) -> None:
    """Req 8.5: 100% pass on golden metric examples."""
    engine = MetricEngine(near_zero_floor=1.0)
    definition = registry.latest(case["metric"])
    result = engine.compute(
        definition, _build_inputs(case["inputs"]), metric_name=case["metric"]
    )
    if "expected_result" in case:
        assert result.state is MetricState.OK
        assert result.result == pytest.approx(case["expected_result"])
    else:
        assert result.state is case["expected_state"]
        assert result.result is None


def test_golden_examples_reproduce_identically(
    registry: MetricDefinitionRegistry,
) -> None:
    """Req 8.6: re-run under the same versions produces identical payloads."""
    engine = MetricEngine(near_zero_floor=1.0)
    for case in GOLDEN_CASES:
        definition = registry.latest(case["metric"])
        a = engine.compute(definition, _build_inputs(case["inputs"]), metric_name=case["metric"])
        b = engine.compute(definition, _build_inputs(case["inputs"]), metric_name=case["metric"])
        assert a.as_payload() == b.as_payload()
