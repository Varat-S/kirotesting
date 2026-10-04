"""Pydantic schemas for the core evidence model (Milestone 1).

Covers SourceRef, EntityRecord, and ExtractedFact / CanonicalFact with the
enriched financial metadata schema (Req 2.1, 2.5, 3.5, 4.1, 4.2, 4.4, 5.4).

Key invariants enforced here:

* Every factual field carries at least one source reference (Req 3.5).
* ``missing`` is never numerical zero and non-value statuses carry no value
  (Req 4.4 / 5.4).
* Raw value/unit are preserved separately from normalized value/unit (Req 4.4).
* Qualitative facts need not populate every financial field (Req 4.2).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import (
    NON_VALUE_STATUSES,
    EntityType,
    ExtractionMethod,
    FactStatus,
)


class SourceRef(BaseModel):
    """Provenance reference into a document (Req 3.5 / design SourceRef).

    At least one locating field beyond ``document_id`` is expected in practice;
    ``document_id`` itself is always required so a ref is never anonymous.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str
    page: int | None = None
    table: str | None = None
    taxonomy_concept: str | None = None
    row_label: str | None = None
    cell: str | None = None


class EntityRecord(BaseModel):
    """EntityRecord (Req 2.1, 2.5).

    The legal borrower is preserved distinct from its consolidated/ultimate
    parent: a borrower sets ``borrower_flag=True`` and references the parent via
    ``parent_entity_id`` (a separate record). An entity cannot be its own
    parent.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    legal_name: str
    aliases: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    entity_type: EntityType
    parent_entity_id: str | None = None
    borrower_flag: bool = False
    guarantor_flag: bool = False
    expected_consolidation_scope: str | None = None
    jurisdiction: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_own_parent(self) -> "EntityRecord":
        if (
            self.parent_entity_id is not None
            and self.parent_entity_id == self.entity_id
        ):
            raise ValueError("An entity cannot be its own parent_entity_id.")
        return self


class CanonicalFact(BaseModel):
    """ExtractedFact / CanonicalFact with enriched metadata (Req 4.1).

    Numeric value fields are optional. A fact whose ``status`` is a non-value
    status (``missing``/``not_applicable``/``not_disclosed``) MUST NOT carry a
    normalized or raw value -- in particular ``missing`` is never ``0``
    (Req 4.4 / 5.4). Every fact must carry >=1 ``source_refs`` entry (Req 3.5).
    """

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    name: str
    original_name: str | None = None
    mapping_version: int | None = None
    mapping_hash: str | None = None
    mapping_status: str | None = None
    mapping_candidates: list[str] = Field(default_factory=list)

    # Raw vs normalized kept separate (Req 4.4).
    raw_value: str | None = None
    raw_unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None

    currency: str | None = None
    scale: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    period_type: str | None = None
    fiscal_year: int | None = None
    accounting_basis: str | None = None
    consolidation_scope: str | None = None
    entity_id: str | None = None
    reporting_entity_name: str | None = None
    restated: bool = False
    taxonomy_concept: str | None = None
    source_label: str | None = None
    data_freshness: datetime | None = None
    normalization_method: str | None = None
    definition_version: str | None = None

    status: FactStatus
    extraction_method: ExtractionMethod | None = None
    confidence: float | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    created_by: str | None = None

    @model_validator(mode="after")
    def _validate_provenance_and_status(self) -> "CanonicalFact":
        # Req 3.5: every factual field carries at least one source reference.
        if not self.source_refs:
            raise ValueError(
                f"Fact {self.fact_id!r} must carry at least one source reference."
            )
        # Req 4.4 / 5.4: missing is never zero; non-value statuses carry no value.
        if self.status in NON_VALUE_STATUSES:
            if self.normalized_value is not None or self.raw_value is not None:
                raise ValueError(
                    f"Fact {self.fact_id!r} has status {self.status.value!r} and must "
                    "not carry a numeric value ('missing' is never zero)."
                )
        return self

    @property
    def is_missing(self) -> bool:
        """True when the fact is explicitly missing (distinct from a zero value)."""
        return self.status is FactStatus.MISSING
