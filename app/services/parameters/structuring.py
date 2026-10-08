"""Structuring deterministic parameter definitions (Milestone 7).

Structuring calculations (Req 7.4): amortization/bullet exposure, LTV, collateral
coverage, DSCR / leverage / liquidity UNDER a candidate structure, covenant
headroom, and the maximum feasible facility size. These feed the deterministic
candidate-structure feasibility engine (Milestone 16). The LLM proposes candidate
terms; every number here is computed deterministically.
"""

from __future__ import annotations

from app.schemas.agentic import Topic
from app.services.parameters.registry import ParameterDefinition

_S = Topic.STRUCTURING


def structuring_parameter_definitions() -> list[ParameterDefinition]:
    return [
        ParameterDefinition("amortization_bullet", _S, "amortization_bullet",
                            "number", description="Bullet/balloon amount at maturity."),
        ParameterDefinition("bullet_to_ebitda", _S, "safe_ratio", "ratio",
                            description="Bullet exposure / EBITDA."),
        ParameterDefinition("bullet_to_fcf", _S, "safe_ratio", "ratio",
                            description="Bullet exposure / FCF."),
        ParameterDefinition("ltv", _S, "safe_ratio", "ratio",
                            description="Loan-to-value under the candidate structure."),
        ParameterDefinition("collateral_coverage", _S, "coverage_ratio", "ratio",
                            description="Collateral value / exposure."),
        ParameterDefinition("guarantee_support", _S, "coverage_ratio", "ratio",
                            description="Guaranteed amount / exposure."),
        ParameterDefinition("candidate_dscr", _S, "coverage_ratio", "ratio",
                            description="DSCR under the candidate structure."),
        ParameterDefinition("candidate_leverage", _S, "safe_ratio", "ratio",
                            description="Net leverage under the candidate structure."),
        ParameterDefinition("candidate_liquidity", _S, "coverage_ratio", "ratio",
                            description="Liquidity coverage under the candidate."),
        ParameterDefinition("candidate_covenant_headroom", _S, "covenant_headroom",
                            "ratio",
                            description="Covenant headroom under the candidate."),
        ParameterDefinition("stressed_candidate_dscr", _S, "coverage_ratio", "ratio",
                            description="Candidate DSCR under the downside scenario."),
        ParameterDefinition("max_feasible_facility", _S, "max_feasible_facility",
                            "number",
                            description="Maximum facility size within policy."),
    ]
