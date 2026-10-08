"""Milestone 7 — deterministic Parameter Engine (Req 7, 3.1, 3.4, 3.5).

Proves: concentration/HHI/trend/stress formulas compute correctly; deterministic
replay is identical; zero/near-zero/missing yield explicit non-numeric states
(never a misleading number); an unregistered parameter becomes
proposed_new_calculation and cannot enter scoring.
"""

from __future__ import annotations

import pytest

from app.schemas.agentic import Method, ParameterStatus, Topic
from app.services.parameters import ParameterEngine
from app.services.parameters.registry import (
    ParameterDefinition,
    ParameterDefinitionRegistry,
)


def _engine() -> ParameterEngine:
    return ParameterEngine()


def test_hhi_fully_concentrated():
    out = _engine().compute("customer_hhi", {"shares": [100]}, analysis_run_id="AR1")
    assert out.result.status is ParameterStatus.OK
    assert out.result.value == pytest.approx(1.0)
    assert out.result.method is Method.DETERMINISTIC
    assert out.result.formula_id == "hhi"
    assert out.result.topic is Topic.BUSINESS


def test_hhi_evenly_split():
    out = _engine().compute("segment_hhi", {"shares": [25, 25, 25, 25]},
                            analysis_run_id="AR1")
    assert out.result.value == pytest.approx(0.25)  # 4 * (0.25^2)


def test_top5_concentration():
    out = _engine().compute(
        "top5_customer_concentration",
        {"shares": [50, 20, 10, 10, 5, 3, 2], "n": 5},
        analysis_run_id="AR1",
    )
    # top 5 = 50+20+10+10+5 = 95 of 100
    assert out.result.value == pytest.approx(0.95)


def test_cagr():
    out = _engine().compute(
        "revenue_cagr", {"begin": 100.0, "end": 133.1, "periods": 3},
        analysis_run_id="AR1",
    )
    assert out.result.value == pytest.approx(0.1, abs=1e-3)  # ~10%


def test_cagr_non_positive_begin_is_not_meaningful():
    out = _engine().compute(
        "revenue_cagr", {"begin": 0.0, "end": 50.0, "periods": 3},
        analysis_run_id="AR1",
    )
    assert out.result.status is ParameterStatus.UNAVAILABLE
    assert out.result.value is None  # never a misleading number


def test_safe_ratio_near_zero_denominator_requires_review():
    out = _engine().compute(
        "fcf_conversion", {"numerator": 10.0, "denominator": 0.0},
        analysis_run_id="AR1",
    )
    assert out.result.status is ParameterStatus.REQUIRES_REVIEW
    assert out.result.value is None


def test_missing_input_is_unavailable_not_zero():
    out = _engine().compute("fcf_conversion", {"numerator": 10.0},
                            analysis_run_id="AR1")
    assert out.result.status is ParameterStatus.UNAVAILABLE
    assert out.result.value is None
    assert out.result.missing_information  # records which input was missing


def test_volatility_near_zero_mean_requires_review():
    out = _engine().compute("revenue_volatility", {"series": [1.0, -1.0, 1.0, -1.0]},
                            analysis_run_id="AR1")
    assert out.result.status is ParameterStatus.REQUIRES_REVIEW


def test_downside_stress_applies_shock():
    out = _engine().compute("downside_ebitda", {"base": 1000.0, "shock_pct": -0.2},
                            analysis_run_id="AR1")
    assert out.result.value == pytest.approx(800.0)


def test_trend_direction_detects_decline():
    out = _engine().compute("margin_trend", {"series": [0.1, 0.08, 0.06, 0.04]},
                            analysis_run_id="AR1")
    assert out.result.status is ParameterStatus.OK
    assert out.result.value < 0  # declining


def test_deterministic_replay_is_identical():
    e = _engine()
    a = e.compute("segment_hhi", {"shares": [40, 30, 30]}, analysis_run_id="AR1")
    b = e.compute("segment_hhi", {"shares": [40, 30, 30]}, analysis_run_id="AR1")
    assert a.result.value == b.result.value
    assert a.result.formula_version == b.result.formula_version


def test_unregistered_parameter_is_proposed_new_calculation():
    out = _engine().compute("some_novel_metric", {"topic": "financial", "x": 1},
                            analysis_run_id="AR1")
    assert out.result.status is ParameterStatus.PROPOSED_NEW_CALCULATION
    assert out.result.value is None
    assert "requires implementation" in (out.result.notes or "")


def test_source_fact_ids_are_tracked():
    out = _engine().compute(
        "fcf_conversion", {"numerator": 8.0, "denominator": 10.0},
        analysis_run_id="AR1", source_fact_ids=["f_fcf", "f_ebitda"],
    )
    assert out.result.value == pytest.approx(0.8)
    assert out.result.source_fact_ids == ["f_fcf", "f_ebitda"]


def test_registry_rejects_unregistered_formula():
    with pytest.raises(ValueError, match="unregistered formula"):
        ParameterDefinition("p", Topic.FINANCIAL, "no_such_formula", "ratio")


def test_registry_rejects_conflicting_redefinition():
    reg = ParameterDefinitionRegistry()
    with pytest.raises(ValueError, match="different definition"):
        reg.register(
            ParameterDefinition("segment_hhi", Topic.FINANCIAL, "safe_ratio", "ratio")
        )


def test_formula_version_is_stable_and_specific():
    reg = ParameterDefinitionRegistry()
    d1 = reg.get("segment_hhi")
    d2 = reg.get("customer_hhi")
    assert d1.formula_version == reg.get("segment_hhi").formula_version
    assert d1.formula_version != d2.formula_version


