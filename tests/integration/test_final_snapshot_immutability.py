"""Integration tests for FinalCaseSnapshot finalization + immutability.

Covers task 7.3 / Req 16.2, 16.3, 23.1:

* sign-off GATES finalization,
* finalization is blocked while mandatory escalations are unresolved,
* a finalized snapshot is structurally immutable,
* a later change creates a NEW version referencing its predecessor while the
  original stays byte-for-byte reproducible.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.core.hashing import content_hash
from app.models.orm import AuditEvent, Case, Snapshot
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.review.finalization import (
    FinalSnapshotAssembler,
    SnapshotImmutabilityError,
)
from app.services.review.workflow import HumanReviewWorkflow, SignOffRequiredError


def _seed_case_and_evidence(session: Session) -> ConfigRegistry:
    """Create a case + a persisted CanonicalEvidenceSnapshot v1."""
    session.add(Case(case_id="DAL_2024"))
    session.flush()

    registry = ConfigRegistry(session)
    registry.register("tolerances", {"revenue": 0.005})
    registry.register("policy", {"net_debt_to_ebitda": 3.5})
    registry.register("source_profiles", {"filing": "critical"})

    assembler = SnapshotAssembler(session, registry)
    entity = EntityRecord(
        entity_id="DAL", legal_name="Delta", entity_type=EntityType.BORROWER
    )
    snapshot = assembler.assemble(
        case_id="DAL_2024",
        facts=[],
        reconciliations=[],
        entities=[entity],
        as_of_date=date(2024, 12, 31),
        evidence_cutoff_timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
    )
    assembler.persist(snapshot)
    return registry


def test_finalization_blocked_without_sign_off(db_session: Session) -> None:
    """Req 15.6 / 16: a recommendation cannot be finalized without sign-off."""
    registry = _seed_case_and_evidence(db_session)
    audit = AuditLog(db_session)
    final = FinalSnapshotAssembler(db_session, registry, audit=audit)

    snapshot = final.assemble(case_id="DAL_2024", evidence_snapshot_version=1)

    with pytest.raises(SignOffRequiredError):
        final.finalize(snapshot, signed_off_by="officer", has_sign_off=False)

    # Nothing was persisted as finalized.
    rows = (
        db_session.query(Snapshot)
        .filter(Snapshot.snapshot_type == "final_case")
        .all()
    )
    assert rows == []


def test_finalization_blocked_while_mandatory_escalation_unresolved(
    db_session: Session,
) -> None:
    """Req 14.7 / 15.5: unresolved mandatory escalations block finalization."""
    registry = _seed_case_and_evidence(db_session)
    audit = AuditLog(db_session)
    engine = EscalationEngine(db_session, audit=audit, case_id="DAL_2024")
    engine.raise_escalation(
        rule_id="R-POLICY-LEV-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        reason="Leverage above illustrative policy limit.",
    )

    final = FinalSnapshotAssembler(db_session, registry, audit=audit)
    snapshot = final.assemble(case_id="DAL_2024", evidence_snapshot_version=1)

    # Even WITH sign-off, an unresolved mandatory escalation blocks finalization.
    with pytest.raises(SignOffRequiredError):
        final.finalize(snapshot, signed_off_by="officer", has_sign_off=True)

    # The unresolved exception is surfaced on the assembled snapshot (Req 15.5).
    assert snapshot.exceptions and snapshot.exceptions[0]["rule_id"] == "R-POLICY-LEV-01"


def test_finalize_records_versions_and_emits_event(db_session: Session) -> None:
    """Req 16.1: references evidence by version + records config/version linkage."""
    registry = _seed_case_and_evidence(db_session)
    audit = AuditLog(db_session)
    wf = HumanReviewWorkflow(db_session, audit=audit, case_id="DAL_2024")
    wf.sign_off_recommendation(reviewer="credit.officer")

    final = FinalSnapshotAssembler(db_session, registry, audit=audit)
    snapshot = final.assemble(
        case_id="DAL_2024",
        evidence_snapshot_version=1,
        metric_definition_versions={"net_debt_to_ebitda": 1},
        rule_versions={"R-POLICY-LEV-01": 1},
        prompt_model_versions={"business_analysis": "v1.0"},
    )
    row = final.finalize(
        snapshot, signed_off_by="credit.officer", has_sign_off=wf.has_sign_off()
    )

    assert row.finalized is True
    assert row.snapshot_version == 1
    # Evidence referenced BY VERSION.
    assert row.payload["evidence_snapshot_ref"] == {
        "case_id": "DAL_2024",
        "snapshot_version": 1,
    }
    # Config + metric-def + rule + prompt-model versions recorded.
    assert row.payload["config_versions"]["policy"] == 1
    assert row.payload["metric_definition_versions"] == {"net_debt_to_ebitda": 1}
    assert row.payload["rule_versions"] == {"R-POLICY-LEV-01": 1}
    assert row.payload["prompt_model_versions"] == {"business_analysis": "v1.0"}
    assert row.payload["recommendation"]["status"] == "final"
    # Frozen content hash matches the stored payload (reproducible).
    assert row.content_hash == content_hash(row.payload)
    assert final.verify_integrity(row) is True

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.event_type == "snapshot_finalized")
        .all()
    )
    assert len(events) == 1


def test_finalized_snapshot_is_immutable(db_session: Session) -> None:
    """Req 16.2: a finalized snapshot cannot be mutated or deleted."""
    registry = _seed_case_and_evidence(db_session)
    audit = AuditLog(db_session)
    final = FinalSnapshotAssembler(db_session, registry, audit=audit)
    snapshot = final.assemble(case_id="DAL_2024", evidence_snapshot_version=1)
    row = final.finalize(snapshot, signed_off_by="officer", has_sign_off=True)
    db_session.commit()
    original_hash = row.content_hash

    # Attempt to tamper with the finalized payload.
    row.payload = {**row.payload, "recommendation": {"status": "tampered"}}
    with pytest.raises(SnapshotImmutabilityError):
        db_session.flush()
    db_session.rollback()

    # The original is unchanged / reproducible after rollback.
    reloaded = final.get_version("DAL_2024", 1)
    assert reloaded.content_hash == original_hash
    assert content_hash(reloaded.payload) == original_hash
    assert reloaded.payload["recommendation"]["status"] == "final"


def test_finalized_snapshot_cannot_be_deleted(db_session: Session) -> None:
    """Req 16.2: a finalized snapshot cannot be deleted."""
    registry = _seed_case_and_evidence(db_session)
    final = FinalSnapshotAssembler(db_session, registry)
    snapshot = final.assemble(case_id="DAL_2024", evidence_snapshot_version=1)
    row = final.finalize(snapshot, signed_off_by="officer", has_sign_off=True)
    db_session.commit()

    db_session.delete(row)
    with pytest.raises(SnapshotImmutabilityError):
        db_session.flush()
    db_session.rollback()
    assert final.get_version("DAL_2024", 1) is not None


def test_change_creates_new_version_referencing_predecessor(
    db_session: Session,
) -> None:
    """Req 16.3: a later change supersedes; original stays reproducible."""
    registry = _seed_case_and_evidence(db_session)
    audit = AuditLog(db_session)
    final = FinalSnapshotAssembler(db_session, registry, audit=audit)

    v1_snapshot = final.assemble(
        case_id="DAL_2024",
        evidence_snapshot_version=1,
        recommendation={"status": "draft", "rating": "BB"},
    )
    v1 = final.finalize(v1_snapshot, signed_off_by="officer", has_sign_off=True)
    v1_hash = v1.content_hash

    # A later change creates a NEW version that references the predecessor.
    v2 = final.supersede(
        v1,
        signed_off_by="officer",
        has_sign_off=True,
        recommendation={"status": "final", "rating": "BB-"},
    )

    assert v2.snapshot_version == 2
    assert v2.payload["supersedes_snapshot"] == 1
    assert v2.supersedes == v1.id
    assert v2.payload["recommendation"]["rating"] == "BB-"

    # The original v1 is untouched and still reproducible.
    reloaded_v1 = final.get_version("DAL_2024", 1)
    assert reloaded_v1.content_hash == v1_hash
    assert content_hash(reloaded_v1.payload) == v1_hash
    assert reloaded_v1.payload["recommendation"]["rating"] == "BB"


def test_assembly_is_deterministic(db_session: Session) -> None:
    """Req 16: same evidence + versions + reviews => equivalent snapshot."""
    registry = _seed_case_and_evidence(db_session)
    final = FinalSnapshotAssembler(db_session, registry)
    a = final.assemble(
        case_id="DAL_2024",
        evidence_snapshot_version=1,
        metrics={"leverage": 3.1},
    )
    b = final.assemble(
        case_id="DAL_2024",
        evidence_snapshot_version=1,
        metrics={"leverage": 3.1},
    )
    # snapshot_version differs by design (next version); content otherwise equal.
    a_payload = a.model_dump(mode="json")
    b_payload = b.model_dump(mode="json")
    a_payload.pop("snapshot_version")
    b_payload.pop("snapshot_version")
    assert content_hash(a_payload) == content_hash(b_payload)


def test_assemble_requires_existing_evidence_version(db_session: Session) -> None:
    """A FinalCaseSnapshot must reference an existing evidence snapshot version."""
    registry = _seed_case_and_evidence(db_session)
    final = FinalSnapshotAssembler(db_session, registry)
    with pytest.raises(ValueError):
        final.assemble(case_id="DAL_2024", evidence_snapshot_version=99)
