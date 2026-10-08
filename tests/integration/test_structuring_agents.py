"""Milestones 14-15 — early Structuring extraction + risk-to-mitigant agents.

M14: the three structuring extraction agents are schedulable in Wave 0,
concurrently with Business/Financial (they have no dependencies).
M15: the four risk-to-mitigant agents are gated behind the accepted Business and
Financial conclusions (business_challenge + financial_challenge) and propose
bounded mitigants that promote to owned ParameterResults.
"""

from __future__ import annotations

from app.services.agents.registry import default_registry
from app.services.orchestration.promotion import promote_mitigant_output  # noqa: F401

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


def _validated_mitigant(agent, parsed):
    from app.schemas.agentic import EvidencePacket
    from app.services.agents.validation import ValidationOutcome
    from app.services.orchestration.promotion import ValidatedAgentOutput

    # Mitigant outputs have their own schema; the gate just needs a valid outcome.
    outcome = ValidationOutcome(valid=True)
    return ValidatedAgentOutput.gate(agent, parsed, outcome,
                                     analysis_run_id="AR1", agent_run_id="run-m")


def test_mitigant_promotion_keeps_only_owned_bounded_proposals():
    reg = default_registry()
    agent = reg.get("liquidity_refinancing_mitigant")
    parsed = {
        "proposals": [
            {"parameter_id": "liquidity_refinancing_mitigant_proposal",
             "mitigant_type": "minimum_liquidity_covenant", "proposed_value": 500,
             "unit": "USDm", "addresses_risk": "downside liquidity",
             "bounded": True},
            # Unbounded proposal -> not eligible to feed candidate generation.
            {"parameter_id": "liquidity_refinancing_mitigant_proposal",
             "mitigant_type": "vague", "bounded": False},
            {"parameter_id": "not_owned_proposal", "mitigant_type": "x",
             "bounded": True},
        ]
    }
    results = promote_mitigant_output(_validated_mitigant(agent, parsed))
    assert len(results) == 1  # only the owned, bounded proposal
    r = results[0]
    assert r.parameter_id == "liquidity_refinancing_mitigant_proposal"
    assert r.value["mitigant_type"] == "minimum_liquidity_covenant"
    assert r.value["proposed_value"] == 500
    assert r.agent_run_id == "run-m"


def test_structuring_orchestrator_waits_for_extraction_and_mitigants():
    reg = default_registry()
    deps = set(reg.get("structuring_orchestrator").dependencies)
    assert EXTRACTION_AGENTS <= deps
    assert MITIGANT_AGENTS <= deps
