"""Assemble + version the CanonicalEvidenceSnapshot (task 4.4).

The :class:`CanonicalEvidenceSnapshot` is the mid-pipeline evidence interface
(Req 5.1). This module assembles it from documents, entities, canonical facts,
and reconciliation results, records which ``config_versions`` it was built under
(Req 19.6 via :meth:`ConfigRegistry.config_versions_map`), validates it against
the versioned JSON Schema on write (Req 5.5), and persists a versioned snapshot
row that downstream objects reference by version (Req 5.6).

Assembly is deterministic: the same facts + reconciliation results + config
versions produce the same snapshot payload.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import Snapshot
from app.schemas.evidence import CanonicalFact, EntityRecord
from app.schemas.json_schema import validate_snapshot
from app.schemas.snapshots import (
    SCHEMA_VERSION,
    CanonicalEvidenceSnapshot,
    DataQualityState,
)
from app.services.reconciliation.service import ReconciliationResult


class SnapshotAssembler:
    """Assemble, validate, version and persist a CanonicalEvidenceSnapshot."""

    # Config kinds relevant to the evidence stage; recorded on the snapshot so
    # downstream reproduction knows exactly which versions were in force.
    EVIDENCE_CONFIG_KINDS = ("tolerances", "parser_precedence", "source_profiles")

    def __init__(self, session: Session, registry: ConfigRegistry) -> None:
        self._session = session
        self._registry = registry

    def assemble(
        self,
        *,
        case_id: str,
        facts: list[CanonicalFact],
        reconciliations: list[ReconciliationResult],
        documents: list[dict] | None = None,
        entities: list[EntityRecord] | None = None,
        financials: dict | None = None,
        as_of_date: date | None = None,
        evidence_cutoff_timestamp: datetime | None = None,
        config_kinds: tuple[str, ...] | None = None,
    ) -> CanonicalEvidenceSnapshot:
        """Build an (unpersisted) snapshot and validate it on write (Req 5.5).

        The per-field ``data_quality`` map is derived from the reconciliation
        results so each field carries value, distinct state, and provenance
        (Req 5.2, 5.4); conflicts retain both values + both refs (Req 7.10).
        """
        kinds = config_kinds if config_kinds is not None else self.EVIDENCE_CONFIG_KINDS
        config_versions = self._registry.config_versions_map(kinds)

        data_quality: dict[str, DataQualityState] = {}
        for result in reconciliations:
            data_quality[result.field] = _to_data_quality(result)

        snapshot = CanonicalEvidenceSnapshot(
            schema_version=SCHEMA_VERSION,
            snapshot_version=self._next_version(case_id),
            case_id=case_id,
            as_of_date=as_of_date,
            evidence_cutoff_timestamp=evidence_cutoff_timestamp,
            config_versions=config_versions,
            documents=list(documents or []),
            entities=[e.model_dump(mode="json") for e in (entities or [])],
            facts=[f.model_dump(mode="json") for f in facts],
            financials=financials or {},
            provenance={"fact_count": len(facts), "field_count": len(data_quality)},
            data_quality=data_quality,
        )

        # Validate-on-write against the versioned JSON Schema (Req 5.5).
        validate_snapshot(snapshot)
        return snapshot

    def persist(self, snapshot: CanonicalEvidenceSnapshot) -> Snapshot:
        """Validate again then persist the snapshot row (downstream refs by version)."""
        payload = validate_snapshot(snapshot)
        row = Snapshot(
            case_id=snapshot.case_id,
            snapshot_type=snapshot.snapshot_type,
            snapshot_version=snapshot.snapshot_version,
            schema_version=snapshot.schema_version,
            finalized=False,
            config_versions=snapshot.config_versions,
            payload=payload,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _next_version(self, case_id: str) -> int:
        stmt = (
            select(Snapshot.snapshot_version)
            .where(
                Snapshot.case_id == case_id,
                Snapshot.snapshot_type == "canonical_evidence",
            )
            .order_by(Snapshot.snapshot_version.desc())
            .limit(1)
        )
        latest = self._session.execute(stmt).scalar_one_or_none()
        return 1 if latest is None else latest + 1


def _to_data_quality(result: ReconciliationResult) -> DataQualityState:
    """Project a reconciliation result into a snapshot data-quality field."""
    value: float | None = None
    if result.resolved_state.value in {"verified", "unverified"} and result.values:
        value = result.values[0]
    return DataQualityState(
        field=result.field,
        state=result.resolved_state.value,
        value=value,
        values=list(result.values),
        source_refs=list(result.source_refs),
        comparison_method=result.comparison_method.value,
        absolute_delta=result.absolute_delta,
        relative_delta=result.relative_delta,
        near_zero_floor=result.near_zero_floor,
        tolerance=result.tolerance,
        tolerance_version=result.tolerance_version,
        mismatch_dimensions=list(result.mismatch_dimensions),
        detail=result.detail,
    )
