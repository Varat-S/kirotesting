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
