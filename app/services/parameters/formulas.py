"""Deterministic parameter formula library (Milestone 7).

Pure functions over resolved numeric inputs. Each returns a ``FormulaOutput``:
``(value, state, detail)`` where ``state`` reuses the metric engine's explicit
non-numeric states (``ok`` / ``not_meaningful`` / ``missing_input`` /
``requires_review``) so zero/near-zero/missing never produce a misleading number
(Req 7.6 / baseline Req 8.4). Formulas never coerce missing inputs to zero.

Each formula is registered with a ``formula_id`` + ``formula_version`` so a
result is reproducible and traceable to its exact inputs.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from app.services.metrics.engine import MetricState

FormulaOutput = tuple[float | None, MetricState, str | None]
# A parameter formula takes a dict of resolved numeric inputs and a near-zero
# floor, and returns a FormulaOutput.
Formula = Callable[[dict[str, Any], float], FormulaOutput]

PARAMETER_ENGINE_VERSION = "parameter-engine-1.0.0"


def _require(values: dict[str, Any], *names: str) -> FormulaOutput | None:
    for name in names:
        if values.get(name) is None:
            return (None, MetricState.MISSING_INPUT, f"Input {name!r} is missing.")
    return None


# --- concentration -----------------------------------------------------------


def hhi(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Herfindahl-Hirschman Index over a list of shares/weights.

    Input ``shares`` is a list of non-negative weights (any scale). Returns the
    normalized HHI in [0, 1] (1 = fully concentrated). An empty/zero-sum input
    is ``not_meaningful`` rather than a divide-by-zero.
    """
    shares = values.get("shares")
    if not shares:
        return (None, MetricState.MISSING_INPUT, "No shares provided.")
    total = float(sum(s for s in shares if s is not None))
    if total <= 0:
        return (None, MetricState.NOT_MEANINGFUL, "Share total is non-positive.")
    index = sum((float(s) / total) ** 2 for s in shares if s is not None)
    return (round(index, 6), MetricState.OK, None)


def top_n_concentration(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Share of the top-N entries over the total (e.g. top-5 customer share).

    Inputs: ``shares`` (list), ``n`` (int). Returns a fraction in [0, 1].
    """
    guard = _require(values, "shares")
    if guard:
        return guard
    shares = [float(s) for s in values["shares"] if s is not None]
    if not shares:
        return (None, MetricState.MISSING_INPUT, "No shares provided.")
    total = sum(shares)
    if total <= 0:
        return (None, MetricState.NOT_MEANINGFUL, "Share total is non-positive.")
    n = int(values.get("n", 5))
    top = sum(sorted(shares, reverse=True)[:n])
    return (round(top / total, 6), MetricState.OK, None)


# --- trend -------------------------------------------------------------------


def cagr(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Compound annual growth rate from ``begin``, ``end`` and ``periods``.

    A non-positive ``begin`` makes CAGR economically meaningless
    (``not_meaningful``); zero/negative periods -> ``requires_review``.
    """
    guard = _require(values, "begin", "end", "periods")
    if guard:
        return guard
    begin, end, periods = float(values["begin"]), float(values["end"]), float(values["periods"])
    if begin <= 0:
        return (None, MetricState.NOT_MEANINGFUL, "Beginning value is non-positive.")
    if periods <= 0:
        return (None, MetricState.REQUIRES_REVIEW, "Periods must be positive.")
    if end <= 0:
        return (None, MetricState.NOT_MEANINGFUL, "Ending value is non-positive.")
    return (round((end / begin) ** (1.0 / periods) - 1.0, 6), MetricState.OK, None)


def trend_direction(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Signed slope of a numeric series via least-squares; 0 if flat.

    Input ``series`` is a list of numbers (chronological). Needs >=2 points,
    otherwise ``missing_input``. Returns the slope per step.
    """
    series = values.get("series")
    if not series or len([s for s in series if s is not None]) < 2:
        return (None, MetricState.MISSING_INPUT, "Need >=2 data points.")
    ys = [float(s) for s in series if s is not None]
    n = len(ys)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return (0.0, MetricState.OK, "Flat series.")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    return (round(slope, 6), MetricState.OK, None)


def volatility(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Coefficient of variation (stdev/|mean|) of a series.

    Needs >=2 points. A near-zero mean makes CoV unstable -> ``requires_review``.
    """
    series = values.get("series")
    if not series or len([s for s in series if s is not None]) < 2:
        return (None, MetricState.MISSING_INPUT, "Need >=2 data points.")
    ys = [float(s) for s in series if s is not None]
    n = len(ys)
    mean = sum(ys) / n
    var = sum((y - mean) ** 2 for y in ys) / (n - 1)
    stdev = math.sqrt(var)
    if abs(mean) <= floor:
        return (None, MetricState.REQUIRES_REVIEW, "Mean is near zero; CoV unstable.")
    return (round(stdev / abs(mean), 6), MetricState.OK, None)


# --- ratios / stress ---------------------------------------------------------


def safe_ratio(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Zero-safe ratio of ``numerator`` / ``denominator`` (Req 7.6)."""
    guard = _require(values, "numerator", "denominator")
    if guard:
        return guard
    numerator = float(values["numerator"])
    denominator = float(values["denominator"])
    if abs(denominator) <= floor:
        return (None, MetricState.REQUIRES_REVIEW, "Denominator near zero.")
    return (round(numerator / denominator, 6), MetricState.OK, None)


def downside_stress(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Apply a multiplicative downside shock to a base value.

    Inputs: ``base`` and ``shock_pct`` (e.g. -0.2 for a 20% decline). Returns
    the stressed value. Deterministic — the LLM only proposes WHICH sensitivity
    to stress; the arithmetic is here (Req 12.2 / 3.1).
    """
    guard = _require(values, "base", "shock_pct")
    if guard:
        return guard
    return (round(float(values["base"]) * (1.0 + float(values["shock_pct"])), 6),
            MetricState.OK, None)


# The registered library, keyed by formula_id. Each entry's identity (its code
# contract) is versioned via the registry's content hash of this mapping's keys.
FORMULA_LIBRARY: dict[str, Formula] = {
    "hhi": hhi,
    "top_n_concentration": top_n_concentration,
    "cagr": cagr,
    "trend_direction": trend_direction,
    "volatility": volatility,
    "safe_ratio": safe_ratio,
    "downside_stress": downside_stress,
}
