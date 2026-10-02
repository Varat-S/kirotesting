"""Snapshot skeleton + validate-on-write tests (Req 5.1, 5.5, 16.1)."""

from __future__ import annotations

import pytest

from app.schemas.json_schema import SchemaValidationError, validate_snapshot
from app.schemas.snapshots import (
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
    assert ces.schema_version == fcs.schema_version == SCHEMA_VERSION


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
