"""Deterministic base/downside stress for candidate structures (Milestone 16).

Pure arithmetic over a candidate's economics under a borrower scenario. Reuses
the parameter engine's zero-safe formulas conceptually; here we compute the
candidate's DSCR / leverage / liquidity under base and downside EBITDA. No LLM.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StressResult:
    scenario: str
    dscr: float | None
    leverage: float | None
    liquidity_coverage: float | None


def _safe_div(num: float, den: float, floor: float = 1e-9) -> float | None:
    return None if abs(den) <= floor else round(num / den, 6)


def stress_candidate(
    *,
    ebitda: float,
    debt_service: float,
    net_debt: float,
    available_liquidity: float,
    liquidity_need: float,
    downside_shock_pct: float = -0.2,
) -> dict[str, StressResult]:
    """Compute base + downside DSCR/leverage/liquidity for a candidate.

    ``debt_service`` and ``net_debt`` reflect the CANDIDATE structure's terms.
    Returns a mapping ``{"base": StressResult, "downside": StressResult}``.
    """
    def _for(scenario: str, e: float) -> StressResult:
        return StressResult(
            scenario=scenario,
            dscr=_safe_div(e, debt_service),
            leverage=_safe_div(net_debt, e),
            liquidity_coverage=_safe_div(available_liquidity, liquidity_need),
        )

    downside_ebitda = ebitda * (1.0 + downside_shock_pct)
    return {"base": _for("base", ebitda), "downside": _for("downside", downside_ebitda)}
