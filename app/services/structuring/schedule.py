"""Candidate-driven amortization / debt-service engine (M16 / R6 item 13).

Candidate economics are DERIVED from the ``CandidateStructure`` terms plus the
borrower's financial state — two different structures (e.g. a 5-year bullet vs a
5-year straight-line amortizer) MUST produce different debt-service, DSCR,
leverage and bullet exposure. Nothing here is an opaque externally-supplied
number (except the borrower forecast fixtures).

Supported minimum (expandable later): facility amount, tenor, straight-line
amortization or bullet, simple annual interest rate, ending debt, annual debt
service, bullet amount, and base/downside CFADS + EBITDA passed through from the
borrower forecast. Missing terms yield explicit ``None`` (never coerced to 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.agentic import CandidateStructure


@dataclass(frozen=True)
class BorrowerForecast:
    """Borrower financial state used to evaluate a candidate (test fixture)."""

    ebitda: float
    cfads: float
    existing_net_debt: float = 0.0
    available_liquidity: float = 0.0
    liquidity_need: float = 0.0
    collateral_value: float | None = None
    downside_shock_pct: float = -0.2


@dataclass(frozen=True)
class DerivedEconomics:
    """Candidate economics derived deterministically from its terms."""

    facility_amount: float | None
    tenor_months: int | None
    annual_principal: float | None       # scheduled amortization per year
    annual_interest: float | None        # cash interest (first-year, on drawn amount)
    annual_debt_service: float | None    # principal + interest
    bullet_amount: float | None          # balloon at maturity
    ending_net_debt: float | None        # existing + facility
    ebitda: float
    cfads: float
    available_liquidity: float
    liquidity_need: float
    collateral_value: float | None
    downside_shock_pct: float


def derive_economics(
    candidate: CandidateStructure, forecast: BorrowerForecast
) -> DerivedEconomics:
    """Derive annual debt service, bullet and ending debt from candidate terms.

    ``amortization`` on the candidate may specify ``{"type": "straight_line"}``
    or ``{"type": "bullet"}``; ``interest_rate`` (annual, fractional) may be set
    via ``amortization.interest_rate`` or the facility's own field. Missing
    amount/tenor/rate propagate as ``None`` so DSCR/leverage become unavailable
    rather than falsely favourable (item 14).
    """
    amount = candidate.facility_amount          # may be None -> unavailable
    tenor = candidate.tenor_months
    amort = candidate.amortization or {}
    rate = amort.get("interest_rate")

    years = (tenor / 12.0) if tenor else None
    annual_principal: float | None = None
    bullet: float | None = None
    if amount is not None and years:
        if amort.get("type") == "bullet":
            annual_principal = 0.0
            bullet = amount
        else:  # straight-line (default)
            annual_principal = round(amount / years, 6)
            bullet = 0.0

    annual_interest = (round(amount * rate, 6)
                       if amount is not None and rate is not None else None)
    annual_debt_service = (
        None if annual_principal is None or annual_interest is None
        else round(annual_principal + annual_interest, 6)
    )
    ending_net_debt = (None if amount is None
                       else round(forecast.existing_net_debt + amount, 6))

    return DerivedEconomics(
        facility_amount=amount,
        tenor_months=tenor,
        annual_principal=annual_principal,
        annual_interest=annual_interest,
        annual_debt_service=annual_debt_service,
        bullet_amount=bullet,
        ending_net_debt=ending_net_debt,
        ebitda=forecast.ebitda,
        cfads=forecast.cfads,
        available_liquidity=forecast.available_liquidity,
        liquidity_need=forecast.liquidity_need,
        collateral_value=forecast.collateral_value,
        downside_shock_pct=forecast.downside_shock_pct,
    )
