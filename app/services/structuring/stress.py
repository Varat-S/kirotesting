"""Deterministic base/downside stress for candidate structures (M16 / R6).

Repayment capacity uses the CORRECT definition (item 12):

    DSCR = CFADS / Debt Service

where ``Debt Service = scheduled principal + cash interest + other configured
obligations``. EBITDA is used for LEVERAGE, never substituted for CFADS.

Missing inputs are never coerced to zero (item 14): a missing CFADS or debt
service yields ``None`` (unavailable), never a favourable 0.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StressResult:
    scenario: str
    dscr: float | None            # CFADS / debt service
    leverage: float | None        # net debt / EBITDA
    liquidity_coverage: float | None


def _safe_div(num: float | None, den: float | None, floor: float = 1e-9) -> float | None:
    # Missing numerator or denominator => unavailable (never 0, item 14).
    if num is None or den is None:
        return None
    return None if abs(den) <= floor else round(num / den, 6)


def stress_candidate(
    *,
    ebitda: float | None,
    cfads: float | None,
    debt_service: float | None,
    net_debt: float | None,
    available_liquidity: float | None,
    liquidity_need: float | None,
    downside_shock_pct: float = -0.2,
    cfads_downside_shock_pct: float | None = None,
) -> dict[str, StressResult]:
    """Base + downside DSCR (CFADS/service), leverage and liquidity.

    ``cfads`` drives DSCR; ``ebitda`` drives leverage. Downside applies the shock
    to BOTH CFADS and EBITDA (CFADS may use its own shock if supplied). Any
    missing input propagates as ``None`` (unavailable).
    """
    cfads_shock = (cfads_downside_shock_pct
                   if cfads_downside_shock_pct is not None else downside_shock_pct)

    def _for(scenario: str, e: float | None, c: float | None) -> StressResult:
        return StressResult(
            scenario=scenario,
            dscr=_safe_div(c, debt_service),             # CFADS / debt service
            leverage=_safe_div(net_debt, e),             # net debt / EBITDA
            liquidity_coverage=_safe_div(available_liquidity, liquidity_need),
        )

    down_ebitda = None if ebitda is None else ebitda * (1.0 + downside_shock_pct)
    down_cfads = None if cfads is None else cfads * (1.0 + cfads_shock)
    return {
        "base": _for("base", ebitda, cfads),
        "downside": _for("downside", down_ebitda, down_cfads),
    }
