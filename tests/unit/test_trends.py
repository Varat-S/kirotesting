"""Historical trend layer tests (Req 9.1-9.5; task 5.3)."""

from __future__ import annotations

import pytest

from app.services.metrics.trends import Direction, TrendAnalyzer, TrendPoint


def _points(values: dict[int, float | None]) -> list[TrendPoint]:
    return [TrendPoint(fiscal_year=y, value=v) for y, v in values.items()]


def test_leverage_rising_is_deteriorating() -> None:
    """Worked example: ND/EBITDA 1.73 -> 2.17 -> 3.00 is deteriorating (up adverse)."""
    analyzer = TrendAnalyzer()
    result = analyzer.analyze(
        "net_debt_to_ebitda",
        _points({2021: 1.73, 2022: 2.17, 2023: 3.00}),
    )
    assert result.direction is Direction.DETERIORATING
    assert result.current_value == 3.00
    assert result.previous_value == 2.17
    # 2-year change from 1.73 to 3.00.
    assert result.change_2y == pytest.approx(3.00 - 1.73)
    assert result.pct_change_2y == pytest.approx((3.00 - 1.73) / 1.73)


def test_coverage_falling_is_deteriorating() -> None:
    analyzer = TrendAnalyzer()
    result = analyzer.analyze(
        "interest_coverage",
        _points({2021: 5.0, 2022: 4.0, 2023: 3.0}),
    )
    assert result.adverse_direction == "down"
    assert result.direction is Direction.DETERIORATING


def test_no_implied_continuity_across_missing_year() -> None:
    """Req 9.4: a missing intermediate year is surfaced; no interpolation."""
    analyzer = TrendAnalyzer()
    result = analyzer.analyze(
        "net_debt_to_ebitda",
        _points({2020: 1.5, 2023: 3.0}),  # 2021, 2022 absent
    )
    assert result.has_gaps is True
    assert result.missing_years == [2021, 2022]
    # No previous period (2022 absent) and no 1y/2y change across the gap.
    assert result.previous_value is None
    assert result.change_1y is None
    assert result.change_2y is None
    # 3-year change IS computable (2020 and 2023 both present, exactly 3 apart).
    assert result.change_3y == pytest.approx(1.5)


def test_structural_break_sign_flip_is_visible() -> None:
    analyzer = TrendAnalyzer()
    result = analyzer.analyze(
        "free_cash_flow",
        _points({2021: 100.0, 2022: -50.0, 2023: -60.0}),
    )
    types = {b["type"] for b in result.structural_breaks}
    assert "sign_flip" in types


def test_structural_break_gap_is_flagged() -> None:
    analyzer = TrendAnalyzer()
    result = analyzer.analyze(
        "net_debt_to_ebitda",
        _points({2020: 1.5, 2023: 3.0}),
    )
    types = {b["type"] for b in result.structural_breaks}
    assert "gap" in types


def test_missing_values_excluded_not_zeroed() -> None:
    analyzer = TrendAnalyzer()
    result = analyzer.analyze(
        "net_debt_to_ebitda",
        _points({2021: 2.0, 2022: None, 2023: 3.0}),
    )
    # 2022 has no value, so it is a gap, not a zero.
    assert 2022 in result.missing_years
    assert result.observed_years == [2021, 2023]


def test_deterministic_rerun() -> None:
    analyzer = TrendAnalyzer()
    pts = _points({2021: 1.73, 2022: 2.17, 2023: 3.00})
    assert (
        analyzer.analyze("net_debt_to_ebitda", pts).as_payload()
        == analyzer.analyze("net_debt_to_ebitda", pts).as_payload()
    )


def test_unknown_metric_has_no_silent_default() -> None:
    analyzer = TrendAnalyzer()
    with pytest.raises(ValueError):
        analyzer.analyze("mystery_metric", _points({2023: 1.0}))
