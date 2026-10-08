"""Milestone 4 — agent registry (Req 5, 28, 29).

Proves: the roster is exactly 27 logical jobs; validation rejects duplicate ids,
unknown deps, cycles and multi-owned parameters; the DAG waves respect
dependencies with ~15 ready in Wave 0; descendant traversal returns the correct
dependent set; a semantic change moves the agent_definition_hash.
"""

from __future__ import annotations

import pytest

from app.schemas.agentic import ModelTier, Topic
from app.services.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    AgentRegistryError,
    default_registry,
)


def test_roster_is_exactly_27_logical_jobs():
    reg = default_registry()
    assert len(reg) == 27


def test_roster_composition():
    reg = default_registry()
    by_topic = {t: 0 for t in Topic}
    tiers = {t: 0 for t in ModelTier}
    for d in reg.all():
        by_topic[d.topic] += 1
        tiers[d.model_tier] += 1
    # 6 business narrow + business orchestrator + business challenge = 8
    assert by_topic[Topic.BUSINESS] == 8
    assert by_topic[Topic.FINANCIAL] == 8
    # 3 extraction + 4 mitigant + orchestrator + challenge = 9
    assert by_topic[Topic.STRUCTURING] == 9
    # credit orchestrator + cross-topic challenge = 2
    assert by_topic[Topic.CROSS_TOPIC] == 2
    assert tiers[ModelTier.CHALLENGE] == 4  # 4 challengers


def test_duplicate_agent_id_rejected():
    d = AgentDefinition("a", Topic.BUSINESS, "x", ModelTier.NARROW)
    with pytest.raises(AgentRegistryError, match="Duplicate"):
        AgentRegistry([d, d])


def test_unknown_dependency_rejected():
    with pytest.raises(AgentRegistryError, match="unknown agent"):
        AgentRegistry(
            [AgentDefinition("a", Topic.BUSINESS, "x", ModelTier.NARROW,
                             dependencies=("ghost",))]
        )


def test_cyclic_dependency_rejected():
    a = AgentDefinition("a", Topic.BUSINESS, "x", ModelTier.NARROW,
                        dependencies=("b",))
    b = AgentDefinition("b", Topic.BUSINESS, "x", ModelTier.NARROW,
                        dependencies=("a",))
    with pytest.raises(AgentRegistryError, match="Cyclic"):
        AgentRegistry([a, b])


def test_multi_owned_parameter_rejected():
    a = AgentDefinition("a", Topic.BUSINESS, "x", ModelTier.NARROW,
                        owned_parameters=("p",))
    b = AgentDefinition("b", Topic.BUSINESS, "x", ModelTier.NARROW,
                        owned_parameters=("p",))
    with pytest.raises(AgentRegistryError, match="owned by both"):
        AgentRegistry([a, b])


def test_parameter_ownership_is_unique_in_default_roster():
    reg = default_registry()
    seen: set[str] = set()
    for d in reg.all():
        for p in d.owned_parameters:
            assert p not in seen, f"parameter {p} owned twice"
            seen.add(p)


def test_topological_waves_respect_dependencies():
    reg = default_registry()
    waves = reg.topological_waves()
    # Wave 0 is the narrow + early-extraction set (no dependencies) ~ 15 jobs.
    assert len(waves[0]) == 15
    # Orchestrators appear only after their narrow agents.
    position = {a: i for i, wave in enumerate(waves) for a in wave}
    assert position["business_orchestrator"] > position["business_model"]
    assert position["business_challenge"] > position["business_orchestrator"]
    assert position["credit_orchestrator"] > position["structuring_challenge"]
    assert position["cross_topic_challenge"] > position["credit_orchestrator"]


def test_mitigants_wait_for_business_and_financial():
    reg = default_registry()
    position = {a: i for i, wave in enumerate(reg.topological_waves())
                for a in wave}
    for mitigant in (
        "liquidity_refinancing_mitigant",
        "leverage_coverage_mitigant",
        "business_concentration_mitigant",
        "governance_information_mitigant",
    ):
        assert position[mitigant] > position["business_challenge"]
        assert position[mitigant] > position["financial_challenge"]


