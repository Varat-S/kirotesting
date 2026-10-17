"""Metrics for controlled M1S2 results produced by BusinessOverviewRunner."""

from __future__ import annotations

from typing import Any, Iterable

from app.services.bo.hybrid import InvestigationMode
from app.services.bo.single_specialist import SingleSpecialistResult


M3_RUNNER_EVALUATION_VERSION = "bo-m3-runner-evaluation-1"


def evaluate_runner_results(
    results: Iterable[SingleSpecialistResult], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Compare modes against a pre-registered scenario manifest."""
    arms = {}
    packet_hashes = set()
    evidence_sets = set()
    for result in results:
        packet_hashes.add(result.packet_hash)
        evidence_sets.add(tuple(result.metadata.get("packet_evidence_ids", [])))
        records = {record.hypothesis_id: record for record in result.records}
        id_arm = ("investigation" if result.mode is InvestigationMode.FULL
                  else "single_pass")
        scenarios = []
        for scenario_id, expected in manifest["scenarios"].items():
            hypothesis_id = expected["hypothesis_id"][id_arm]
            record = records.get(hypothesis_id)
            scenarios.append({
                "scenario": scenario_id,
                "kind": expected["kind"],
                "hypothesis_id": hypothesis_id,
                "present": record is not None,
                "usable": bool(record and record.usable),
                "disposition": record.disposition.value if record else None,
                "as_expected": bool(
                    record and record.disposition.value in expected["acceptable"]),
            })
        defects = [row for row in scenarios if row["kind"] == "defect"]
        controls = [row for row in scenarios if row["kind"] == "control"]
        usage = result.arm.usage()
        arms[result.mode.value] = {
            "analysis_run_id": result.analysis_run_id,
            "failure": result.arm.failure,
            "validation_valid": bool(result.arm.validation
                                     and result.arm.validation.valid),
            "complete": result.complete,
            "completeness_status": result.completeness_status,
            "agent6_eligible": result.agent6.eligible,
            "candidate_count": len(result.records),
            "usable_count": sum(record.usable for record in result.records),
            "selected_for_investigation": sum(
                bool(row.get("investigate"))
                for row in result.metadata.get("lineage", [])),
            "defects_held_back": sum(not row["usable"] for row in defects),
            "defects_total": len(defects),
            "controls_retained": sum(row["usable"] for row in controls),
            "controls_total": len(controls),
            "scenarios_as_expected": sum(row["as_expected"] for row in scenarios),
            "scenarios_total": len(scenarios),
            "usage": {
                **usage,
                "token_usage": (usage["input_tokens"] + usage["output_tokens"]
                                if usage["input_tokens"] or usage["output_tokens"]
                                else "not_measured"),
                "cost": "not_measured",
                "latency": "not_measured",
            },
            "scenarios": scenarios,
        }
    return {
        "evaluation_version": M3_RUNNER_EVALUATION_VERSION,
        "specialist": "M1S2",
        "same_evidence_input": len(evidence_sets) == 1,
        "run_bound_packet_hashes": sorted(packet_hashes),
        "arms": arms,
    }
