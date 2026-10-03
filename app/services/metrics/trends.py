"""Historical trend layer (Req 9.1-9.5; task 5.3).

Deterministic per-metric trend analysis. For a sequence of metric values keyed
by fiscal year the layer computes current value, previous period, 2-year and
3-year change, slope/direction, and volatility (Req 9.1). Direction is judged
against a CONFIGURED, metric-specific adverse-direction definition (Req 9.2):
for leverage, UP is adverse; for coverage, DOWN is adverse.

Honesty guarantees:

* **No implied continuity across missing years (Req 9.4).** A change is only
  computed between two years that are BOTH present and exactly N apart. If an
  intermediate year is absent, the gap is surfaced (``has_gaps``) and the layer
  never interpolates.
* **Structural breaks are visible (Req 9.5).** A year where a value flips sign,
  or where the period-over-period change exceeds a configured break threshold,
  is reported in ``structural_breaks``.
* **Deterministic (Req 9.3).** Same inputs + same adverse-direction config
  yield identical trend output.

Adverse-direction rules are *configuration*: they live in the ``trend_rules``
config artifact kind and are passed in resolved form; nothing is hardcoded
(Req 21.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from statistics import pstdev
from typing import Any


class Direction(str, Enum):
    """Direction of movement over the observed window."""

    IMPROVING = "improving"
    DETERIORATING = "deteriorating"
    FLAT = "flat"
    INDETERMINATE = "indeterminate"


# Illustrative, versioned adverse-direction config (NOT bank policy). ``up``
# means an increasing value is adverse; ``down`` means a decreasing value is
# adverse. This is the DATA for the ``trend_rules`` config kind.
DEFAULT_ADVERSE_DIRECTIONS: dict[str, str] = {
    "net_debt_to_ebitda": "up",
    "net_debt": "up",
    "capex_to_revenue": "up",
    "casm": "up",
    "interest_coverage": "down",
    "operating_margin": "down",
    "cash_conversion": "down",
    "revenue_growth": "down",
    "free_cash_flow": "down",
    "liquidity": "down",
    "load_factor": "down",
}

# Default structural-break threshold: a period-over-period relative change whose
# magnitude exceeds this is flagged as a structural break (configurable).
DEFAULT_BREAK_THRESHOLD = 0.5


@dataclass(frozen=True)
class TrendPoint:
    """A single (fiscal_year, value) observation feeding the trend layer."""

    fiscal_year: int
    value: float | None


@dataclass
class TrendResult:
    """Deterministic trend summary for one metric (design-aligned)."""

    metric_name: str
    adverse_direction: str
    current_fiscal_year: int | None = None
    current_value: float | None = None
    previous_value: float | None = None
    change_1y: float | None = None
    change_2y: float | None = None
    change_3y: float | None = None
    pct_change_1y: float | None = None
    pct_change_2y: float | None = None
    pct_change_3y: float | None = None
    slope: float | None = None
    direction: Direction = Direction.INDETERMINATE
    volatility: float | None = None
    has_gaps: bool = False
    missing_years: list[int] = field(default_factory=list)
    structural_breaks: list[dict] = field(default_factory=list)
    observed_years: list[int] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "adverse_direction": self.adverse_direction,
            "current_fiscal_year": self.current_fiscal_year,
            "current_value": self.current_value,
            "previous_value": self.previous_value,
            "change_1y": self.change_1y,
            "change_2y": self.change_2y,
            "change_3y": self.change_3y,
            "pct_change_1y": self.pct_change_1y,
            "pct_change_2y": self.pct_change_2y,
            "pct_change_3y": self.pct_change_3y,
            "slope": self.slope,
            "direction": self.direction.value,
            "volatility": self.volatility,
            "has_gaps": self.has_gaps,
            "missing_years": self.missing_years,
            "structural_breaks": self.structural_breaks,
            "observed_years": self.observed_years,
        }


class TrendAnalyzer:
    """Compute deterministic per-metric trends from yearly observations."""

    def __init__(
        self,
        adverse_directions: dict[str, str] | None = None,
        *,
        break_threshold: float = DEFAULT_BREAK_THRESHOLD,
    ) -> None:
        self._adverse = (
            dict(adverse_directions)
            if adverse_directions is not None
            else dict(DEFAULT_ADVERSE_DIRECTIONS)
        )
        self._break_threshold = float(break_threshold)

    def adverse_direction(self, metric_name: str) -> str:
        adverse = self._adverse.get(metric_name)
        if adverse is None:
            raise ValueError(
                f"No adverse-direction configured for metric {metric_name!r} "
                "(no silent default, Req 21.1)."
            )
        return adverse

    def analyze(
        self, metric_name: str, points: list[TrendPoint]
    ) -> TrendResult:
        """Analyze a metric's history. ``points`` need not be sorted or complete.

        Only years with a numeric value participate. Changes are computed only
        between years that are BOTH present and exactly N apart (no implied
        continuity, Req 9.4). Gaps and structural breaks are surfaced.
        """
        adverse = self.adverse_direction(metric_name)
        result = TrendResult(metric_name=metric_name, adverse_direction=adverse)

        # Keep only observations that carry a numeric value, newest last.
        valued = sorted(
            [p for p in points if p.value is not None], key=lambda p: p.fiscal_year
        )
        if not valued:
            return result

        by_year = {p.fiscal_year: float(p.value) for p in valued}  # type: ignore[arg-type]
        years = [p.fiscal_year for p in valued]
        result.observed_years = years

        # Detect gaps across the observed span (Req 9.4).
        span = range(years[0], years[-1] + 1)
        missing = [y for y in span if y not in by_year]
        result.missing_years = missing
        result.has_gaps = bool(missing)

        current_year = years[-1]
        result.current_fiscal_year = current_year
        result.current_value = by_year[current_year]

        # Previous period only if the immediately preceding year is present.
        if (current_year - 1) in by_year:
            result.previous_value = by_year[current_year - 1]

        # N-year changes only when both endpoints are present and N apart.
        result.change_1y, result.pct_change_1y = self._change(by_year, current_year, 1)
        result.change_2y, result.pct_change_2y = self._change(by_year, current_year, 2)
        result.change_3y, result.pct_change_3y = self._change(by_year, current_year, 3)

        # Direction + slope from a simple least-squares fit over observed points.
        result.slope = _slope(years, [by_year[y] for y in years])
        result.direction = self._direction(result.slope, adverse)

        # Volatility: population stddev of observed values (0 for a single point).
        values = [by_year[y] for y in years]
        result.volatility = pstdev(values) if len(values) >= 2 else 0.0

        # Structural breaks: sign flips or large period-over-period swings
        # between CONSECUTIVE present years only (never across a gap).
        result.structural_breaks = self._structural_breaks(by_year, years)
        return result

    def _change(
        self, by_year: dict[int, float], current_year: int, n: int
    ) -> tuple[float | None, float | None]:
        base_year = current_year - n
        if base_year not in by_year:
            return (None, None)
        base = by_year[base_year]
        cur = by_year[current_year]
        abs_change = cur - base
        pct = (abs_change / abs(base)) if abs(base) > 0 else None
        return (abs_change, pct)

    def _direction(self, slope: float | None, adverse: str) -> Direction:
        if slope is None:
            return Direction.INDETERMINATE
        if slope == 0:
            return Direction.FLAT
        rising = slope > 0
        if adverse == "up":
            return Direction.DETERIORATING if rising else Direction.IMPROVING
        if adverse == "down":
            return Direction.IMPROVING if rising else Direction.DETERIORATING
        return Direction.INDETERMINATE

    def _structural_breaks(
        self, by_year: dict[int, float], years: list[int]
    ) -> list[dict]:
        breaks: list[dict] = []
        for prev_y, cur_y in zip(years, years[1:]):
            # Only consecutive calendar years are a continuous segment; a jump
            # across a gap is itself a discontinuity, flagged here.
            if cur_y - prev_y != 1:
                breaks.append(
                    {
                        "type": "gap",
                        "from_year": prev_y,
                        "to_year": cur_y,
                        "detail": "non-contiguous years; continuity not implied",
                    }
                )
                continue
            prev_v, cur_v = by_year[prev_y], by_year[cur_y]
            if (prev_v < 0 < cur_v) or (prev_v > 0 > cur_v):
                breaks.append(
                    {
                        "type": "sign_flip",
                        "from_year": prev_y,
                        "to_year": cur_y,
                        "from_value": prev_v,
                        "to_value": cur_v,
                    }
                )
                continue
            if abs(prev_v) > 0:
                rel = abs(cur_v - prev_v) / abs(prev_v)
                if rel > self._break_threshold:
                    breaks.append(
                        {
                            "type": "large_change",
                            "from_year": prev_y,
                            "to_year": cur_y,
                            "relative_change": rel,
                        }
                    )
        return breaks


def _slope(xs: list[int], ys: list[float]) -> float | None:
    """Least-squares slope of ys over xs; None for fewer than two points."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return num / denom
