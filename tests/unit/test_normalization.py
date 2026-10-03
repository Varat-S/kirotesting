"""Unit tests for deterministic unit/scale and date/period normalization (3.6).

Covers Req 4.4, 4.5, 5.2, 23.1: deterministic conversions, raw-vs-normalized
preservation, and period derivation. Normalization NEVER fabricates a value for
unparseable input.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.extraction.normalization import (
    NormalizationError,
    compute_freshness,
    normalize_amount,
    normalize_period,
    normalize_scale,
    parse_raw_number,
)


class TestScale:
    def test_word_and_abbreviation_scales(self) -> None:
        assert normalize_scale("thousands").multiplier == 1_000.0
        assert normalize_scale("millions").multiplier == 1_000_000.0
        assert normalize_scale("mm").label == "millions"
        assert normalize_scale("bn").label == "billions"

    def test_none_defaults_to_units(self) -> None:
        info = normalize_scale(None)
        assert info.label == "units"
        assert info.multiplier == 1.0

    def test_numeric_multiplier(self) -> None:
        assert normalize_scale(1000).label == "thousands"

    def test_unknown_scale_raises(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_scale("gazillions")


class TestParseRawNumber:
    def test_thousands_separator_and_currency(self) -> None:
        assert parse_raw_number("$1,500") == 1500.0

    def test_accounting_parentheses_negative(self) -> None:
        assert parse_raw_number("(750)") == -750.0

    def test_plain_number(self) -> None:
        assert parse_raw_number(250) == 250.0

    def test_unparseable_raises_not_zero(self) -> None:
        # Critical: never coerce an unparseable value to 0 (Req 4.4).
        with pytest.raises(NormalizationError):
            parse_raw_number("n/a")

    def test_empty_raises(self) -> None:
        with pytest.raises(NormalizationError):
            parse_raw_number("")


class TestAmount:
    def test_thousands_to_millions(self) -> None:
        amount = normalize_amount("1,500", scale="thousands", currency="USD")
        assert amount.value == pytest.approx(1.5)
        assert amount.unit == "USD_million"

    def test_millions_to_millions_identity(self) -> None:
        amount = normalize_amount("1500", scale="millions", currency="usd")
        assert amount.value == pytest.approx(1500.0)
        assert amount.unit == "USD_million"

    def test_negative_preserved(self) -> None:
        amount = normalize_amount("(750)", scale="millions", currency="USD")
        assert amount.value == pytest.approx(-750.0)

    def test_no_currency_label(self) -> None:
        amount = normalize_amount("5", scale="billions", target_scale="millions")
        assert amount.unit == "million"
        assert amount.value == pytest.approx(5000.0)


class TestPeriod:
    def test_instant(self) -> None:
        p = normalize_period(instant="2023-12-31")
        assert p.period_type == "instant"
        assert p.period_start == date(2023, 12, 31)
        assert p.period_end == date(2023, 12, 31)
        assert p.fiscal_year == 2023

    def test_duration(self) -> None:
        p = normalize_period(period_start="2023-01-01", period_end="2023-12-31")
        assert p.period_type == "duration"
        assert p.fiscal_year == 2023

    def test_fiscal_year_calendar(self) -> None:
        p = normalize_period(fiscal_year=2023)
        assert p.period_type == "duration"
        assert p.period_start == date(2023, 1, 1)
        assert p.period_end == date(2023, 12, 31)

    def test_fiscal_year_non_calendar(self) -> None:
        # FY ending in June.
        p = normalize_period(fiscal_year=2023, fiscal_year_end_month=6)
        assert p.period_end == date(2023, 6, 30)
        assert p.period_start == date(2022, 7, 1)

    def test_instant_and_duration_conflict(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_period(instant="2023-12-31", period_end="2023-12-31")

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_period(period_start="2023-12-31", period_end="2023-01-01")

    def test_no_info_raises(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_period()

    def test_bad_date_format_raises(self) -> None:
        with pytest.raises(NormalizationError):
            normalize_period(instant="12/31/2023")


class TestFreshness:
    def test_date_becomes_utc_datetime(self) -> None:
        fresh = compute_freshness(date(2023, 12, 31))
        assert fresh == datetime(2023, 12, 31, tzinfo=timezone.utc)

    def test_determinism(self) -> None:
        a = normalize_amount("1,234.5", scale="thousands", currency="EUR")
        b = normalize_amount("1,234.5", scale="thousands", currency="EUR")
        assert a == b
