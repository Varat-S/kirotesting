"""Snapshot skeleton + validate-on-write tests (Req 5.1, 5.5, 16.1)."""

from __future__ import annotations

import pytest

from app.schemas.json_schema import SchemaValidationError, validate_snapshot
from app.schemas.snapshots import (
    FINAL_SCHEMA_VERSION,
    SCHEMA_VERSION,
    CanonicalEvidenceSnapshot,
    EvidenceSnapshotRef,
    FinalCaseSnapshot,
)


def test_two_distinct_snapshot_types() -> None:
    ces = CanonicalEvidenceSnapshot(case_id="DAL_2024")
    fcs = FinalCaseSnapshot(
        evidence_snapshot_ref=EvidenceSnapshotRef(case_id="DAL_2024", snapshot_version=1)
    )
    assert ces.snapshot_type == "canonical_evidence"
    assert fcs.snapshot_type == "final_case"
    # The canonical-evidence boundary is unchanged (1.1); the FinalCaseSnapshot
    # gains additive agentic fields at 1.2 (Milestone 1.3).
    assert ces.schema_version == SCHEMA_VERSION
    assert fcs.schema_version == FINAL_SCHEMA_VERSION


def test_snapshots_carry_schema_and_config_versions() -> None:
    ces = CanonicalEvidenceSnapshot(
        case_id="DAL_2024", config_versions={"tolerances": 1, "policy": 2}
    )
    assert ces.schema_version == SCHEMA_VERSION
    assert ces.config_versions == {"tolerances": 1, "policy": 2}


def test_final_snapshot_references_evidence_snapshot_version() -> None:
    """Req 16.1: FinalCaseSnapshot references a CanonicalEvidenceSnapshot."""
    fcs = FinalCaseSnapshot(
        evidence_snapshot_ref=EvidenceSnapshotRef(case_id="DAL_2024", snapshot_version=3)
    )
    assert fcs.evidence_snapshot_ref.snapshot_version == 3
    assert fcs.finalized is False


def test_validate_on_write_accepts_valid_snapshots() -> None:
    ces = CanonicalEvidenceSnapshot(case_id="DAL_2024")
    fcs = FinalCaseSnapshot(
        evidence_snapshot_ref=EvidenceSnapshotRef(case_id="DAL_2024", snapshot_version=1)
    )
    assert validate_snapshot(ces)["snapshot_type"] == "canonical_evidence"
    assert validate_snapshot(fcs)["snapshot_type"] == "final_case"


def test_validate_on_write_rejects_bad_payload() -> None:
    with pytest.raises(SchemaValidationError):
        validate_snapshot({"schema_version": SCHEMA_VERSION})  # no snapshot_type

    with pytest.raises(SchemaValidationError):
        # Wrong type for config_versions.
        validate_snapshot(
            {
                "snapshot_type": "canonical_evidence",
                "schema_version": SCHEMA_VERSION,
                "snapshot_version": 1,
                "case_id": "DAL_2024",
                "config_versions": "not-an-object",
            }
        )


# --- Milestone 1.3: FinalCaseSnapshot v1.2 additive agentic fields -----------


def test_legacy_v1_1_final_payload_still_validates() -> None:
    """A baseline (legacy) final payload with no agentic fields validates under
    its own 1.1 schema key — backward compatibility is preserved."""
    legacy = {
        "schema_version": "1.1",
        "snapshot_type": "final_case",
        "snapshot_version": 1,
        "evidence_snapshot_ref": {"case_id": "DAL_2024", "snapshot_version": 1},
    }
    assert validate_snapshot(legacy)["schema_version"] == "1.1"


def test_final_snapshot_defaults_to_v1_2_without_agentic_fields() -> None:
    fcs = FinalCaseSnapshot(
        evidence_snapshot_ref=EvidenceSnapshotRef(case_id="DAL_2024", snapshot_version=1)
    )
    assert fcs.schema_version == FINAL_SCHEMA_VERSION
    # New fields are optional and default-empty.
    assert fcs.accepted_analysis_run_id is None
    assert fcs.scores == {}
    assert fcs.accepted_parameter_result_ids == []
    assert validate_snapshot(fcs)["snapshot_type"] == "final_case"


def test_final_snapshot_carries_accepted_agentic_run() -> None:
    fcs = FinalCaseSnapshot(
        evidence_snapshot_ref=EvidenceSnapshotRef(case_id="DAL_2024", snapshot_version=2),
        analysis_mode="agentic",
        accepted_analysis_run_id="AR1",
        accepted_parameter_result_ids=["PR1", "PR2"],
        accepted_score_ids=["S_business", "S_financial", "S_obligor"],
        selected_candidate_id=None,
        router_version="router-1",
        agent_registry_version="reg-1",
        agent_registry_hash="rh",
        scoring_config_version=1,
        scoring_config_hash="sh",
    )
    data = validate_snapshot(fcs)
    assert data["accepted_analysis_run_id"] == "AR1"
    assert data["analysis_mode"] == "agentic"
    assert data["schema_version"] == FINAL_SCHEMA_VERSION
