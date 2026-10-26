"""Controlled M1S2 execution through BusinessOverviewRunner (offline only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.orm import (
    AgenticAnalysisRun,
    AuditEvent,
    EvidencePacketRow,
    HypothesisRecordRow,
)
from app.services.bo.hybrid import InvestigationMode
from app.services.bo.m3_runner_evaluation import evaluate_runner_results
from app.services.bo.runner import BusinessOverviewRunner
from app.services.bo.sectors import load_case_checks
from app.services.bo.single_specialist import agent6_eligibility
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline


HCA_DIR = Path("examples/healthcare/hca-2025")
SCRIPTS = Path("examples/bo_m1s2/hca_m1s2")
pytestmark = pytest.mark.skipif(
    not (HCA_DIR / "filing/bundle.json").exists(), reason="HCA filing unavailable")


@pytest.fixture()
def prepared(db_session, tmp_path):
    return CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).prepare_case("HCA_2025_M2", package=SourcePackage.load(HCA_DIR / "package.json"))


def _responses():
    return {
        "single_pass": json.loads((SCRIPTS / "single_pass.json").read_text()),
        "investigation": json.loads((SCRIPTS / "investigation.json").read_text()),
    }


@pytest.mark.parametrize("mode", list(InvestigationMode))
def test_m1s2_all_modes_run_through_runner_and_persist(db_session, tmp_path,
                                                        prepared, mode):
    result = BusinessOverviewRunner(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).run_m1s2_mode(
        "HCA_2025_M2", prepared=prepared, mode=mode, responses=_responses(),
        coverage_checks=load_case_checks(HCA_DIR / "coverage_checks.json"))

    row = db_session.get(AgenticAnalysisRun, result.analysis_run_id)
    assert row.model_configuration["execution_mode"] == mode.value
    assert row.agent_registry_version == "agent-registry-3.0.0"
    assert row.router_hash == result.packet_hash
    packet = db_session.query(EvidencePacketRow).filter_by(
        analysis_run_id=result.analysis_run_id).one()
    assert packet.packet_hash == result.packet_hash
    assert packet.payload["packet_hash"] == result.packet_hash
    assert packet.evidence_ids == packet.payload["evidence_ids"]
    assert packet.evidence_ids
    assert db_session.query(HypothesisRecordRow).filter_by(
        analysis_run_id=result.analysis_run_id).count() == len(result.records)
    event = db_session.query(AuditEvent).filter_by(
        event_type="bo_m1s2_mode_executed").order_by(AuditEvent.timestamp.desc()).first()
    assert event.after["packet_hash"] == result.packet_hash
    assert event.after["execution_mode"] == mode.value
    assert event.after["usage"]["cost"] == "not_measured"
    assert event.after["usage"]["latency"] == "not_measured"
    # HCA's mandatory checklist is not fully answered by the scripted fixture.
    assert not result.complete and not result.agent6.eligible
    assert result.agent6_payload() == []


def test_hybrid_records_selection_lineage_and_no_silent_fallback(db_session, tmp_path,
                                                                  prepared):
    scripts = _responses()
    for enquiry in scripts["investigation"]["plan"]["enquiries"]:
        enquiry["permitted_document_ids"] = ["not_admitted"]
    result = BusinessOverviewRunner(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).run_m1s2_mode(
        "HCA_2025_M2", prepared=prepared,
        mode=InvestigationMode.HYBRID, responses=scripts, coverage_checks=[])
    assert result.arm.failure
    assert result.metadata["complete"] is False
    assert result.metadata["lineage"]
    selected = {row["hypothesis_id"] for row in result.metadata["lineage"]
                if row["investigate"]}
    assert selected
    for record in result.records:
        if record.hypothesis_id in selected:
            assert not record.usable
            assert record.investigation["status"] == "incomplete"
    assert result.agent6_payload() == []


def test_agent6_interface_allows_only_complete_validated_usable_records():
    class Record:
        hypothesis_id = "M1S2-H001"
        usable = True

    allowed = agent6_eligibility(records=[Record()], validation_valid=True,
                                 complete=True, failure=None)
    blocked = agent6_eligibility(records=[Record()], validation_valid=True,
                                 complete=False, failure=None)
    assert allowed.eligible and allowed.hypothesis_ids == ("M1S2-H001",)
    assert not blocked.eligible and blocked.hypothesis_ids == ()


def test_actual_runner_three_way_comparison(db_session, tmp_path, prepared):
    runner = BusinessOverviewRunner(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out")
    results = [runner.run_m1s2_mode(
        "HCA_2025_M2", prepared=prepared, mode=mode, responses=_responses(),
        coverage_checks=load_case_checks(HCA_DIR / "coverage_checks.json"))
        for mode in InvestigationMode]
    manifest = json.loads((SCRIPTS / "manifest.json").read_text())
    report = evaluate_runner_results(results, manifest)

    # Packets are sealed per run (their payload contains the run id), while all
    # three arms receive the identical admitted evidence set.
    assert report["same_evidence_input"]
    assert len(report["run_bound_packet_hashes"]) == 3
    assert set(report["arms"]) == {mode.value for mode in InvestigationMode}
    assert report["arms"]["single_pass"]["usage"]["model_calls"] == 1
    assert report["arms"]["full_investigation"]["usage"]["model_calls"] == 4
    assert report["arms"]["hybrid_selective"]["usage"]["model_calls"] == 4
    assert report["arms"]["hybrid_selective"]["selected_for_investigation"] == 7
    assert report["arms"]["single_pass"]["defects_held_back"] == 2
    assert report["arms"]["full_investigation"]["defects_held_back"] == 5
    assert report["arms"]["hybrid_selective"]["defects_held_back"] == 4
    assert all(arm["usage"]["cost"] == "not_measured"
               for arm in report["arms"].values())
    assert all(not arm["agent6_eligible"] for arm in report["arms"].values())
