"""Inspectable standalone Module 3 demo writer.

Writes the governed, byte-stable Module 3 artifacts for a single
:class:`~app.services.bo.m3_standalone.M3StandaloneResult` so an analyst or
auditor can inspect exactly what was produced:

* ``m3_corporate_structure.json`` — Deliverable A (de-wrapped v2.0 at root);
  skipped when unavailable (fail-closed, no diagram fabricated).
* ``m3_analysis.json``            — Deliverable B (governed analytical record).
* ``m3_structure_provenance.json``— the governed structure sidecar; skipped when
  unavailable.
* ``validation_report.json``      — Validator 7 issues + rejected items +
  per-finding dispositions + completion status.
* ``evaluation_scorecard.json``   — explicit-denominator metrics with a
  completeness caveat; the LLM semantic accuracy is reported ``not_measured``.
* ``run_manifest.json``           — the run manifest plus the EXACT reproduce
  command for the ``run-m3`` CLI.

All JSON is written with :func:`app.core.hashing.canonical_json` (sorted keys,
newline-terminated) so repeated runs over the same inputs are byte-identical.
Nothing here calls a model; it only serialises an already-governed result.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from app.core.hashing import canonical_json, content_hash
from app.services.bo import m3_semantic_eval


MANAGEMENT_BENCHMARK_DIR = Path("examples/bo_m3/management_benchmark")
MANAGEMENT_BENCHMARK_CASES = MANAGEMENT_BENCHMARK_DIR / "cases.json"


def _write(path: Path, payload: Any) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")


# -- corporate-structure accuracy --------------------------------------------


def _count_simplified_tree(doc: dict[str, Any]) -> dict[str, int]:
    """Entity / ownership-edge / guarantor counts in a simplified v1.0 tree."""
    entities = 0
    edges = 0
    guarantors = 0

    def walk(node: dict[str, Any], *, is_root: bool) -> None:
        nonlocal entities, edges, guarantors
        entities += 1
        if node.get("is_guarantor"):
            guarantors += 1
        if not is_root:
            edges += 1
        for child in node.get("children") or []:
            walk(child, is_root=False)

    tree = doc.get("corporate_tree")
    if isinstance(tree, dict):
        walk(tree, is_root=True)
    return {"entities": entities, "relationships": edges, "guarantors": guarantors}


def _count_refined(structure_export: dict[str, Any] | None) -> dict[str, int]:
    """Node / ownership-link / guarantor counts in a de-wrapped v2.0 export."""
    if not structure_export:
        return {"entities": 0, "relationships": 0, "guarantors": 0}
    companies = structure_export.get("companies") or {}
    nodes = 0
    links = 0
    guarantors = 0
    for company in companies.values():
        if not isinstance(company, dict):
            continue
        company_nodes = company.get("nodes") or []
        nodes += len(company_nodes)
        links += len(company.get("ownership_links") or [])
        guarantors += sum(1 for n in company_nodes if n.get("is_guarantor"))
    return {"entities": nodes, "relationships": links, "guarantors": guarantors}


def _structure_scorecard(
    result,
    simplified_document: dict[str, Any] | None,
    reference_output: dict[str, Any] | None,
) -> dict[str, Any]:
    export = result.m3_corporate_structure
    refined_counts = _count_refined(export)
    sidecar = result.m3_structure_sidecar or {}
    roundtrip = (sidecar.get("roundtrip_reconciliation")
                 if "roundtrip_reconciliation" in sidecar else None)

    checks: list[dict[str, Any]] = []
    # Against the simplified onboarding input (the ingested contract).
    if simplified_document is not None:
        expected_in = _count_simplified_tree(simplified_document)
        for field in ("entities", "relationships", "guarantors"):
            checks.append({
                "population": f"vs_simplified_input.{field}",
                "expected": expected_in[field],
                "actual": refined_counts[field],
                "match": expected_in[field] == refined_counts[field],
            })
    # Against the reference final-output contract (if supplied).
    if reference_output is not None:
        expected_ref = _count_refined(reference_output)
        for field in ("entities", "relationships", "guarantors"):
            checks.append({
                "population": f"vs_reference_output.{field}",
                "expected": expected_ref[field],
                "actual": refined_counts[field],
                "match": expected_ref[field] == refined_counts[field],
            })

    denominator = len(checks)
    numerator = sum(1 for c in checks if c["match"])
    return {
        "corporate_structure_status": result.corporate_structure_status,
        "refined_counts": refined_counts,
        "count_checks": checks,
        "count_accuracy": {"numerator": numerator, "denominator": denominator},
        "roundtrip_consistent": (roundtrip or {}).get("consistent"),
        # Determinism hash over the de-wrapped export (None when unavailable):
        # re-running the standalone path over identical inputs must reproduce it.
        "determinism_hash": (content_hash(export) if export is not None else None),
        "note": ("Deterministic structural counts only; does not assess analytical "
                 "or narrative quality."),
    }


# -- management analytical quality -------------------------------------------


def _management_scorecard() -> dict[str, Any]:
    """Run the frozen management benchmark through the semantic-entailment eval.

    The benchmark (``examples/bo_m3/management_benchmark/cases.json``) carries a
    SEPARATE credibility label and credit_direction label per case. The semantic
    evaluator scores the credibility (entailment) contract; it is adapted to the
    evaluator's dataset shape in memory. The benchmark passages are verbatim
    admitted filing excerpts, but the evaluation uses scripted expected labels
    and therefore reports ``actual_llm_semantic_accuracy = not_measured``.
    """
    if not MANAGEMENT_BENCHMARK_CASES.is_file():
        return {
            "status": "unavailable",
            "reason": "Management benchmark cases.json is not present.",
            "actual_llm_semantic_accuracy": "not_measured",
        }
    benchmark = json.loads(MANAGEMENT_BENCHMARK_CASES.read_text(encoding="utf-8"))
    # Adapt to the m3_semantic_eval dataset contract (one credibility label per
    # case). The separate credit_direction label is carried through for audit.
    adapted = {
        "dataset_id": benchmark["dataset_id"],
        "cases": [{
            "id": case["id"],
            "category": case.get("dimension") or "management_claim",
            "evidence_id": case["evidence_id"],
            "passage": case["passage"],
            "claim": case["claim"],
            "expected": case["credibility"]["expected"],
            "reason": case["credibility"].get("reason", ""),
            "checks": case.get("checks", []),
            "credit_direction": case["credit_direction"]["label"],
        } for case in benchmark["cases"]],
    }
    tmp = Path(tempfile.mkdtemp(prefix="m3_mgmt_eval_")) / "adapted_cases.json"
    try:
        tmp.write_text(json.dumps(adapted, sort_keys=True), encoding="utf-8")
        report = m3_semantic_eval.evaluate(tmp)
    finally:
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass
    # Credit-direction distribution (descriptive; separate from credibility).
    directions: dict[str, int] = {}
    for case in benchmark["cases"]:
        label = case["credit_direction"]["label"]
        directions[label] = directions.get(label, 0) + 1
    report["benchmark_grounding"] = benchmark.get("grounding", "unknown")
    report["benchmark_label_status"] = benchmark.get("label_status")
    report["credit_direction_distribution"] = directions
    report["credible_adverse_cases"] = sorted(
        c["id"] for c in benchmark["cases"] if c.get("credible_adverse"))
    report["unsupported_causal_attribution_cases"] = sorted(
        c["id"] for c in benchmark["cases"]
        if c.get("unsupported_causal_attribution"))
    return report


def build_evaluation_scorecard(
    result,
    *,
    simplified_document: dict[str, Any] | None = None,
    reference_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Explicit-denominator scorecard with a completeness caveat."""
    return {
        "scorecard_kind": "bo_m3_standalone_evaluation",
        "case_id": result.case_id,
        "analysis_run_id": result.analysis_run_id,
        "actual_llm_semantic_accuracy": "not_measured",
        "corporate_structure_accuracy": _structure_scorecard(
            result, simplified_document, reference_output),
        "management_analytical_quality": _management_scorecard(),
        "completion_status": result.completion_status,
        "requires_human_review": True,
        "forced_outcome": False,
        "completeness_caveat": (
            "This scorecard reports deterministic structural counts and a scripted "
            "semantic-entailment contract over a frozen, developer-authored "
            "benchmark whose labels await external review. A passing scripted set "
            "is NOT proof of real-world correctness; the actual model's semantic "
            "accuracy is not measured here, and this evaluation is neither perfect "
            "nor complete. Every finding requires human credit-analyst review."),
    }


