"""Milestones 14-15 — early Structuring extraction + risk-to-mitigant agents.

M14: the three structuring extraction agents are schedulable in Wave 0,
concurrently with Business/Financial (they have no dependencies).
M15: the four risk-to-mitigant agents are gated behind the accepted Business and
Financial conclusions (business_challenge + financial_challenge) and propose
bounded mitigants that promote to owned ParameterResults.
"""

from __future__ import annotations

from app.services.agents.registry import default_registry
from app.services.orchestration.promotion import promote_mitigant_output

EXTRACTION_AGENTS = {
    "facility_terms",
    "collateral_security_guarantee",
    "legal_undertakings_conditions",
}
MITIGANT_AGENTS = {
    "liquidity_refinancing_mitigant",
    "leverage_coverage_mitigant",
    "business_concentration_mitigant",
    "governance_information_mitigant",
}


def test_structuring_extraction_agents_are_in_wave0():
    reg = default_registry()
    wave0 = set(reg.topological_waves()[0])
    assert EXTRACTION_AGENTS <= wave0  # concurrent with Business/Financial
    for a in EXTRACTION_AGENTS:
        assert reg.get(a).dependencies == ()  # no deps => early extraction


def test_mitigant_agents_wait_for_business_and_financial():
    reg = default_registry()
    position = {a: i for i, wave in enumerate(reg.topological_waves())
                for a in wave}
    for m in MITIGANT_AGENTS:
        deps = set(reg.get(m).dependencies)
        assert deps == {"business_challenge", "financial_challenge"}
        assert position[m] > position["business_challenge"]
        assert position[m] > position["financial_challenge"]
        # And strictly after the narrow extraction / Wave 0.
        assert position[m] > 0


def test_mitigant_promotion_keeps_only_owned_bounded_proposals():
    reg = default_registry()
    agent = reg.get("liquidity_refinancing_mitigant")
    parsed = {
        "proposals": [
            {"parameter_id": "liquidity_refinancing_mitigant_proposal",
             "mitigant": "Require a minimum-liquidity covenant of $500m.",
             "addresses_risk": "downside liquidity", "bounded": True},
            {"parameter_id": "not_owned_proposal", "mitigant": "unrelated"},
        ]
    }
    results = promote_mitigant_output(
        agent, parsed, analysis_run_id="AR1", agent_run_id="run-m",
    )
    pids = {r.parameter_id for r in results}
    assert "liquidity_refinancing_mitigant_proposal" in pids
    assert "not_owned_proposal" not in pids  # ownership enforced
    r = results[0]
    assert r.value_type == "text"
    assert r.agent_run_id == "run-m"
    assert r.notes == "downside liquidity"


def test_structuring_orchestrator_waits_for_extraction_and_mitigants():
    reg = default_registry()
    deps = set(reg.get("structuring_orchestrator").dependencies)
    assert EXTRACTION_AGENTS <= deps
    assert MITIGANT_AGENTS <= deps
