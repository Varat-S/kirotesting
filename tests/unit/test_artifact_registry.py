"""Targeted tests for the artifact manifest / registry and its graph + rendering.

Covers the integration deliverables:
* artifact manifest generation and integrity (content hashes, status taxonomy),
* graph node-to-artifact mappings from declared dependencies,
* missing and failed artifact handling (no fabricated successes),
* generic JSON rendering and deterministic PDF export.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.hashing import content_hash
from app.schemas.artifact_manifest import (
    ARTIFACT_MANIFEST_VERSION,
    ArtifactManifest,
    ExecutionStatus,
)
from app.services.registry import build_manifest, build_pipeline_graph
from app.services.registry.graph import PIPELINE_GRAPH_VERSION
from app.services.registry.json_render import render_json_html, render_json_pdf

DEMO = Path("examples/bo_m3/structural_credit/demo_output/hca_simulated")


def _manifest() -> ArtifactManifest:
    return build_manifest(
        "examples/bo_m3/structural_credit/demo_output",
        "hca_simulated",
        m3_dirs=[str(DEMO)],
        repo_root=".",
    )


# -- manifest generation & integrity -----------------------------------------

def test_manifest_indexes_recorded_m3_artifacts():
    m = _manifest()
    assert m.manifest_version == ARTIFACT_MANIFEST_VERSION
    ids = {a.artifact_id for a in m.artifacts}
    assert {"m3.corporate_structure", "m3.analysis", "m3.validator_7",
            "m3.evaluation", "m3.run_manifest"} <= ids


def test_manifest_content_hash_matches_canonical_payload():
    m = _manifest()
    entry = m.by_id("m3.analysis")
    assert entry is not None and entry.file_location
    payload = json.loads(Path(entry.file_location).read_text(encoding="utf-8"))
    assert entry.content_hash == content_hash(payload)


def test_manifest_does_not_rewrite_source_files():
    before = (DEMO / "m3_analysis.json").read_bytes()
    _manifest()
    after = (DEMO / "m3_analysis.json").read_bytes()
    assert before == after  # read-only indexing never mutates artifacts


def test_status_taxonomy_distinguishes_validated_and_qualified():
    m = _manifest()
    struct = m.by_id("m3.corporate_structure")
    analysis = m.by_id("m3.analysis")
    # The corporate structure is available; the analysis for this fixture
    # requires human review -> qualified (not silently promoted to validated).
    assert struct.status == ExecutionStatus.VALIDATED
    assert analysis.status == ExecutionStatus.QUALIFIED
    assert analysis.human_review_status.value == "pending"


# -- missing / failed artifact handling ---------------------------------------

def test_absent_producer_is_represented_not_dropped(tmp_path):
    # A BO run dir that exists but has no files -> entries present but ABSENT.
    run = tmp_path / "bo" / "CASE_X" / "runA"
    run.mkdir(parents=True)
    m = build_manifest(tmp_path, "CASE_X")
    memo = m.by_id("bo.memo.runA")
    assert memo is not None
    assert memo.status == ExecutionStatus.ABSENT
    assert memo.content_hash is None
    assert "no recorded artifact" in (memo.status_reason or "")


def test_failed_completion_maps_to_failed(tmp_path):
    run = tmp_path / "bo" / "CASE_Y" / "runB"
    run.mkdir(parents=True)
    (run / "business_overview.json").write_text(
        json.dumps({"completion_status": "failed"}), encoding="utf-8")
    m = build_manifest(tmp_path, "CASE_Y")
    memo = m.by_id("bo.memo.runB")
    assert memo.status == ExecutionStatus.FAILED


def test_unreadable_artifact_is_error_not_success(tmp_path):
    run = tmp_path / "fo" / "CASE_Z" / "runC"
    run.mkdir(parents=True)
    (run / "financial_overview.json").write_text("{not valid json", encoding="utf-8")
    m = build_manifest(tmp_path, "CASE_Z")
    fo = m.by_id("fo.overview.runC")
    assert fo.status == ExecutionStatus.ERROR
    assert not fo.is_successful()


def test_counts_by_status_and_successful_filter():
    m = _manifest()
    counts = m.counts_by_status()
    assert sum(counts.values()) == len(m.artifacts)
    # qualified counts as successful-with-caveat; absent does not.
    assert all(a.is_successful() for a in m.successful())


# -- graph node-to-artifact mappings ------------------------------------------

def test_graph_renders_without_a_run():
    g = build_pipeline_graph(None)
    assert g["graph_version"] == PIPELINE_GRAPH_VERSION
    assert len(g["nodes"]) > 0 and len(g["edges"]) > 0
    # Illustration mode: every node absent, nothing claimed successful.
    assert all(n["status"] == "absent" for n in g["nodes"])


def test_graph_has_required_stage_nodes():
    g = build_pipeline_graph(None)
    ids = {n["id"] for n in g["nodes"]}
    assert {"raw_input", "canonical_snapshot", "synthesis_v1", "challenger",
            "synthesis_v2", "governance"} <= ids
    # Six Module 1 agents incl. Agent 6 synthesis.
    m1 = [n for n in g["nodes"] if n["group"] == "M1"]
    assert len(m1) == 6
    # Two Module 4 specialists under one module group.
    m4 = [n for n in g["nodes"] if n["group"] == "M4"]
    assert {"agent.m4_strategy_capital", "agent.m4_debt_structure"} <= {n["id"] for n in m4}
    # FO branches alongside BO.
    assert any(n["group"] == "FO" for n in g["nodes"])


def test_graph_edges_are_left_to_right_by_layer():
    g = build_pipeline_graph(None)
    layer = {n["id"]: n["layer"] for n in g["nodes"]}
    for edge in g["edges"]:
        if edge["source"] in layer and edge["target"] in layer:
            # Every declared edge flows forward (or within) the layering.
            assert layer[edge["source"]] <= layer[edge["target"]]


def test_graph_picks_up_recorded_status():
    g = build_pipeline_graph(_manifest())
    m3 = next(n for n in g["nodes"] if n["id"] == "agent.m3_corporate_management")
    v7 = next(n for n in g["nodes"] if n["id"] == "validator.v7")
    assert m3["status"] in {"validated", "qualified"}
    assert v7["status"] in {"validated", "qualified"}


# -- generic JSON rendering & PDF export --------------------------------------

def test_render_json_html_handles_arbitrary_shapes():
    html = render_json_html(
        {"scalar": 1, "list": [{"a": 1, "b": 2}, {"a": 3, "b": 4}],
         "nested": {"deep": {"flag": True, "none": None}}},
        title="X",
    )
    assert "jv-table" in html          # uniform list -> table
    assert "<details" in html          # nested -> collapsible
    assert "null" in html              # None rendered, not crashed


def test_render_json_html_escapes_injection():
    html = render_json_html({"x": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pdf_export_is_deterministic_and_valid():
    payload = json.loads((DEMO / "m3_analysis.json").read_text(encoding="utf-8"))
    a = render_json_pdf(payload, title="M3", subtitle="s")
    b = render_json_pdf(payload, title="M3", subtitle="s")
    assert a[:4] == b"%PDF"
    assert a == b                      # byte-identical: deterministic


def test_pdf_export_handles_wide_lists_without_overflow():
    wide = {"rows": [{f"c{i}": i for i in range(30)} for _ in range(3)]}
    pdf = render_json_pdf(wide, title="Wide")
    assert pdf[:4] == b"%PDF"          # falls back to per-item sections


def test_pdf_known_schema_vs_generic():
    known = json.loads((DEMO / "m3_analysis.json").read_text(encoding="utf-8"))
    assert known.get("schema_version") == "bo-m3-analysis-1.0"
    assert render_json_pdf(known, title="known")[:4] == b"%PDF"
    assert render_json_pdf({"anything": [1, 2, 3]}, title="generic")[:4] == b"%PDF"


# -- Module 4 dual-specialist reconciliation ----------------------------------

def test_module4_both_specialists_preserved():
    """Both M4 capabilities survive integration under one governed module.

    Strategy/capital-allocation (roster v3) and Ernest's debt-structure
    specialist must both be importable and distinct — neither silently replaced.
    """
    from app.services.bo.roster_v3 import M4_STRATEGY_CAPITAL
    from app.services.modules.module4 import MODULE4_AGENT_NAME

    assert M4_STRATEGY_CAPITAL.agent_id == "m4_strategy_capital"
    assert M4_STRATEGY_CAPITAL.module == "M4"
    assert MODULE4_AGENT_NAME  # debt-structure specialist prompt is registered
    assert M4_STRATEGY_CAPITAL.agent_id != MODULE4_AGENT_NAME


def test_graph_shows_both_module4_specialists_and_their_validators():
    g = build_pipeline_graph(None)
    ids = {n["id"] for n in g["nodes"]}
    assert {"agent.m4_strategy_capital", "agent.m4_debt_structure"} <= ids
    # Distinct Validator-8 nodes for the two capabilities (no silent collapse).
    assert {"validator.v8_strategy", "validator.v8_debt"} <= ids


def test_module4_validator8_mandatory_escalation_rule_exists():
    """The debt-structure Validator 8 failure raises a MANDATORY escalation."""
    from app.services.modules.module4.specialist import RULE_VALIDATOR8_FAILURE

    assert RULE_VALIDATOR8_FAILURE == "R-MODULE4-VALIDATOR8-01"
