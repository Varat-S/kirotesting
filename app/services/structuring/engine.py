"""Deterministic structuring feasibility engine (M16 / R6 items 12-15).

Tests each immutable candidate against deterministic feasibility + policy checks
and emits a versioned, append-only ``CandidateFeasibility``. Key properties:

* economics are DERIVED from the ``CandidateStructure`` terms + borrower forecast
  (item 13) — different structures produce different debt-service/DSCR/leverage;
* DSCR = CFADS / debt service (item 12), never EBITDA;
* missing inputs are never coerced to zero (item 14): a missing facility amount
  or collateral value makes LTV ``unavailable`` and the candidate untestable
  rather than falsely 0% LTV;
* feasibility thresholds come from VERSIONED config (``policy.structuring``,
  item 15); the config version + hash are recorded on the verdict so the same
  candidate + borrower + policy version reproduces the same verdict.

The engine NEVER calls an LLM and NEVER overrides a deterministic failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.schemas.agentic import CandidateFeasibility, CandidateStructure
from app.services.structuring.schedule import (
    BorrowerForecast,
    DerivedEconomics,
    derive_economics,
)
from app.services.structuring.stress import stress_candidate


@dataclass(frozen=True)
class FeasibilityPolicy:
    """Versioned deterministic feasibility thresholds (ILLUSTRATIVE)."""

    max_leverage: float = 4.0
    min_dscr: float = 1.0
    min_downside_dscr: float = 1.0
    min_liquidity_coverage: float = 1.0
    max_ltv: float = 0.8
    require_covenant_package: bool = True
    config_version: int | None = None
    config_hash: str | None = None

    @classmethod
    def from_config(cls, policy_content: dict, *, version: int, content_hash: str
                    ) -> "FeasibilityPolicy":
        s = (policy_content or {}).get("structuring", {})
        return cls(
            max_leverage=float(s.get("max_leverage", 4.0)),
            min_dscr=float(s.get("min_dscr", 1.0)),
            min_downside_dscr=float(s.get("min_downside_dscr", 1.0)),
            min_liquidity_coverage=float(s.get("min_liquidity_coverage", 1.0)),
            max_ltv=float(s.get("max_ltv", 0.8)),
            require_covenant_package=bool(s.get("require_covenant_package", True)),
            config_version=version,
            config_hash=content_hash,
        )

    @classmethod
    def from_registry(cls, registry, *, version: int | None = None
                      ) -> "FeasibilityPolicy":
        if version is None:
            latest = registry.latest("policy")
            if latest is None:
                raise ValueError("No 'policy' configuration registered.")
            version = latest.version
        row = registry.get("policy", version)
        return cls.from_config(row.content, version=row.version,
                               content_hash=row.content_hash)


class StructuringEngine:
    """Deterministic feasibility + policy testing of candidate structures."""

    def __init__(self, policy: FeasibilityPolicy | None = None) -> None:
        self._policy = policy or FeasibilityPolicy()

    def evaluate(
        self,
        candidate: CandidateStructure,
        forecast: BorrowerForecast,
    ) -> CandidateFeasibility:
        """Derive economics from the candidate + forecast, then test them."""
        econ = derive_economics(candidate, forecast)
        return self._evaluate_economics(candidate, econ)

    def _evaluate_economics(
        self, candidate: CandidateStructure, econ: DerivedEconomics
    ) -> CandidateFeasibility:
        failures: list[str] = []
        unavailable: list[str] = []
        detail: dict[str, Any] = {}

        stress = stress_candidate(
            ebitda=econ.ebitda,
            cfads=econ.cfads,
            debt_service=econ.annual_debt_service,
            net_debt=econ.ending_net_debt,
            available_liquidity=econ.available_liquidity,
            liquidity_need=econ.liquidity_need,
            downside_shock_pct=econ.downside_shock_pct,
        )
        detail["economics"] = {
            "annual_principal": econ.annual_principal,
            "annual_interest": econ.annual_interest,
            "annual_debt_service": econ.annual_debt_service,
            "bullet_amount": econ.bullet_amount,
            "ending_net_debt": econ.ending_net_debt,
        }
        detail["stress"] = {k: vars(v) for k, v in stress.items()}
        base, downside = stress["base"], stress["downside"]

        # Leverage within policy (base + downside). Missing => unavailable.
        self._check_max(base.leverage, self._policy.max_leverage,
                        "base_leverage_exceeds_policy", failures, unavailable,
                        "base_leverage")
        self._check_max(downside.leverage, self._policy.max_leverage,
                        "downside_leverage_exceeds_policy", failures, unavailable,
                        "downside_leverage")

        # DSCR (CFADS/service) at/above minimum (base + downside).
        self._check_min(base.dscr, self._policy.min_dscr,
                        "base_dscr_below_minimum", failures, unavailable, "base_dscr")
        self._check_min(downside.dscr, self._policy.min_downside_dscr,
                        "downside_dscr_below_minimum", failures, unavailable,
                        "downside_dscr")

        # Liquidity coverage.
        self._check_min(base.liquidity_coverage, self._policy.min_liquidity_coverage,
                        "liquidity_coverage_below_minimum", failures, unavailable,
                        "liquidity_coverage")

        # LTV when collateral is pledged — missing inputs => unavailable, NOT 0.
        if candidate.collateral:
            if econ.facility_amount is None or econ.collateral_value is None:
                unavailable.append("ltv")
                detail["ltv"] = None
            elif econ.collateral_value <= 0:
                unavailable.append("ltv")
                detail["ltv"] = None
            else:
                ltv = econ.facility_amount / econ.collateral_value
                detail["ltv"] = round(ltv, 6)
                if ltv > self._policy.max_ltv + 1e-9:
                    failures.append("ltv_exceeds_policy")

        if self._policy.require_covenant_package and not candidate.covenant_package:
            failures.append("missing_required_covenant_package")

        # A candidate with an UNAVAILABLE core metric is not feasible (no
        # favourable assumption): it needs the missing input before approval.
        feasible = not failures and not unavailable
        detail["failures"] = failures
        detail["unavailable"] = unavailable
        detail["policy_version"] = self._policy.config_version
        detail["policy_hash"] = self._policy.config_hash
        return CandidateFeasibility(
            feasibility_id=f"fe_{uuid.uuid4().hex[:16]}",
            analysis_run_id=candidate.analysis_run_id,
            candidate_id=candidate.candidate_id,
            feasible=feasible,
            feasibility_detail=detail,
        )

    @staticmethod
    def _check_max(value, limit, code, failures, unavailable, name) -> None:
        if value is None:
            unavailable.append(name)
        elif value > limit + 1e-9:
            failures.append(code)

    @staticmethod
    def _check_min(value, minimum, code, failures, unavailable, name) -> None:
        if value is None:
            unavailable.append(name)
        elif value < minimum - 1e-9:
            failures.append(code)
