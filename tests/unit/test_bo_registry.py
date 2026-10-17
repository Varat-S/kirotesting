"""Acceptance A — the versioned six-agent Business Overview roster."""

from __future__ import annotations

import dataclasses

import pytest

from app.schemas.agentic import ModelTier, Topic
from app.services.agents.registry import (
    AGENT_REGISTRY_VERSION,
    AGENT_REGISTRY_VERSION_BO_V2,
    AgentRegistry,
    AgentRegistryError,
    default_registry,
    deterministic_parameter_ids,
)
from app.services.bo.plans import (
    BO_AGENT_SPECS,
    BO_NARROW_AGENT_IDS,
    BO_PRIMARY_AGENT_IDS,
    BO_RESILIENCE_AGENT_ID,
    BO_V1_TO_V2_AGENT,
)


def _business_narrow(registry):
    return [d for d in registry.all()
            if d.topic is Topic.BUSINESS and d.model_tier is ModelTier.NARROW]


def test_v2_has_exactly_six_business_narrow_agents_and_27_jobs():
    registry = default_registry("v2")
    assert len(registry) == 27
    assert registry.version == AGENT_REGISTRY_VERSION_BO_V2
    assert [d.agent_id for d in _business_narrow(registry)] == list(BO_NARROW_AGENT_IDS)
    assert len(BO_NARROW_AGENT_IDS) == 6


def test_v1_roster_is_unchanged_and_still_the_default():
    registry = default_registry()
    assert registry.version == AGENT_REGISTRY_VERSION
    assert {d.agent_id for d in _business_narrow(registry)} == set(BO_V1_TO_V2_AGENT)
    assert default_registry("v1").registry_hash() == registry.registry_hash()
    with pytest.raises(AgentRegistryError):
        default_registry("v9")


def test_every_business_parameter_has_exactly_one_owner():
    registry = default_registry("v2")
    owned = [p for spec in BO_AGENT_SPECS for p in spec.owned_parameters]
    assert len(owned) == len(set(owned))
    for spec in BO_AGENT_SPECS:
        for parameter in spec.owned_parameters:
            assert registry.owner_of(parameter) == spec.agent_id


def test_v1_parameter_ids_are_preserved_and_remapped_to_the_declared_owner():
    v1, v2 = default_registry(), default_registry("v2")
    for definition in _business_narrow(v1):
        for parameter in definition.owned_parameters:
            assert v2.owner_of(parameter) == BO_V1_TO_V2_AGENT[definition.agent_id]


def test_no_llm_agent_owns_a_deterministic_parameter():
    deterministic = deterministic_parameter_ids()
    for definition in default_registry("v2").all():
        assert not set(definition.owned_parameters) & deterministic
    assert default_registry("v2").owner_of("customer_hhi") is None


def test_bo6_depends_on_the_validated_primary_agents_and_dag_is_acyclic():
    registry = default_registry("v2")
    assert set(registry.get(BO_RESILIENCE_AGENT_ID).dependencies) == set(
        BO_PRIMARY_AGENT_IDS)
    position = {a: i for i, wave in enumerate(registry.topological_waves())
                for a in wave}
    for primary in BO_PRIMARY_AGENT_IDS:
        assert position[primary] < position[BO_RESILIENCE_AGENT_ID]
    assert position[BO_RESILIENCE_AGENT_ID] < position["business_orchestrator"]
    assert position["business_orchestrator"] < position["business_challenge"]
    assert BO_RESILIENCE_AGENT_ID in registry.get("business_orchestrator").dependencies
    assert BO_RESILIENCE_AGENT_ID in registry.descendants_of(BO_PRIMARY_AGENT_IDS[0])


def test_bo6_owns_no_score_and_orchestrator_owns_nothing():
    registry = default_registry("v2")
    assert not any("score" in p or "signal" in p
                   for p in registry.get(BO_RESILIENCE_AGENT_ID).owned_parameters)
    assert registry.get("business_orchestrator").owned_parameters == ()


def test_definition_hash_changes_when_semantics_change():
    registry = default_registry("v2")
    target = registry.get("bo_competition_pricing")
    edited = dataclasses.replace(
        target, selector_identity=(*target.selector_identity, "narrative:new_terms"))
    changed = AgentRegistry(
        [edited if d.agent_id == target.agent_id else d for d in registry.all()],
        version=registry.version)
    assert changed.definition_hash(target.agent_id) != target.definition_hash()
    assert changed.registry_hash() != registry.registry_hash()


def test_orchestrator_and_challenger_definitions_differ_between_rosters():
    v1, v2 = default_registry(), default_registry("v2")
    for agent_id in ("business_orchestrator", "business_challenge"):
        assert v1.definition_hash(agent_id) != v2.definition_hash(agent_id)
        assert v1.get(agent_id).prompt == agent_id
        assert v2.get(agent_id).prompt.startswith("bo_v2_")
    # Non-Business jobs are byte-for-byte the same definition in both rosters.
    for agent_id in ("ebitda_adjustments", "covenant_extraction", "facility_terms"):
        assert v1.definition_hash(agent_id) == v2.definition_hash(agent_id)


def test_old_agent_run_records_remain_readable(db_session):
    """A run recorded under the v1 roster still loads next to a v2 run."""
    from app.models.orm import AgenticAnalysisRun, AgentRunRow

    v1 = default_registry()
    db_session.add(AgenticAnalysisRun(
        analysis_run_id="ar_old", case_id="C1", evidence_snapshot_version=1,
        router_version="r", agent_registry_version=v1.version,
        agent_registry_hash=v1.registry_hash(), scoring_config_version=1,
        scoring_config_hash="h"))
    db_session.add(AgentRunRow(
        run_id="run_old", analysis_run_id="ar_old", agent_id="business_model",
        agent_definition_hash=v1.definition_hash("business_model"), topic="business",
        case_id="C1", snapshot_version=1, prompt_id="business_model_v1.0",
        prompt_hash="p", model_id="m", input_hash="i", validation_status="valid"))
    db_session.flush()
    row = db_session.get(AgentRunRow, "run_old")
    assert row.agent_definition_hash == default_registry().definition_hash(row.agent_id)
    assert BO_V1_TO_V2_AGENT[row.agent_id] in default_registry("v2")