# --- expanded parameter coverage (remediation point 3) ---------------------


def test_default_set_spans_all_three_topics_and_is_large():
    from app.services.parameters import (
        business_parameter_definitions,
        financial_parameter_definitions,
        structuring_parameter_definitions,
    )
    from app.services.parameters.registry import default_parameter_definitions

    defs = default_parameter_definitions()
    topics = {d.topic for d in defs}
    assert topics == {Topic.BUSINESS, Topic.FINANCIAL, Topic.STRUCTURING}
    # The per-topic modules compose the default set (design intent).
    assert len(defs) == (
        len(business_parameter_definitions())
        + len(financial_parameter_definitions())
        + len(structuring_parameter_definitions())
    )
    # Substantial coverage, not a token subset.
    assert len(defs) >= 40


@pytest.mark.parametrize(
    "parameter_id,inputs,expected",
    [
        ("seasonality_index", {"periods": [100, 100, 100, 400]}, 2.285714),
        ("management_turnover", {"departures": 2, "headcount": 10}, 0.2),
        ("management_tenure", {"series": [4, 6, 8]}, 6.0),
        ("acquisition_frequency", {"count": 6, "years": 3}, 2.0),
        ("goodwill_growth", {"begin": 100, "end": 150}, 0.5),
        ("dso", {"balance": 50, "flow": 365, "days": 365}, 50.0),
        ("cash_conversion_cycle", {"dso": 50, "dio": 30, "dpo": 40}, 40.0),
        ("debt_capacity", {"ebitda": 100, "max_leverage": 3.5, "net_debt": 200}, 150.0),
    ],
)
def test_new_business_and_financial_formulas(parameter_id, inputs, expected):
    out = _engine().compute(parameter_id, inputs, analysis_run_id="AR1")
    assert out.result.status is ParameterStatus.OK
    assert out.result.value == pytest.approx(expected, abs=1e-4)


def test_price_volume_decomposition():
    out = _engine().compute(
        "price_volume_split",
        {"price_begin": 10, "price_end": 12, "volume_begin": 100,
         "volume_end": 110},
        analysis_run_id="AR1",
    )
    # rev 1000 -> 1320; price effect (12-10)*110 = 220 of 320 total.
    assert out.result.value == pytest.approx(220 / 320, abs=1e-4)


def test_structuring_amortization_bullet_and_coverage():
    e = _engine()
    bullet = e.compute("amortization_bullet",
                       {"principal": 1000, "amortization": [100, 100, 100]},
                       analysis_run_id="AR1")
    assert bullet.result.value == pytest.approx(700.0)

    cov = e.compute("collateral_coverage", {"available": 1500, "required": 1000},
                    analysis_run_id="AR1")
    assert cov.result.value == pytest.approx(1.5)

    dscr = e.compute("candidate_dscr", {"available": 120, "required": 100},
                     analysis_run_id="AR1")
    assert dscr.result.value == pytest.approx(1.2)


def test_covenant_headroom_breach_is_negative():
    out = _engine().compute(
        "covenant_headroom", {"actual": 4.0, "limit": 3.5, "direction": "max"},
        analysis_run_id="AR1",
    )
    assert out.result.value < 0  # breach


def test_max_feasible_facility_never_negative():
    out = _engine().compute(
        "max_feasible_facility",
        {"ebitda": 100, "max_leverage": 2.0, "net_debt": 500},
        analysis_run_id="AR1",
    )
    assert out.result.value == 0.0  # already over-levered => no capacity


def test_sensitivity_estimates_slope():
    out = _engine().compute(
        "rate_sensitivity",
        {"base_output": 100, "shocked_output": 90, "shock_size": 0.01},
        analysis_run_id="AR1",
    )
    assert out.result.value == pytest.approx(-1000.0)


def test_metric_adapter_wraps_metric_result_as_parameter():
    from app.services.metrics.engine import MetricResult, MetricState
    from app.services.parameters import metric_to_parameter

    metric = MetricResult(
        metric_name="net_debt_to_ebitda",
        metric_definition_id="net_debt_to_ebitda",
        metric_definition_version=1,
        formula_id="net_debt_to_ebitda_v1",
        engine_version="metric-engine-1.1.0",
        inputs={"net_debt": 20000, "ebitda": 6500},
        input_fact_ids=["f_nd", "f_ebitda"],
        state=MetricState.OK,
        result=3.07,
        units="x",
    )
    pr = metric_to_parameter(metric, analysis_run_id="AR1")
    assert pr.method.value == "deterministic"
    assert pr.parameter_id == "net_debt_to_ebitda"
    assert pr.value == 3.07
    assert pr.formula_id == "net_debt_to_ebitda"
    assert pr.formula_version == "1"
    assert pr.source_fact_ids == ["f_nd", "f_ebitda"]
    assert pr.agent_run_id is None  # deterministic => no agent run


def test_metric_adapter_maps_explicit_state_to_unavailable():
    from app.services.metrics.engine import MetricResult, MetricState
    from app.services.parameters import metric_to_parameter

    metric = MetricResult(
        metric_name="interest_coverage",
        metric_definition_id="interest_coverage",
        metric_definition_version=1,
        formula_id="interest_coverage_v1",
        engine_version="metric-engine-1.1.0",
        inputs={},
        input_fact_ids=[],
        state=MetricState.MISSING_INPUT,
        result=None,
        detail="Input 'interest_expense' is missing.",
    )
    pr = metric_to_parameter(metric, analysis_run_id="AR1")
    assert pr.status is ParameterStatus.UNAVAILABLE
    assert pr.value is None
    assert pr.missing_information
