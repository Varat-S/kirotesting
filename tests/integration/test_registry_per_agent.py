"""Priority 3: individual-agent artifact discovery from a real recorded BO run.

Runs a full offline v3 BO run, writes its artifacts, then indexes them and
asserts that each BO specialist, the synthesis and the challenger are discovered
as independently inspectable artifacts (from the existing audit_report.json, no
new write path), that graph nodes resolve to the correct artifact within the
SAME run, and that clicking a specialist returns THAT specialist's output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from app.services.registry import build_manifest, build_pipeline_graph
from app.services.registry.explorer import inspect_node, load_artifact
from app.schemas.artifact_manifest import ArtifactType, ExecutionStatus
from tests import bo_helpers as h
from tests.integration.test_bo_v3_complete import _script


@pytest.fixture()
def bo_run(db_session, tmp_path):
    out = tmp_path / "out"
    runner = BusinessOverviewRunner(
        db_session, backend=ScriptedBoBackend(_script()), bo_roster="v3",
        data_root=tmp_path / "data", output_root=out)
    result = runner.run_prepared(h.CASE, h.evidence_snapshot(), h.metrics(),
                                 write_outputs=True)
    db_session.commit()
    return result, out


def _manifest(out: Path):
    return build_manifest(str(out), h.CASE, repo_root=str(out))


def test_each_specialist_is_discovered_as_an_artifact(bo_run):
    result, out = bo_run
    m = _manifest(out)
    agent_entries = {a.producer_agent for a in m.artifacts
                     if a.artifact_type == ArtifactType.AGENT_OUTPUT}
    # All six Module 1 agents + M2 + M4 strategy are individually present; Module 3
    # is not an agent, so it has no agent artifact.
    for aid in ("m1_revenue_quality", "m1_customer_stickiness", "m1_pricing_power",
                "m1_operating_model", "m1_competitive_durability",
                "m1_resilience_integration", "m2_industry", "m4_strategy_capital"):
        assert aid in agent_entries, aid
    assert "m3_corporate_management" not in agent_entries


def test_synthesis_and_challenger_are_distinct_artifacts(bo_run):
    result, out = bo_run
    m = _manifest(out)
    ids = {a.artifact_id for a in m.artifacts}
    assert any(i.startswith("bo.synthesis.") for i in ids)
    assert any(i.startswith("bo.challenger.") for i in ids)
    # The synthesis artifact is NOT the memo.
    syn = next(a for a in m.artifacts if a.artifact_id.startswith("bo.synthesis."))
    memo = next(a for a in m.artifacts if a.artifact_id.startswith("bo.memo."))
    assert syn.artifact_id != memo.artifact_id


def test_graph_nodes_resolve_to_per_agent_artifacts_in_same_run(bo_run):
    result, out = bo_run
    g = build_pipeline_graph(_manifest(out))
    run_id = result.analysis_run_id
    m2 = next(n for n in g["nodes"] if n["id"] == "agent.m2_industry")
    syn = next(n for n in g["nodes"] if n["id"] == "synthesis_v1")
    ch = next(n for n in g["nodes"] if n["id"] == "challenger")
    assert m2["artifact_id"] == f"bo.agent.m2_industry.{run_id}"
    assert syn["artifact_id"] == f"bo.synthesis.{run_id}"
    assert ch["artifact_id"] == f"bo.challenger.{run_id}"
    # Every resolved per-agent id carries the SAME run id (no cross-run leakage).
    for node in g["nodes"]:
        aid = node.get("artifact_id") or ""
        if aid.startswith(("bo.agent.", "bo.synthesis.", "bo.challenger.")):
            assert aid.endswith(run_id)


def test_clicking_specialist_returns_its_own_output_not_the_memo(bo_run):
    result, out = bo_run
    run_id = result.analysis_run_id
    info = inspect_node(h.CASE, f"bo.agent.m2_industry.{run_id}",
                        str(out), repo_root=str(out))
    assert info["status"] == "validated"
    payload = info["payload"]
    # The slice is the M2 agent's own record: its agent block + hypotheses only.
    assert payload["agent_id"] == "m2_industry"
    assert payload["agent"]["agent_id"] == "m2_industry"
    assert all(hhh["agent_id"] == "m2_industry"
               for hhh in payload["hypotheses"])
    # It is NOT the whole memo (no memo-level sections).
    assert "sections" not in payload


def test_clicking_challenger_returns_recorded_challenge(bo_run):
    result, out = bo_run
    run_id = result.analysis_run_id
    payload, _ = load_artifact(h.CASE, f"bo.challenger.{run_id}",
                               str(out), repo_root=str(out))
    assert "challenge" in payload
    assert "rounds" in payload["challenge"]


def test_missing_agent_artifact_shows_unavailable_reason(bo_run):
    result, out = bo_run
    m = _manifest(out)
    # A debt specialist node is declared in the graph but the recorded run did
    # not execute it (flag off) -> its node must be absent, not a false success.
    g = build_pipeline_graph(m)
    debt = next(n for n in g["nodes"] if n["id"] == "agent.m4_debt_structure")
    assert debt["status"] == ExecutionStatus.ABSENT.value


def test_per_agent_status_matches_recorded_validation(bo_run):
    result, out = bo_run
    m = _manifest(out)
    for a in m.artifacts:
        if a.artifact_type == ArtifactType.AGENT_OUTPUT:
            # Scripted happy-path run: every specialist validated.
            assert a.status == ExecutionStatus.VALIDATED