# -- validation report --------------------------------------------------------


def _finding_dispositions(result) -> list[dict[str, Any]]:
    governed = result.governed_assessment or {}
    rows: list[dict[str, Any]] = []
    for item in governed.get("management_assessments") or []:
        rows.append({
            "kind": "management_assessment",
            "id": item.get("observation_id"),
            "disposition": item.get("disposition"),
            "credit_direction": item.get("credit_direction"),
            "credibility_vs_direction_note": item.get("credibility_vs_direction_note"),
            "admissibility": item.get("admissibility"),
        })
    for item in governed.get("governance_screening") or []:
        rows.append({
            "kind": "governance_screening",
            "id": item.get("finding_id"),
            "disposition": item.get("disposition"),
            "admissibility": item.get("admissibility"),
        })
    return rows


def build_validation_report(result) -> dict[str, Any]:
    governed = result.governed_assessment or {}
    return {
        "report_kind": "bo_m3_validator_7_report",
        "case_id": result.case_id,
        "analysis_run_id": result.analysis_run_id,
        "validator_id": governed.get("validator_id", "validator_7"),
        "specialist_validated": result.specialist_validated,
        "specialist_failure": result.specialist_failure,
        "m3_assessment_present": result.governed_assessment is not None,
        "validation_issues": result.validation_issues,
        "rejected_items": result.rejected_items,
        "finding_dispositions": _finding_dispositions(result),
        "corporate_structure_status": result.corporate_structure_status,
        "completion_status": result.completion_status,
        "requires_human_review": True,
        "forced_outcome": False,
    }


