"""Milestone 6 — result cache / idempotency (Req 18, 28).

Proves: reuse on unchanged inputs; invalidation on any identity change
(evidence snapshot, prompt, model, router, packet, AND agent-definition /
response-schema even when prompt text is unchanged); force_regenerate bypass; a
cached result must have previously passed validation.
"""

from __future__ import annotations

from dataclasses import replace

from app.models.orm import AgentRunRow
from app.services.agents.cache import AgentResultCache, CacheIdentity


def _identity(**overrides) -> CacheIdentity:
    base = dict(
        case_id="C1",
        evidence_snapshot_version=1,
        agent_id="business_model",
        agent_definition_hash="adh-1",
        prompt_hash="ph-1",
        response_schema_hash="rsh-1",
        model_id="fake-deterministic-v1",
        model_config={"temperature": 0.0},
        router_version="router-1",
        packet_hash="pkt-1",
    )
    base.update(overrides)
    return CacheIdentity(**base)


def _seed_run(db_session, identity, *, status="valid", parsed=None, run_id="run-1"):
    db_session.add(
        AgentRunRow(
            run_id=run_id,
            analysis_run_id="AR1",
            agent_id=identity.agent_id,
            agent_definition_hash=identity.agent_definition_hash,
            topic="business",
            case_id=identity.case_id,
            snapshot_version=identity.evidence_snapshot_version,
            prompt_id="p",
            prompt_hash=identity.prompt_hash,
            model_id=identity.model_id,
            input_hash=identity.packet_hash,
            cache_key=identity.key(),
            validation_status=status,
            parsed_response=parsed or {"ok": True},
            execution_wave=0,
        )
    )
    db_session.flush()


def test_reuse_on_unchanged_inputs(db_session):
    identity = _identity()
    _seed_run(db_session, identity, parsed={"result": 42})
    cache = AgentResultCache(db_session)
    hit = cache.lookup(identity)
    assert hit is not None
    assert hit.run_id == "run-1"
    assert hit.parsed == {"result": 42}


def test_force_regenerate_bypasses_cache(db_session):
    identity = _identity()
    _seed_run(db_session, identity)
    cache = AgentResultCache(db_session)
    assert cache.lookup(identity, force_regenerate=True) is None


def test_rejected_runs_are_not_reused(db_session):
    identity = _identity()
    _seed_run(db_session, identity, status="rejected")
    cache = AgentResultCache(db_session)
    assert cache.lookup(identity) is None  # must have passed validation (Req 18.5)


def test_invalidation_on_each_identity_change(db_session):
    identity = _identity()
    _seed_run(db_session, identity)
    cache = AgentResultCache(db_session)

    changed_identities = [
        replace(identity, evidence_snapshot_version=2),
        replace(identity, prompt_hash="ph-2"),
        replace(identity, model_id="other-model"),
        replace(identity, model_config={"temperature": 0.2}),
        replace(identity, router_version="router-2"),
        replace(identity, packet_hash="pkt-2"),
        replace(identity, agent_definition_hash="adh-2"),  # semantic change
        replace(identity, response_schema_hash="rsh-2"),
    ]
    for changed in changed_identities:
        assert changed.key() != identity.key()
        assert cache.lookup(changed) is None


def test_agent_definition_change_invalidates_even_with_same_prompt(db_session):
    # Same prompt_hash, different agent_definition_hash => different key => miss.
    identity = _identity()
    _seed_run(db_session, identity)
    cache = AgentResultCache(db_session)
    semantic_change = replace(identity, agent_definition_hash="adh-NEW")
    assert semantic_change.prompt_hash == identity.prompt_hash
    assert cache.lookup(semantic_change) is None


def test_key_is_deterministic():
    assert _identity().key() == _identity().key()
