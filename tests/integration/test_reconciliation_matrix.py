"""Zero-safe reconciliation MATRIX + snapshot tests (task 4.5).

Covers the full matrix from tasks.md 4.5: matching, conflicting, 0-vs-0,
0-vs-small, near-zero denominator, negatives, scale mismatch, adjusted-vs-GAAP,
restated-vs-original, wrong-entity, wrong-period; plus missing != zero and
snapshot validation. Requirements: 7.2, 7.3, 7.6, 23.3.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import ReconciliationRecord
from app.schemas.enums import EntityType, ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, EntityRecord, SourceRef
from app.services.audit.log import AuditLog, EventType
from app.services.reconciliation.service import (
    ComparisonMethod,
    HumanCorrectionWorkflow,
    Reconciler,
    ToleranceConfig,
    deduplicate_exact,
)
from app.services.reconciliation.snapshot import SnapshotAssembler


def _fact(
    fact_id: str,
    value: float | None,
    *,
    name: str = "revenue",
    method: ExtractionMethod = ExtractionMethod.XBRL,
    status: FactStatus = FactStatus.UNVERIFIED,
    scale: str = "millions",
    normalized_unit: str = "USD_million",
    accounting_basis: str = "GAAP",
    restated: bool = False,
    entity_id: str = "DAL",
    fiscal_year: int = 2024,
    doc: str | None = None,
) -> CanonicalFact:
    kwargs: dict = dict(
        fact_id=fact_id,
        name=name,
        currency="USD",
        scale=scale,
        normalized_unit=normalized_unit,
        period_start=date(fiscal_year, 1, 1),
        period_end=date(fiscal_year, 12, 31),
        period_type="duration",
        fiscal_year=fiscal_year,
        accounting_basis=accounting_basis,
        consolidation_scope="consolidated",
        entity_id=entity_id,
        restated=restated,
        status=status,
        extraction_method=method,
        source_refs=[SourceRef(document_id=doc or f"doc-{fact_id}")],
    )
    if value is not None:
        kwargs["raw_value"] = str(value)
        kwargs["raw_unit"] = normalized_unit
        kwargs["normalized_value"] = value
    return CanonicalFact(**kwargs)


@pytest.fixture()
def reconciler(db_session: Session) -> Reconciler:
    registry = ConfigRegistry(db_session)
    tol = ToleranceConfig.from_registry(registry)
    audit = AuditLog(db_session)
    return Reconciler(tol, session=db_session, audit=audit, case_id="DAL_2024")


# --- MATRIX ---------------------------------------------------------------


def test_matching_values_verify_relative(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0)
    b = _fact("b", 1002.0, method=ExtractionMethod.PDF_TEXT)  # 0.2% < 0.5%
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.RELATIVE
    assert result.resolved_state is FactStatus.VERIFIED


def test_conflicting_values_outside_tolerance(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0)
    b = _fact("b", 1200.0, method=ExtractionMethod.PDF_TEXT)  # 20% > 0.5%
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.RELATIVE
    assert result.resolved_state is FactStatus.CONFLICTING
    # No silent merge: both values + both refs preserved (Req 7.10).
    assert sorted(result.values) == [1000.0, 1200.0]
    assert len(result.source_refs) == 2


def test_zero_vs_zero_is_exact(reconciler: Reconciler) -> None:
    a = _fact("a", 0.0)
    b = _fact("b", 0.0, method=ExtractionMethod.PDF_TEXT)
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.EXACT
    assert result.resolved_state is FactStatus.VERIFIED
    assert result.absolute_delta == 0.0
    # Never divided by zero.
    assert result.relative_delta is None


def test_zero_vs_small_uses_absolute_no_division(reconciler: Reconciler) -> None:
    """0-vs-small must NOT divide; it uses the absolute tolerance band."""
    a = _fact("a", 0.0)
    b = _fact("b", 0.5, method=ExtractionMethod.PDF_TEXT)  # within abs 1.0
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.ABSOLUTE
    assert result.relative_delta is None
    assert result.resolved_state is FactStatus.VERIFIED


def test_near_zero_denominator_falls_back_to_absolute(reconciler: Reconciler) -> None:
    """Denominator at/below the near-zero floor never divides (Req 7.2)."""
    a = _fact("a", 0.4)
    b = _fact("b", 0.9, method=ExtractionMethod.PDF_TEXT)
    result = reconciler.reconcile_pair("revenue", a, b)
    # max magnitude 0.9 <= floor 1.0 -> absolute path.
    assert result.comparison_method is ComparisonMethod.ABSOLUTE
    assert result.relative_delta is None


def test_negative_values_use_magnitude(reconciler: Reconciler) -> None:
    a = _fact("a", -1000.0)
    b = _fact("b", -1003.0, method=ExtractionMethod.PDF_TEXT)  # 0.3% < 0.5%
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.RELATIVE
    assert result.resolved_state is FactStatus.VERIFIED
    assert result.absolute_delta == pytest.approx(3.0)


def test_scale_mismatch_is_definition_mismatch(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0, scale="millions", normalized_unit="USD_million")
    b = _fact("b", 1000.0, scale="thousands", normalized_unit="USD_thousand")
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert result.resolved_state is FactStatus.CONFLICTING
    assert "unit_scale" in result.mismatch_dimensions


def test_adjusted_vs_gaap_definition_mismatch(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0, accounting_basis="GAAP")
    b = _fact("b", 1000.0, accounting_basis="management_adjusted")
    result = reconciler.reconcile_pair("ebitda", a, b)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert "accounting_definition" in result.mismatch_dimensions


def test_restated_vs_original_definition_mismatch(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0, restated=False)
    b = _fact("b", 1000.0, restated=True)
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert "restatement" in result.mismatch_dimensions


def test_wrong_entity_definition_mismatch(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0, entity_id="DAL")
    b = _fact("b", 1000.0, entity_id="UAL")
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert "entity" in result.mismatch_dimensions


def test_wrong_period_definition_mismatch(reconciler: Reconciler) -> None:
    a = _fact("a", 1000.0, fiscal_year=2024)
    b = _fact("b", 1000.0, fiscal_year=2023)
    result = reconciler.reconcile_pair("revenue", a, b)
    assert result.comparison_method is ComparisonMethod.DEFINITION_MISMATCH
    assert "period" in result.mismatch_dimensions


def test_missing_is_not_zero(reconciler: Reconciler) -> None:
    """A field with no value facts resolves to missing, never numeric zero."""
    result = reconciler.reconcile_field("cash", [])
    assert result.resolved_state is FactStatus.MISSING
    assert result.values == []


def test_single_source_is_unverified(reconciler: Reconciler) -> None:
    result = reconciler.reconcile_field("revenue", [_fact("a", 1000.0)])
    assert result.resolved_state is FactStatus.UNVERIFIED


def test_exact_duplicates_are_deduplicated() -> None:
    a = _fact("a", 1000.0, doc="doc-shared")
    dup = _fact("a", 1000.0, doc="doc-shared")  # same signature incl. refs
    unique = deduplicate_exact([a, dup])
    assert len(unique) == 1


def test_non_numeric_contradiction_record(reconciler: Reconciler) -> None:
    a = CanonicalFact(
        fact_id="a", name="auditor", raw_value="EY", status=FactStatus.UNVERIFIED,
        entity_id="DAL", source_refs=[SourceRef(document_id="d1")],
    )
    b = CanonicalFact(
        fact_id="b", name="auditor", raw_value="PwC", status=FactStatus.UNVERIFIED,
        entity_id="DAL", source_refs=[SourceRef(document_id="d2")],
    )
    result = reconciler.contradiction("auditor", a, b)
    assert result.record_kind == "non_numeric"
    assert result.resolved_state is FactStatus.CONFLICTING
    assert len(result.source_refs) == 2


# --- persistence + audit --------------------------------------------------


def test_persist_conflict_emits_audit_event(
    db_session: Session, reconciler: Reconciler
) -> None:
    a = _fact("a", 1000.0)
    b = _fact("b", 1200.0, method=ExtractionMethod.PDF_TEXT)
    result = reconciler.reconcile_pair("revenue", a, b)
    row = reconciler.persist(result)
    assert isinstance(row, ReconciliationRecord)
    assert row.resolved_state == "conflicting"
    events = AuditLog(db_session).events_for_case("DAL_2024")
    assert any(e.event_type == EventType.FACT_CONFLICT_DETECTED.value for e in events)


def test_persist_verified_emits_fact_verified(
    db_session: Session, reconciler: Reconciler
) -> None:
    a = _fact("a", 1000.0)
    b = _fact("b", 1001.0, method=ExtractionMethod.PDF_TEXT)
    reconciler.persist(reconciler.reconcile_pair("revenue", a, b))
    events = AuditLog(db_session).events_for_case("DAL_2024")
    assert any(e.event_type == EventType.FACT_VERIFIED.value for e in events)


def test_tolerance_version_recorded(reconciler: Reconciler) -> None:
    """Req 7.4 / 19.6: the record carries the tolerance_version used."""
    result = reconciler.reconcile_pair(
        "revenue", _fact("a", 1000.0), _fact("b", 1200.0, method=ExtractionMethod.PDF_TEXT)
    )
    assert result.tolerance_version == 1


# --- human correction (task 4.3) ------------------------------------------


def test_human_correction_is_non_destructive(
    db_session: Session, reconciler: Reconciler
) -> None:
    a = _fact("a", 1000.0)
    b = _fact("b", 1200.0, method=ExtractionMethod.PDF_TEXT)
    row = reconciler.persist(reconciler.reconcile_pair("revenue", a, b))

    audit = AuditLog(db_session)
    workflow = HumanCorrectionWorkflow(db_session, audit=audit)
    correction = workflow.correct(
        row.id,
        corrected_by="analyst@bank",
        after_state={"accepted_value": 1000.0, "accepted_source": "a"},
        reason="XBRL figure confirmed against primary filing.",
        case_id="DAL_2024",
    )

    # Original conflict row is intact (not deleted/overwritten) -- Req 7.11.
    reloaded = db_session.get(ReconciliationRecord, row.id)
    assert reloaded is not None
    assert reloaded.resolved_state == "conflicting"
    # Both before and after states are recoverable -- Req 7.12 / 15.3.
    assert correction.before_state["resolved_state"] == "conflicting"
    assert correction.after_state["accepted_value"] == 1000.0
    # fact_human_corrected emitted -- Req 15.2.
    events = audit.events_for_case("DAL_2024")
    assert any(e.event_type == EventType.FACT_HUMAN_CORRECTED.value for e in events)
    # History is queryable.
    assert len(workflow.history(row.id)) == 1


# --- snapshot assembly (task 4.4) -----------------------------------------


def test_snapshot_assembles_versions_and_validates(db_session: Session) -> None:
    registry = ConfigRegistry(db_session)
    tol = ToleranceConfig.from_registry(registry)  # registers tolerances v1
    registry.register("source_profiles", {"filing": "critical"})
    reconciler = Reconciler(tol, session=db_session, case_id="DAL_2024")

    verified = reconciler.reconcile_pair(
        "revenue", _fact("a", 1000.0), _fact("b", 1001.0, method=ExtractionMethod.PDF_TEXT)
    )
    conflict = reconciler.reconcile_pair(
        "ebitda",
        _fact("c", 1000.0, name="ebitda", accounting_basis="GAAP"),
        _fact("d", 1000.0, name="ebitda", accounting_basis="management_adjusted"),
    )
    missing = reconciler.reconcile_field("cash", [])

    assembler = SnapshotAssembler(db_session, registry)
    entity = EntityRecord(entity_id="DAL", legal_name="Delta Air Lines", entity_type=EntityType.BORROWER)
    snapshot = assembler.assemble(
        case_id="DAL_2024",
        facts=[_fact("a", 1000.0)],
        reconciliations=[verified, conflict, missing],
        entities=[entity],
        as_of_date=date(2024, 12, 31),
        evidence_cutoff_timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
    )

    # Records config_versions via the registry map (Req 19.6).
    assert snapshot.config_versions["tolerances"] == 1
    assert snapshot.config_versions["source_profiles"] == 1
    # Distinct per-field states, missing != zero (Req 5.4).
    assert snapshot.data_quality["revenue"].state == "verified"
    assert snapshot.data_quality["ebitda"].state == "conflicting"
    assert snapshot.data_quality["cash"].state == "missing"
    assert snapshot.data_quality["cash"].value is None
    # Conflict preserves both values in the snapshot.
    assert snapshot.data_quality["ebitda"].mismatch_dimensions == ["accounting_definition"]

    row = assembler.persist(snapshot)
    assert row.snapshot_type == "canonical_evidence"
    assert row.snapshot_version == 1

    # Downstream references by version; a second assembly increments version.
    snapshot2 = assembler.assemble(
        case_id="DAL_2024", facts=[], reconciliations=[verified]
    )
    assert snapshot2.snapshot_version == 2
