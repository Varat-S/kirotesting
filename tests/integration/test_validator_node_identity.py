"""PR #3 Task 1: each validator node resolves to its OWN recorded output.

The Strategy Validator 8 node previously rendered unrelated parameters with
``agent_id: null`` because the slice used ``entry.producer_agent`` (None) instead
of the agent id in the audit subpath. These tests pin that each validator node
(strategy V8, debt V8, Validator 7) resolves to its own record within the
selected run, and that an absent strategy validator reports absence rather than
substituting another producer's data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.artifact_manifest import ExecutionStatus
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from app.services.registry import build_manifest, build_pipeline_graph
from app.services.registry.explorer import inspect_node
from tests import bo_helpers as h
from tests.integration.test_bo_m4_dual_specialist import _debt_evidence, _script_with_debt


@pytest.fixture()
def dual_m4_run(db_session, tmp_path):
    out = tmp_path / "out"
    result = BusinessOverviewRunner(
        db_session, backend=ScriptedBoBackend(_script_with_debt()), bo_roster="v3",
        data_root=tmp_path / "d", output_root=out,
        enable_m4_debt_specialist=True).run_prepared(
            h.CASE, _debt_evidence(), h.metrics(), write_outputs=True)
    db_session.commit()
    return result, out


def test_strategy_v8_shows_its_own_validation_not_null_agent_params(dual_m4_run):
    result, out = dual_m4_run
    run_id = result.analysis_run_id
    info = inspect_node(h.CASE, f"bo.validator8_strategy.{run_id}",
                        str(out), repo_root=str(out))
    payload = info["payload"]
    # The slice is the m4_strategy_capital agent's VALIDATION record.
    assert payload["agent_id"] == "m4_strategy_capital"
    assert payload["view"] == "validation"
    assert payload["validator_id"] == "validator_8"
    assert "validation" in payload and payload["validation"]["agent_id"] == "m4_strategy_capital"
    # The old bug: unrelated hypotheses/parameters (agent_id: null) leaked in.
    assert "parameters" not in payload
    assert "hypotheses" not in payload
    # And no null-agent data anywhere in the slice.
    assert "null" not in str(payload.get("parameters", ""))


def test_strategy_and_debt_v8_are_distinct(dual_m4_run):
    result, out = dual_m4_run
    run_id = result.analysis_run_id
    strat = inspect_node(h.CASE, f"bo.validator8_strategy.{run_id}",
                         str(out), repo_root=str(out))["payload"]
    debt = inspect_node(h.CASE, f"bo.validator8_debt.{run_id}",
                        str(out), repo_root=str(out))["payload"]
    assert strat["validator_id"] == "validator_8"
    # Debt V8 is its own deterministic six-check report, not the strategy record.
    assert debt.get("passed") is True
    assert len(debt.get("checks", [])) == 6
    assert strat != debt


def test_each_validator_node_maps_within_the_same_run(dual_m4_run):
    result, out = dual_m4_run
    run_id = result.analysis_run_id
    g = build_pipeline_graph(build_manifest(str(out), h.CASE, repo_root=str(out)))
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["validator.v8_strategy"]["artifact_id"] == f"bo.validator8_strategy.{run_id}"
    assert by_id["validator.v8_debt"]["artifact_id"] == f"bo.validator8_debt.{run_id}"
    # No validator node points at another run or another producer's artifact.
    for nid in ("validator.v8_strategy", "validator.v8_debt", "validator.v7"):
        aid = by_id[nid]["artifact_id"] or ""
        assert aid == "" or aid.endswith(run_id)


def test_strategy_v8_absent_when_agent_did_not_run(db_session, tmp_path):
    # A v2 roster run has no m4_strategy_capital agent -> the strategy V8 node is
    # absent, never substituted with another artifact.
    from tests.integration.test_bo_cli_workbench import _script as v2_script
    import json as _json
    backend = ScriptedBoBackend(_json.loads(Path(v2_script(tmp_path)).read_text()))
    out = tmp_path / "out"
    result = BusinessOverviewRunner(
        db_session, backend=backend, bo_roster="v2",
        data_root=tmp_path / "d", output_root=out).run_prepared(
            h.CASE, h.evidence_snapshot(), h.metrics(), write_outputs=True)
    db_session.commit()
    m = build_manifest(str(out), h.CASE, repo_root=str(out))
    run_id = result.analysis_run_id
    assert m.by_id(f"bo.validator8_strategy.{run_id}") is None
    g = build_pipeline_graph(m)
    strat = next(n for n in g["nodes"] if n["id"] == "validator.v8_strategy")
    assert strat["status"] == ExecutionStatus.ABSENT.value
