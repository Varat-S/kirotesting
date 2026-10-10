"""Evaluation methodology — four separate dimensions against a reviewer manifest.

The manifest is authored by a reviewer from the source passages; the checks are
concept- and grounding-level and never compare wording with a reference memo.
"""

from __future__ import annotations

import copy
import json

from app.services.bo.evaluation import evaluate_run, load_manifest
from tests.bo_helpers import (
    PERIOD,
    evidence_snapshot,
    good_script,
    hyp,
    metrics,
    run,
    synthetic_manifest,
)


def _evaluate(db_session, tmp_path, script=None, manifest=None):
    result, _ = run(db_session, tmp_path, script)
    return result, evaluate_run(manifest or synthetic_manifest(), memo=result.memo,
                                audit=result.audit, evidence=evidence_snapshot())


def test_dimensions_are_reported_separately_without_a_single_accuracy_number(
        db_session, tmp_path):
    _, report = _evaluate(db_session, tmp_path)
    assert {"factual_correctness", "analytical_reasoning", "completeness",
            "execution_reliability"} <= set(report)
    assert "accuracy" not in json.dumps(report).lower()
    factual = report["factual_correctness"]
    assert factual["citation_validity"] == 1.0 and factual["invalid_references"] == []
    assert factual["facts_located"] == 3 and factual["facts_covered"] == 3
    assert factual["recomputed_values"][0]["match"] is True
    reasoning = report["analytical_reasoning"]
    assert reasoning["false_hypotheses_avoided"] == 2
    assert reasoning["known_false_hypotheses"][0]["explicitly_rejected_by"] == [
        "BO1-H003"]
    assert reasoning["trap_checks_passed"] == 5
    assert reasoning["acceptable_inferences"][0]["found_in"] == ["BO1-H002"]
    completeness = report["completeness"]
    assert completeness["sections_missing"] == []
    assert completeness["concept_recall_in_memo"] == 1.0
    assert completeness["unresolved_question_recall"] == 1.0
    assert completeness["duplicate_memo_findings"] == 0
    reliability = report["execution_reliability"]
    assert reliability["agents_validated"] == 8 and reliability["ai_calls"] == 8
    assert reliability["validation_rejection_rate"] == 0.0
    assert reliability["token_usage"] == "not_reported"  # the fake backend has none


def test_offline_and_unverified_runs_are_labelled_as_not_proof(db_session, tmp_path):
    manifest = synthetic_manifest()
    manifest["verification_status"] = "unverified_pending_filing"
    _, report = _evaluate(db_session, tmp_path, manifest=manifest)
    assert report["execution_mode"] == "offline_simulation"
    assert "NOT been reviewer-verified" in report["interpretation"]
    assert "not a model" in report["interpretation"]


def test_admissions_share_presented_as_revenue_share_is_caught(db_session, tmp_path):
    script = good_script()
    script["bo_regulation_external"]["hypotheses"].append(hyp(
        "BO5-H004", "Texas generated 38% of revenue.", "fact", "negative",
        "supported", support=["admissions originated"],
        quotes=[("admissions originated",
                 "Approximately 38% of our admissions originated in the state of "
                 "Texas")]))
    _, report = _evaluate(db_session, tmp_path, script)
    trap = next(t for t in report["analytical_reasoning"]["trap_checks"]
                if t["id"] == "revenue_vs_admissions")
    assert not trap["passed"] and "BO5-H004" in trap["problems"][0]


def test_volume_guarantee_and_false_hypothesis_are_caught(db_session, tmp_path):
    script = good_script()
    script["bo_customers_contracts"]["hypotheses"][0]["claim"] = (
        "Managed-care contracts fix rates and guarantee minimum patient volumes.")
    script["bo_business_model_revenue"]["hypotheses"][2].update(
        disposition="qualified", supporting_evidence_ids=[
            {"$evidence": "Demand for acute care"}])
    _, report = _evaluate(db_session, tmp_path, script)
    reasoning = report["analytical_reasoning"]
    rate = next(t for t in reasoning["trap_checks"] if t["id"] == "rate_vs_volume")
    assert not rate["passed"]
    false = next(f for f in reasoning["known_false_hypotheses"] if f["id"] == "X1")
    assert not false["passed"] and "BO1-H003" in false["asserted_by"]


def test_missing_coverage_and_wrong_recomputed_value_are_reported(db_session, tmp_path):
    manifest = synthetic_manifest()
    manifest["recomputed_values"][0]["expected_value"] = 0.52
    manifest["expected_coverage"]["concepts"].append(
        {"id": "C9", "kind": "risk", "patterns": ["cybersecurity"]})
    manifest["source_confirmed_facts"].append(
        {"id": "F9", "concept": "not in filing", "verified_quote": "no such text"})
    _, report = _evaluate(db_session, tmp_path, manifest=manifest)
    assert report["factual_correctness"]["recomputed_values"][0]["match"] is False
    assert report["factual_correctness"]["facts_located"] == 3
    assert report["completeness"]["concept_recall_in_memo"] == 0.75


def test_manifest_file_round_trip_and_runner_writes_evaluation(db_session, tmp_path):
    from app.services.bo.offline import ScriptedBoBackend
    from app.services.bo.runner import BusinessOverviewRunner

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(synthetic_manifest()), encoding="utf-8")
    runner = BusinessOverviewRunner(
        db_session, backend=ScriptedBoBackend(copy.deepcopy(good_script())),
        output_root=tmp_path / "out")
    result = runner.run_prepared("NFH_BO", evidence_snapshot(), metrics(),
                                 manifest=load_manifest(path))
    written = json.loads(result.output_paths["evaluation.json"].read_text("utf-8"))
    assert written["ground_truth_status"] == "reviewer_verified"
    assert written["factual_correctness"]["recomputed_values"][0]["calculated"] == 0.3
    assert PERIOD in json.dumps(result.audit["calculations"])
