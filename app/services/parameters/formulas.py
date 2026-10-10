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


def positive_denominator_ratio(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Ratio that is only meaningful over a POSITIVE denominator.

    Used for leverage, coverage and margin ratios where a negative denominator
    (negative EBITDA, negative interest, negative revenue) would produce a
    number with the wrong economic sign. A near-zero denominator is unstable
    (``requires_review``); a negative one is ``not_meaningful``. Neither is ever
    reported as a value, and a missing input is never treated as zero.
    """
    guard = _require(values, "numerator", "denominator")
    if guard:
        return guard
    numerator = float(values["numerator"])
    denominator = float(values["denominator"])
    if abs(denominator) <= floor:
        return (None, MetricState.REQUIRES_REVIEW, "Denominator near zero.")
    if denominator < 0:
        return (None, MetricState.NOT_MEANINGFUL,
                "Negative denominator; the ratio is not economically meaningful.")
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


def growth_rate(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Period-over-period growth: ``end``/``begin`` - 1 (zero-safe)."""
    guard = _require(values, "begin", "end")
    if guard:
        return guard
    begin, end = float(values["begin"]), float(values["end"])
    if abs(begin) <= floor:
        return (None, MetricState.NOT_MEANINGFUL, "Base value near zero.")
    return (round(end / begin - 1.0, 6), MetricState.OK, None)


def seasonality(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Seasonality index = peak-period value / mean period value.

    Input ``periods`` is a list of per-period values within a cycle (e.g. 4
    quarters). >1 means pronounced seasonality; near 1 means flat.
    """
    periods = values.get("periods")
    if not periods or len([p for p in periods if p is not None]) < 2:
        return (None, MetricState.MISSING_INPUT, "Need >=2 periods.")
    vals = [float(p) for p in periods if p is not None]
    mean = sum(vals) / len(vals)
    if abs(mean) <= floor:
        return (None, MetricState.NOT_MEANINGFUL, "Mean period value near zero.")
    return (round(max(vals) / mean, 6), MetricState.OK, None)


def turnover_rate(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Management turnover = ``departures`` / ``headcount`` over a window."""
    guard = _require(values, "departures", "headcount")
    if guard:
        return guard
    headcount = float(values["headcount"])
    if headcount <= 0:
        return (None, MetricState.NOT_MEANINGFUL, "Headcount non-positive.")
    return (round(float(values["departures"]) / headcount, 6), MetricState.OK, None)


def average(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Mean of a numeric series (e.g. average management tenure in years)."""
    series = values.get("series")
    if not series or all(s is None for s in series):
        return (None, MetricState.MISSING_INPUT, "No values provided.")
    vals = [float(s) for s in series if s is not None]
    return (round(sum(vals) / len(vals), 6), MetricState.OK, None)


def count_rate(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Frequency = ``count`` / ``years`` (e.g. acquisitions per year)."""
    guard = _require(values, "count", "years")
    if guard:
        return guard
    years = float(values["years"])
    if years <= 0:
        return (None, MetricState.REQUIRES_REVIEW, "Years must be positive.")
    return (round(float(values["count"]) / years, 6), MetricState.OK, None)


def days_outstanding(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Days metric = ``balance`` / ``flow`` * ``days`` (DSO/DIO/DPO).

    E.g. DSO = receivables / revenue * 365. Zero-safe on the flow.
    """
    guard = _require(values, "balance", "flow")
    if guard:
        return guard
    flow = float(values["flow"])
    if abs(flow) <= floor:
        return (None, MetricState.NOT_MEANINGFUL, "Flow near zero.")
    days = float(values.get("days", 365))
    return (round(float(values["balance"]) / flow * days, 6), MetricState.OK, None)


def cash_conversion_cycle(values: dict[str, Any], floor: float) -> FormulaOutput:
    """CCC = DSO + DIO - DPO."""
    guard = _require(values, "dso", "dio", "dpo")
    if guard:
        return guard
    return (round(float(values["dso"]) + float(values["dio"]) - float(values["dpo"]), 6),
            MetricState.OK, None)


def price_volume_decomposition(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Price contribution to revenue change (price-volume split).

    Inputs: ``price_begin``, ``price_end``, ``volume_begin``, ``volume_end``.
    Returns the fraction of total revenue change attributable to price. When
    total change is ~0 the split is not meaningful.
    """
    guard = _require(values, "price_begin", "price_end", "volume_begin", "volume_end")
    if guard:
        return guard
    pb, pe = float(values["price_begin"]), float(values["price_end"])
    vb, ve = float(values["volume_begin"]), float(values["volume_end"])
    rev_begin, rev_end = pb * vb, pe * ve
    total_change = rev_end - rev_begin
    if abs(total_change) <= floor:
        return (None, MetricState.NOT_MEANINGFUL, "Total revenue change near zero.")
    # Price effect at ending volume (one standard decomposition convention).
    price_effect = (pe - pb) * ve
    return (round(price_effect / total_change, 6), MetricState.OK, None)


def amortization_bullet(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Bullet (balloon) amount = principal - sum(scheduled amortization).

    Inputs: ``principal`` and ``amortization`` (list of scheduled repayments).
    """
    guard = _require(values, "principal")
    if guard:
        return guard
    principal = float(values["principal"])
    amort = values.get("amortization") or []
    scheduled = sum(float(a) for a in amort if a is not None)
    bullet = principal - scheduled
    return (round(max(bullet, 0.0), 6), MetricState.OK, None)


def coverage_ratio(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Coverage = ``available`` / ``required`` (collateral coverage, DSCR, etc.).

    E.g. collateral coverage = collateral value / exposure; DSCR = CFADS /
    debt service. Zero-safe on the required amount.
    """
    guard = _require(values, "available", "required")
    if guard:
        return guard
    required = float(values["required"])
    if abs(required) <= floor:
        return (None, MetricState.NOT_MEANINGFUL, "Required amount near zero.")
    return (round(float(values["available"]) / required, 6), MetricState.OK, None)


def covenant_headroom(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Covenant headroom as a signed fraction of the limit.

    Inputs: ``actual``, ``limit`` and ``direction`` ('max' or 'min'). For a
    'max' covenant (e.g. max net leverage 3.5x) headroom = (limit-actual)/limit;
    negative means a breach. For a 'min' covenant, the sign flips.
    """
    guard = _require(values, "actual", "limit")
    if guard:
        return guard
    actual, limit = float(values["actual"]), float(values["limit"])
    if abs(limit) <= floor:
        return (None, MetricState.NOT_MEANINGFUL, "Covenant limit near zero.")
    direction = values.get("direction", "max")
    headroom = (limit - actual) / limit if direction == "max" else (actual - limit) / limit
    return (round(headroom, 6), MetricState.OK, None)


def max_feasible_facility(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Maximum facility size that keeps leverage within a policy limit.

    Inputs: ``ebitda``, ``max_leverage`` and existing ``net_debt``. Returns the
    additional debt capacity (never negative).
    """
    guard = _require(values, "ebitda", "max_leverage")
    if guard:
        return guard
    ebitda = float(values["ebitda"])
    if ebitda <= 0:
        return (None, MetricState.NOT_MEANINGFUL, "EBITDA non-positive.")
    net_debt = float(values.get("net_debt", 0.0))
    capacity = ebitda * float(values["max_leverage"]) - net_debt
    return (round(max(capacity, 0.0), 6), MetricState.OK, None)


def sensitivity(values: dict[str, Any], floor: float) -> FormulaOutput:
    """Linear sensitivity: change in output per unit shock on an input.

    Inputs: ``base_output``, ``shocked_output``, ``shock_size``. Returns the
    derivative estimate (delta output / shock). Used for rate/FX/commodity
    sensitivities where structured inputs exist (Req 7.3).
    """
    guard = _require(values, "base_output", "shocked_output", "shock_size")
    if guard:
        return guard
    shock = float(values["shock_size"])
    # A shock is a (often fractional) perturbation, not a balance-sheet
    # magnitude, so it uses a tiny fixed epsilon rather than the financial floor.
    if abs(shock) <= 1e-12:
        return (None, MetricState.REQUIRES_REVIEW, "Shock size near zero.")
    delta = float(values["shocked_output"]) - float(values["base_output"])
    return (round(delta / shock, 6), MetricState.OK, None)


# The registered library, keyed by formula_id. Each entry's identity (its code
# contract) is versioned via the registry's content hash of this mapping's keys.
FORMULA_LIBRARY: dict[str, Formula] = {
    "hhi": hhi,
    "top_n_concentration": top_n_concentration,
    "cagr": cagr,
    "growth_rate": growth_rate,
    "trend_direction": trend_direction,
    "volatility": volatility,
    "seasonality": seasonality,
    "turnover_rate": turnover_rate,
    "average": average,
    "count_rate": count_rate,
    "safe_ratio": safe_ratio,
    "positive_denominator_ratio": positive_denominator_ratio,
    "days_outstanding": days_outstanding,
    "cash_conversion_cycle": cash_conversion_cycle,
    "price_volume_decomposition": price_volume_decomposition,
    "downside_stress": downside_stress,
    "amortization_bullet": amortization_bullet,
    "coverage_ratio": coverage_ratio,
    "covenant_headroom": covenant_headroom,
    "max_feasible_facility": max_feasible_facility,
    "sensitivity": sensitivity,
}