def test_descendants_of_business_model_excludes_unrelated_financial():
    reg = default_registry()
    descendants = reg.descendants_of("customer_supplier_contract")
    # Business score/orchestrator/challenge recompute; cross-topic recomputes.
    assert "business_orchestrator" in descendants
    assert "business_challenge" in descendants
    assert "credit_orchestrator" in descendants
    assert "cross_topic_challenge" in descendants
    # Structuring mitigants/orchestrator depend on business_challenge => included.
    assert "structuring_orchestrator" in descendants
    # Unrelated Financial narrow agents are NOT descendants (Remediation 2).
    assert "ebitda_adjustments" not in descendants
    assert "covenant_extraction" not in descendants
    # The node itself is never in its own descendant set.
    assert "customer_supplier_contract" not in descendants


def test_registry_and_definition_hashes_change_on_semantic_edit():
    reg = default_registry()
    h1 = reg.registry_hash()
    d1 = reg.definition_hash("business_model")

    # Change owned parameters => definition + registry hash both change.
    edited = [
        d if d.agent_id != "business_model"
        else AgentDefinition(
            agent_id=d.agent_id, topic=d.topic, task_type=d.task_type,
            model_tier=d.model_tier, dependencies=d.dependencies,
            owned_parameters=(*d.owned_parameters, "new_param"),
            prompt_name=d.prompt_name, response_schema_ref=d.response_schema_ref,
            routing=d.routing, selector_identity=d.selector_identity,
        )
        for d in reg.all()
    ]
    reg2 = AgentRegistry(edited)
    assert reg2.definition_hash("business_model") != d1
    assert reg2.registry_hash() != h1


def test_prompt_defaults_to_agent_id():
    d = AgentDefinition("some_agent", Topic.BUSINESS, "x", ModelTier.NARROW)
    assert d.prompt == "some_agent"


# --- routing fixes (review points 5 & 6) ------------------------------------


def test_collateral_agent_is_routed_collateral_and_guarantor_evidence():
    # Previously this agent had NO selector and received nothing (review point 5).
    from app.services.agents.router import EvidenceRouter, RouterInputs

    reg = default_registry()
    spec = reg.get("collateral_security_guarantee").routing
    inputs = RouterInputs(
        case_id="C1", snapshot_version=1, analysis_run_id="AR1",
        facility_terms=[
            {"id": "ft_amt", "term": "facility_amount", "value": 1000},
            {"id": "ft_col", "term": "collateral", "value": "fleet aircraft"},
            {"id": "ft_grt", "term": "guarantee", "value": "parent guarantee"},
        ],
        entity_relationships=[
            {"entity_id": "SUB", "entity_type": "guarantor"},
            {"entity_id": "PARENT", "entity_type": "parent"},
        ],
    )
    packet = EvidenceRouter().build_packet(spec, inputs)
    kinds = {t.get("term") for t in packet.facility_terms}
    assert "collateral" in kinds and "guarantee" in kinds
    assert "facility_amount" not in kinds  # core terms go to facility_terms agent
    assert any(e["entity_id"] == "SUB" for e in packet.entity_relationships)
    assert all(e["entity_id"] != "PARENT" for e in packet.entity_relationships)


def test_facility_terms_agent_excludes_collateral():
    from app.services.agents.router import EvidenceRouter, RouterInputs

    reg = default_registry()
    spec = reg.get("facility_terms").routing
    inputs = RouterInputs(
        case_id="C1", snapshot_version=1, analysis_run_id="AR1",
        facility_terms=[
            {"id": "ft_amt", "term": "facility_amount", "value": 1000},
            {"id": "ft_col", "term": "collateral", "value": "fleet"},
        ],
    )
    packet = EvidenceRouter().build_packet(spec, inputs)
    kinds = {t.get("term") for t in packet.facility_terms}
    assert "facility_amount" in kinds
    assert "collateral" not in kinds


def test_business_agents_have_distinct_narrow_topics():
    reg = default_registry()
    bm = set(reg.get("business_model").selector_identity)
    mg = set(reg.get("management_governance").selector_identity)
    cp = set(reg.get("competition_pricing").selector_identity)
    # business_model does not pull the management corpus; management_governance
    # does not pull the competitive-position corpus (review point 6).
    assert "narrative:management" not in bm
    assert "narrative:competitive_position" not in mg
    assert "narrative:management" in mg
    assert "narrative:competitive_position" in cp
    # The six business agents are not all identical.
    identities = {d.agent_id: tuple(d.selector_identity)
                  for d in reg.all() if d.topic.value == "business"
                  and d.task_type not in {"orchestrate", "challenge"}}
    assert len(set(identities.values())) > 1