# -- reproduce command + manifest --------------------------------------------


def _reproduce_command(result, *, corporate_structure_path: str | None,
                       responses_path: str | None, output_dir: str) -> str:
    parts = ["python -m app.cli", "--output", output_dir, "run-m3", result.case_id]
    if corporate_structure_path:
        parts += ["--corporate-structure", corporate_structure_path]
    if responses_path:
        parts += ["--responses", responses_path]
    return "DEBUG=false " + " ".join(parts)


def write_m3_demo(
    result,
    output_dir: str | Path,
    *,
    simplified_document: dict[str, Any] | None = None,
    reference_output: dict[str, Any] | None = None,
    corporate_structure_path: str | None = None,
    responses_path: str | None = None,
) -> dict[str, Path]:
    """Write the six inspectable standalone Module 3 files; return their paths.

    ``m3_corporate_structure.json`` and ``m3_structure_provenance.json`` are
    skipped (fail-closed) when the corporate structure is unavailable.
    """
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    if result.m3_corporate_structure is not None:
        paths["m3_corporate_structure"] = folder / "m3_corporate_structure.json"
        _write(paths["m3_corporate_structure"], result.m3_corporate_structure)
        if result.m3_structure_sidecar is not None:
            paths["m3_structure_provenance"] = folder / "m3_structure_provenance.json"
            _write(paths["m3_structure_provenance"], result.m3_structure_sidecar)

    paths["m3_analysis"] = folder / "m3_analysis.json"
    _write(paths["m3_analysis"], result.m3_analysis)

    paths["validation_report"] = folder / "validation_report.json"
    _write(paths["validation_report"], build_validation_report(result))

    paths["evaluation_scorecard"] = folder / "evaluation_scorecard.json"
    _write(paths["evaluation_scorecard"], build_evaluation_scorecard(
        result, simplified_document=simplified_document,
        reference_output=reference_output))

    reproduce = _reproduce_command(
        result, corporate_structure_path=corporate_structure_path,
        responses_path=responses_path, output_dir=str(output_dir))
    paths["run_manifest"] = folder / "run_manifest.json"
    _write(paths["run_manifest"], {**result.run_manifest,
                                   "reproduce_command": reproduce})
    return paths
