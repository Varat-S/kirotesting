"""Deterministic structuring feasibility engine (Milestone 16 / Req 13.4-13.5).

Tests each immutable candidate against deterministic feasibility and policy
checks and emits a versioned, append-only ``CandidateFeasibility``. An infeasible
candidate cannot be selected (M17). The engine NEVER calls an LLM and NEVER
overrides a deterministic failure.

Checks (illustrative thresholds come from the versioned ``policy`` / ``scoring``
config in a full wiring; here they are passed in explicitly):

* facility amount within the maximum feasible facility size,
* base-case and downside leverage within the policy limit,
* base-case and downside DSCR at or above the minimum,
* liquidity coverage at or above the minimum,
* LTV at or below the maximum (when collateral is pledged),
* required protections present (e.g. a covenant package when policy requires it).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.schemas.agentic import CandidateFeasibility, CandidateStructure
from app.services.structuring.stress import StressResult, stress_candidate


@dataclass(frozen=True)
class FeasibilityPolicy:
    """Illustrative deterministic feasibility thresholds (NOT bank policy)."""

    max_leverage: float = 4.0
    min_dscr: float = 1.0
    min_downside_dscr: float = 1.0
    min_liquidity_coverage: float = 1.0
    max_ltv: float = 0.8
    require_covenant_package: bool = True


@dataclass
class CandidateEconomics:
    """The deterministic inputs needed to test a candidate."""

    ebitda: float
    debt_service: float
    net_debt: float
    available_liquidity: float
    liquidity_need: float
    collateral_value: float | None = None
    max_feasible_facility: float | None = None
    downside_shock_pct: float = -0.2


class StructuringEngine:
    """Deterministic feasibility + policy testing of candidate structures."""

    def __init__(self, policy: FeasibilityPolicy | None = None) -> None:
        self._policy = policy or FeasibilityPolicy()

    def evaluate(
        self,
        candidate: CandidateStructure,
        economics: CandidateEconomics,
    ) -> CandidateFeasibility:
        """Return an append-only feasibility verdict for ``candidate``."""
        failures: list[str] = []
        detail: dict[str, Any] = {}

        stress = stress_candidate(
            ebitda=economics.ebitda,
            debt_service=economics.debt_service,
            net_debt=economics.net_debt,
            available_liquidity=economics.available_liquidity,
            liquidity_need=economics.liquidity_need,
            downside_shock_pct=economics.downside_shock_pct,
        )
        detail["stress"] = {k: vars(v) for k, v in stress.items()}

        base, downside = stress["base"], stress["downside"]

        # Facility size vs maximum feasible facility.
        if (
            economics.max_feasible_facility is not None
            and candidate.facility_amount is not None
            and candidate.facility_amount > economics.max_feasible_facility + 1e-9
        ):
            failures.append("facility_amount_exceeds_max_feasible")

        # Leverage within policy (base + downside).
        self._check_max(base.leverage, self._policy.max_leverage,
                        "base_leverage_exceeds_policy", failures)
        self._check_max(downside.leverage, self._policy.max_leverage,
                        "downside_leverage_exceeds_policy", failures)

        # DSCR at/above minimum (base + downside).
        self._check_min(base.dscr, self._policy.min_dscr,
                        "base_dscr_below_minimum", failures)
        self._check_min(downside.dscr, self._policy.min_downside_dscr,
                        "downside_dscr_below_minimum", failures)

        # Liquidity coverage.
        self._check_min(base.liquidity_coverage, self._policy.min_liquidity_coverage,
                        "liquidity_coverage_below_minimum", failures)

        # LTV when collateral is pledged.
        if candidate.collateral and economics.collateral_value:
            ltv = (candidate.facility_amount or 0.0) / economics.collateral_value
            detail["ltv"] = round(ltv, 6)
            if ltv > self._policy.max_ltv + 1e-9:
                failures.append("ltv_exceeds_policy")

        # Required protections present.
        if self._policy.require_covenant_package and not candidate.covenant_package:
            failures.append("missing_required_covenant_package")

        feasible = not failures
        detail["failures"] = failures
        return CandidateFeasibility(
            feasibility_id=f"fe_{uuid.uuid4().hex[:16]}",
            analysis_run_id=candidate.analysis_run_id,
            candidate_id=candidate.candidate_id,
            feasible=feasible,
            feasibility_detail=detail,
        )

    @staticmethod
    def _check_max(value, limit, code, failures) -> None:
        if value is not None and value > limit + 1e-9:
            failures.append(code)

    @staticmethod
    def _check_min(value, minimum, code, failures) -> None:
        if value is None or value < minimum - 1e-9:
            failures.append(code)
