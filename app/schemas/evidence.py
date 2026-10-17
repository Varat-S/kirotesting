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
from enum import Enum

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
    xbrl_context_id: str | None = None
    inline_element_id: str | None = None
    presentation_role: str | None = None
    sec_accession: str | None = None


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
    xbrl_context_id: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    inline_element_id: str | None = None
    sec_accession: str | None = None
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


# ---------------------------------------------------------------------------
# Evidence retrieval (Business Overview Module 1, Stage 5 infrastructure).
#
# These models support SEARCHING the full narrative contents of ingested
# documents -- distinct from the pre-extracted financial facts above. A
# retrieved passage is SOURCE EVIDENCE, never a verified fact: retrieval
# relevance is NOT credibility, and a match does not prove a hypothesis. All
# additions are additive and reuse ``SourceRef`` / ``ExtractionMethod``.
# ---------------------------------------------------------------------------


class EvidenceSearchIntent(str, Enum):
    """Why a passage search is being run (recorded on every request/result).

    Intent is metadata that a later agent uses to interpret results; it does NOT
    change ranking determinism and never asserts that a match satisfies the
    intent (e.g. an OFFSETTING_CAPABILITY search returning a passage does not
    prove an offset exists or is effective).
    """

    SUPPORTING_EVIDENCE = "supporting_evidence"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    OFFSETTING_CAPABILITY = "offsetting_capability"
    GENERAL_INVESTIGATION = "general_investigation"


class EvidenceSearchOutcome(str, Enum):
    """Explicit, distinct outcomes of a single-query search (Req: no silent gaps).

    * ``matches_found``        -- >=1 passage scored above zero and is returned.
    * ``no_match_found``       -- the corpus was searchable but nothing matched
      (NOT proof that a capability/fact is absent).
    * ``source_unsearchable``  -- a permitted document exists but yielded no
      searchable passages (e.g. image-only / unreadable pages).
    * ``source_not_available`` -- a requested document is not in the corpus /
      not permitted (never fabricated to look available).
    """

    MATCHES_FOUND = "matches_found"
    NO_MATCH_FOUND = "no_match_found"
    SOURCE_UNSEARCHABLE = "source_unsearchable"
    SOURCE_NOT_AVAILABLE = "source_not_available"


class EvidencePassage(BaseModel):
    """A searchable slice of a document's narrative text, with provenance.

    The passage text is stored VERBATIM (never rewritten). PDF page numbering is
    kept separate from any printed/report page label; printed page numbers are
    only populated when actually present in the source and are never invented.
    """

    model_config = ConfigDict(extra="forbid")

    passage_id: str
    document_id: str
    document_hash: str | None = None
    # PDF page is OPTIONAL: an HTML / inline-XBRL filing has no PDF page and must
    # NOT have one fabricated (Task 11). ``section`` / ``source_location`` carry
    # the HTML-native location instead (e.g. "Item 1A", an element id).
    pdf_page: int | None = None
    printed_page_label: str | None = None
    section: str | None = None
    source_location: str | None = None
    text: str
    char_offset: int | None = None
    extraction_method: ExtractionMethod | None = None
    document_metadata: dict = Field(default_factory=dict)

    def source_ref(self) -> SourceRef:
        """A provenance reference into the originating document (page if any)."""
        return SourceRef(
            document_id=self.document_id,
            page=self.pdf_page,
            row_label=self.section,
        )


class EvidenceCorpus(BaseModel):
    """A reproducible, page-addressable set of passages across documents.

    Also records, per document, which pages were unreadable and whether the
    document produced any searchable passage, so search can report
    ``source_unsearchable`` deterministically rather than silently returning
    nothing.
    """

    model_config = ConfigDict(extra="forbid")

    passages: list[EvidencePassage] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    document_hashes: dict[str, str] = Field(default_factory=dict)
    unreadable_pages: dict[str, list[int]] = Field(default_factory=dict)

    def passages_for(self, document_id: str) -> list[EvidencePassage]:
        return [p for p in self.passages if p.document_id == document_id]


class EvidenceSearchRequest(BaseModel):
    """An explicit, reproducible evidence-search request.

    ``queries`` are free text (one or many). ``permitted_document_ids`` is the
    authoritative access scope: nothing outside it is ever searched. ``intent``
    and ``metadata_filters`` are recorded; the latter restricts results to
    passages whose ``document_metadata`` matches all supplied key/values.
    """

    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(min_length=1)
    permitted_document_ids: list[str] = Field(min_length=1)
    intent: EvidenceSearchIntent = EvidenceSearchIntent.GENERAL_INVESTIGATION
    max_results: int = Field(default=5, ge=1)
    metadata_filters: dict = Field(default_factory=dict)


class RankedPassage(BaseModel):
    """One ranked hit: the passage, its score and its deterministic rank."""

    model_config = ConfigDict(extra="forbid")

    rank: int
    score: float
    passage: EvidencePassage


class EvidenceSearchResult(BaseModel):
    """The outcome of ONE query within a request (reproducible + traceable)."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    query: str
    intent: EvidenceSearchIntent
    outcome: EvidenceSearchOutcome
    ranked_passages: list[RankedPassage] = Field(default_factory=list)
    searched_document_ids: list[str] = Field(default_factory=list)
    unavailable_document_ids: list[str] = Field(default_factory=list)
    retrieval_version: str
