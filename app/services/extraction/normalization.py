"""Deterministic unit/scale and date/period normalization (task 3.6).

This module provides pure, deterministic conversions used by the parsers to
populate the normalized fields of a :class:`~app.schemas.evidence.CanonicalFact`
while PRESERVING the raw value/unit separately (Req 4.4, 4.5, 5.2, 23.1):

* :func:`normalize_scale` -- map a scale word/multiplier (thousands, millions,
  billions, units) to a numeric multiplier and a canonical scale label.
* :func:`normalize_amount` -- turn a raw string/number + scale + currency into a
  canonical numeric amount expressed in a normalized unit (e.g. ``USD_million``).
* :func:`normalize_period` -- derive ``period_type`` (``instant``/``duration``),
  ``period_start``/``period_end`` and ``fiscal_year`` deterministically from
  explicit dates or a fiscal-year descriptor.
* :func:`compute_freshness` -- preserve reporting freshness (how old the data is
  relative to a reference instant).

Everything here is deterministic: same input -> same output, no I/O, no LLM.
Raw inputs are never discarded; callers keep ``raw_value``/``raw_unit`` and set
``normalized_value``/``normalized_unit`` from these results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

# ---------------------------------------------------------------------------
# Scale normalization
# ---------------------------------------------------------------------------

# Canonical multipliers keyed by a normalized scale label.
SCALE_MULTIPLIERS: dict[str, float] = {
    "units": 1.0,
    "ones": 1.0,
    "tens": 10.0,
    "hundreds": 100.0,
    "thousands": 1_000.0,
    "millions": 1_000_000.0,
    "billions": 1_000_000_000.0,
    "trillions": 1_000_000_000_000.0,
}

# Aliases/synonyms (including common abbreviations) -> canonical scale label.
_SCALE_ALIASES: dict[str, str] = {
    "": "units",
    "unit": "units",
    "units": "units",
    "one": "units",
    "ones": "units",
    "actual": "units",
    "actuals": "units",
    "ten": "tens",
    "tens": "tens",
    "hundred": "hundreds",
    "hundreds": "hundreds",
    "thousand": "thousands",
    "thousands": "thousands",
    "k": "thousands",
    "000": "thousands",
    "000s": "thousands",
    "in thousands": "thousands",
    "million": "millions",
    "millions": "millions",
    "mm": "millions",
    "m": "millions",
    "mn": "millions",
    "in millions": "millions",
    "billion": "billions",
    "billions": "billions",
    "bn": "billions",
    "b": "billions",
    "in billions": "billions",
    "trillion": "trillions",
    "trillions": "trillions",
    "tn": "trillions",
}


class NormalizationError(ValueError):
    """Raised when a value cannot be deterministically normalized."""


@dataclass(frozen=True)
class ScaleInfo:
    """Resolved scale: canonical label and numeric multiplier."""

    label: str
    multiplier: float


def normalize_scale(scale: str | int | float | None) -> ScaleInfo:
    """Resolve a scale descriptor to a canonical label and multiplier.

    Accepts a word (``"thousands"``), an abbreviation (``"mm"``), or a numeric
    multiplier (``1000``). Returns :class:`ScaleInfo`. ``None``/empty resolves to
    ``units`` (multiplier 1.0) -- an explicit, documented default, never silent
    fabrication of a value. Unknown descriptors raise :class:`NormalizationError`.
    """
    if scale is None:
        return ScaleInfo("units", 1.0)
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        multiplier = float(scale)
        for label, mult in SCALE_MULTIPLIERS.items():
            if mult == multiplier and label not in ("ones",):
                return ScaleInfo(label, mult)
        if multiplier <= 0:
            raise NormalizationError(f"Scale multiplier must be positive: {scale!r}")
        return ScaleInfo(f"x{multiplier:g}", multiplier)

    key = str(scale).strip().lower()
    if key in _SCALE_ALIASES:
        label = _SCALE_ALIASES[key]
        return ScaleInfo(label, SCALE_MULTIPLIERS[label])
    raise NormalizationError(f"Unknown scale descriptor: {scale!r}")


# ---------------------------------------------------------------------------
# Amount normalization
# ---------------------------------------------------------------------------

# Parentheses denote negative in accounting notation, e.g. "(1,234)" == -1234.
_NUMERIC_CLEAN = re.compile(r"[,\s$€£¥]")


def parse_raw_number(raw: str | int | float) -> float:
    """Parse a raw financial number string to a float, deterministically.

    Handles thousands separators, currency symbols, and accounting parentheses
    for negatives. Raises :class:`NormalizationError` for non-numeric input --
    it NEVER substitutes a default such as 0 for an unparseable value.
    """
    if isinstance(raw, bool):  # guard: bool is an int subclass
        raise NormalizationError(f"Boolean is not a numeric value: {raw!r}")
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    if not text:
        raise NormalizationError("Cannot parse an empty value as a number.")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = _NUMERIC_CLEAN.sub("", text)
    if text.startswith("-"):
        negative = True
        text = text[1:]
    try:
        value = float(text)
    except ValueError as exc:  # pragma: no cover - message asserted in tests
        raise NormalizationError(f"Cannot parse {raw!r} as a number.") from exc
    return -value if negative else value


@dataclass(frozen=True)
class NormalizedAmount:
    """A canonically normalized amount with its normalized unit label."""

    value: float
    unit: str
    scale_label: str
    multiplier: float
    method: str


# Target scale labels and their multipliers for canonical output units.
_TARGET_SCALE_LABEL: dict[str, str] = {
    "millions": "million",
    "thousands": "thousand",
    "billions": "billion",
    "units": "unit",
}


def normalize_amount(
    raw: str | int | float,
    *,
    scale: str | int | float | None = None,
    currency: str | None = None,
    target_scale: str = "millions",
) -> NormalizedAmount:
    """Normalize a raw amount to a canonical unit (e.g. ``USD_million``).

    The raw number is parsed, multiplied by its source ``scale`` to obtain the
    absolute amount, then re-expressed in ``target_scale``. The returned
    :class:`NormalizedAmount` carries the normalized numeric value plus a unit
    label combining ``currency`` and the target scale, e.g. ``USD_million``.

    The caller keeps the raw value/unit separately; this function only computes
    the normalized side (Req 4.4). It never fabricates a value: unparseable
    input raises :class:`NormalizationError`.
    """
    source = normalize_scale(scale)
    target = normalize_scale(target_scale)
    absolute = parse_raw_number(raw) * source.multiplier
    normalized_value = absolute / target.multiplier

    scale_word = _TARGET_SCALE_LABEL.get(target.label, target.label)
    if currency:
        unit = f"{currency.strip().upper()}_{scale_word}"
    else:
        unit = scale_word
    return NormalizedAmount(
        value=normalized_value,
        unit=unit,
        scale_label=target.label,
        multiplier=target.multiplier,
        method=f"scale:{source.label}->{target.label}",
    )


# ---------------------------------------------------------------------------
# Period / date normalization
# ---------------------------------------------------------------------------

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FY_DESCRIPTOR = re.compile(r"^(?:FY)?\s*(\d{4})$", re.IGNORECASE)


def _coerce_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if _ISO_DATE.match(text):
        return date.fromisoformat(text)
    raise NormalizationError(f"Unrecognized date format: {value!r} (expected ISO 8601)")


@dataclass(frozen=True)
class NormalizedPeriod:
    """Deterministically derived reporting period."""

    period_type: str  # "instant" | "duration"
    period_start: date | None
    period_end: date | None
    fiscal_year: int | None


def normalize_period(
    *,
    period_start: str | date | datetime | None = None,
    period_end: str | date | datetime | None = None,
    instant: str | date | datetime | None = None,
    fiscal_year: int | str | None = None,
    fiscal_year_end_month: int = 12,
) -> NormalizedPeriod:
    """Derive a normalized reporting period, deterministically.

    * An ``instant`` (balance-sheet point in time) yields ``period_type`` =
      ``instant`` with equal start/end.
    * A ``period_start``+``period_end`` pair yields ``period_type`` =
      ``duration``.
    * A bare ``fiscal_year`` with ``fiscal_year_end_month`` derives a duration
      spanning that fiscal year (defaulting to a calendar year end).

    The fiscal year is inferred from the period end when not supplied. Raises
    :class:`NormalizationError` for an inconsistent/unspecified period rather
    than guessing.
    """
    inst = _coerce_date(instant)
    start = _coerce_date(period_start)
    end = _coerce_date(period_end)

    fy: int | None
    if fiscal_year is None:
        fy = None
    else:
        m = _FY_DESCRIPTOR.match(str(fiscal_year).strip())
        if not m:
            raise NormalizationError(f"Unrecognized fiscal year: {fiscal_year!r}")
        fy = int(m.group(1))

    if inst is not None:
        if start is not None or end is not None:
            raise NormalizationError(
                "Provide either an instant or a start/end duration, not both."
            )
        resolved_fy = fy if fy is not None else inst.year
        return NormalizedPeriod("instant", inst, inst, resolved_fy)

    if start is not None and end is not None:
        if end < start:
            raise NormalizationError(
                f"period_end {end} precedes period_start {start}."
            )
        resolved_fy = fy if fy is not None else end.year
        return NormalizedPeriod("duration", start, end, resolved_fy)

    if start is not None or end is not None:
        raise NormalizationError(
            "A duration requires both period_start and period_end."
        )

    if fy is not None:
        if not 1 <= fiscal_year_end_month <= 12:
            raise NormalizationError(
                f"fiscal_year_end_month out of range: {fiscal_year_end_month!r}"
            )
        # End on the last day of the fiscal-year-end month.
        if fiscal_year_end_month == 12:
            derived_end = date(fy, 12, 31)
            derived_start = date(fy, 1, 1)
        else:
            derived_end = _last_day_of_month(fy, fiscal_year_end_month)
            start_month = fiscal_year_end_month + 1
            derived_start = date(fy - 1, start_month, 1)
        return NormalizedPeriod("duration", derived_start, derived_end, fy)

    raise NormalizationError(
        "No period information supplied; cannot normalize a period."
    )


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    first_of_next = date(year, month + 1, 1)
    return date.fromordinal(first_of_next.toordinal() - 1)


def compute_freshness(
    reporting_instant: date | datetime,
    *,
    reference: datetime | None = None,
) -> datetime:
    """Return the reporting instant as a tz-aware datetime for freshness tracking.

    Preserves the reporting period's point-in-time so downstream staleness logic
    (Milestone 4+) can compare it against a case cutoff. ``reference`` is accepted
    for API symmetry; freshness is the reporting instant itself.
    """
    if isinstance(reporting_instant, datetime):
        dt = reporting_instant
    else:
        dt = datetime(reporting_instant.year, reporting_instant.month, reporting_instant.day)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
