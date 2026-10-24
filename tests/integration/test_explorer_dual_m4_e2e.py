"""End-to-end: a dual-M4 BO run is indexed and inspectable in the Pipeline Explorer.

Runs a full offline v3 BO run with the debt specialist enabled, writes its
artifacts, then asserts the registry/Explorer index the M4 debt assessment, the
debt Validator 8 and the strategy Validator 8 as per-run, inspectable artifacts
whose graph nodes resolve within the same run. Deterministic; no paid model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.artifact_manifest import ExecutionStatus
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from app.services.registry import build_manifest, build_pipeline_graph
from app.services.registry.explorer import inspect_node, load_artifact
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


def test_explorer_indexes_debt_assessment_and_validator(dual_m4_run):
    result, out = dual_m4_run
    run_id = result.analysis_run_id
    m = build_manifest(str(out), h.CASE, repo_root=str(out))
    ids = {a.artifact_id for a in m.artifacts}
    assert f"bo.m4_debt.{run_id}" in ids
    assert f"bo.validator8_debt.{run_id}" in ids
    assert f"bo.validator8_strategy.{run_id}" in ids

    debt = m.by_id(f"bo.m4_debt.{run_id}")
    assert debt.status == ExecutionStatus.VALIDATED
    assert debt.producer_agent == "module4_debt_structure_specialist"
    v8 = m.by_id(f"bo.validator8_debt.{run_id}")
    assert v8.status == ExecutionStatus.VALIDATED
    assert v8.producer_validator == "validator_8_debt_structure"


def test_graph_nodes_resolve_to_dual_m4_artifacts_same_run(dual_m4_run):
    result, out = dual_m4_run
    run_id = result.analysis_run_id
    g = build_pipeline_graph(build_manifest(str(out), h.CASE, repo_root=str(out)))
    debt_node = next(n for n in g["nodes"] if n["id"] == "agent.m4_debt_structure")
    v8_debt = next(n for n in g["nodes"] if n["id"] == "validator.v8_debt")
    v8_strat = next(n for n in g["nodes"] if n["id"] == "validator.v8_strategy")
    assert debt_node["artifact_id"] == f"bo.m4_debt.{run_id}"
    assert debt_node["status"] == "validated"
    assert v8_debt["artifact_id"] == f"bo.validator8_debt.{run_id}"
    assert v8_strat["artifact_id"] == f"bo.validator8_strategy.{run_id}"
    # Both strategy and debt capabilities present, distinct, same run.
    assert v8_debt["artifact_id"] != v8_strat["artifact_id"]


def test_clicking_debt_node_returns_its_own_assessment(dual_m4_run):
    result, out = dual_m4_run
    run_id = result.analysis_run_id
    info = inspect_node(h.CASE, f"bo.m4_debt.{run_id}", str(out), repo_root=str(out))
    payload = info["payload"]
    # The slice is the debt assessment itself (four deliverables + validator), not
    # the whole memo.
    assert payload["agent_id"] == "module4_debt_structure_specialist"
    assert payload["assessment"]["deliverable_1"]["senior_notes_schedule"]
    assert payload["validator_report"]["passed"] is True
    assert "module_conclusions" not in payload


def test_debt_validator_failure_is_indexed_as_failed(db_session, tmp_path):
    import copy
    from tests.fixtures.hca_module4 import hca_module4_output

    broken = copy.deepcopy(hca_module4_output())
    broken["deliverable_1"]["senior_notes_schedule"][0]["principal_amount_m"] += 5000.0
    out = tmp_path / "out"
    result = BusinessOverviewRunner(
        db_session, backend=ScriptedBoBackend(_script_with_debt(debt_output=broken)),
        bo_roster="v3", data_root=tmp_path / "d", output_root=out,
        enable_m4_debt_specialist=True).run_prepared(
            h.CASE, _debt_evidence(), h.metrics(), write_outputs=True)
    db_session.commit()
    run_id = result.analysis_run_id
    m = build_manifest(str(out), h.CASE, repo_root=str(out))
    debt = m.by_id(f"bo.m4_debt.{run_id}")
    v8 = m.by_id(f"bo.validator8_debt.{run_id}")
    assert debt.status == ExecutionStatus.FAILED
    assert v8.status == ExecutionStatus.FAILED
    # The mandatory escalation travels on the indexed debt artifact.
    assert debt.escalations
