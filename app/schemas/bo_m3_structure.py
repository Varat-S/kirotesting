"""Additive, versioned contract for the Module 3 corporate-structure deliverable.

This schema is independent of (and additive to) ``app.schemas.bo_m3`` which
governs the narrative-driven legal-entity / management assessment. It models the
*onboarding corporate-structure JSON* (``schema_version`` "2.0",
``artifact_type`` "llm_reproducible_corporate_structure_diagrams"), the result of
validating it against the single-company contract, and the deterministic diagram
artifact produced from validated values only.

Design rules honoured here (see the Credit Memo North Star and the Module 3
spec):

* Evidence first / no fabrication: ownership percentages live *only* on
  ``ownership_links``; guarantor shields *only* where ``is_guarantor is True``.
  Nothing in this contract invents entities, percentages, borrowers or
  guarantors.
* Never hide uncertainty: a diagram exists only when a case has an explicitly
  associated, valid, single-company JSON. Otherwise the status is ``unavailable``
  and the reason is recorded verbatim.
* Separate display graph from any governed legal-entity data contract: aggregate
  grouping nodes (e.g. "Other Subsidiaries", "JV / Minority Investments") may be
  DRAWN but are flagged and must not be treated as independently established
  legal persons.
* Auditability: the parsed artifact preserves source document id, document hash,
  JSON version, selected company key, node/relationship ids, simulation status
  and validator/renderer versions. The original JSON is never mutated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bo import Confidence, Direction
from app.schemas.agentic import Materiality


# Versioned so audit records can tie a diagram to the exact code that produced it.
CORP_STRUCTURE_ADAPTER_VERSION = "bo-m3-corp-structure-adapter-1.0.0"
CORP_STRUCTURE_VALIDATOR_VERSION = "validator-7-corp-structure-1.0.0"
CORP_STRUCTURE_RENDERER_VERSION = "bo-m3-corp-structure-svg-1.0.0"
# The deterministic v1.0 onboarding -> v2.0 refined-output transformer. Bumped
# independently of the downstream v2.0 adapter/renderer.
CORP_STRUCTURE_TRANSFORM_VERSION = "bo-m3-corp-structure-transform-1.0.0"

# The downstream presentation-ready contract (what the renderer/memo consume).
SUPPORTED_SCHEMA_VERSIONS = ("2.0",)
SUPPORTED_ARTIFACT_TYPES = ("llm_reproducible_corporate_structure_diagrams",)

# The simplified onboarding contract that a company supplies at ingestion. It is
# a nested corporate_tree with NO presentation (no colours/coordinates/canvas).
SUPPORTED_ONBOARDING_SCHEMA_VERSIONS = ("1.0",)
SUPPORTED_ONBOARDING_DOCUMENT_TYPES = ("corporate_structure_onboarding",)

# The exact, mandated message when no valid case-associated corporate-structure
# JSON is available. Must be displayed verbatim.
NO_JSON_MESSAGE = (
    "Corporate structure unavailable — no valid corporate-structure JSON was "
    "provided during onboarding."
)

CorporateStructureStatus = Literal["available", "unavailable", "rejected"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructuralCreditSignal(_Strict):
    """A grounded, qualified structural observation.

    Credit direction, verification and assessment availability are kept in
    SEPARATE fields (neutral is not the same as insufficient evidence). Nothing
    here is a numeric credit score; it reuses the repo's ``Direction`` /
    ``Materiality`` / ``Confidence`` vocabulary.
    """

    signal_id: str
    dimension: Literal[
        "borrower_position", "operating_vs_borrower", "ownership_and_control",
        "minority_or_jv_exposure", "structural_complexity",
        "illustrative_guarantor_coverage", "subsidiary_distribution_dependency",
        "cash_access_limitation",
    ]
    observation: str
    credit_direction: Direction
    credit_mechanism: str
    # Node / relationship ids that ground the observation (exact JSON pointers
    # are kept on the parsed artifact, not duplicated here).
    source_node_ids: list[str] = Field(default_factory=list)
    source_link_ids: list[str] = Field(default_factory=list)
    assessment_availability: Literal["sufficient", "insufficient_evidence"] = "sufficient"
    materiality: Materiality = Materiality.MEDIUM
    confidence: Confidence = Confidence.MEDIUM
    qualifications: list[str] = Field(default_factory=list)


class StructureNode(_Strict):
    """A validated node ready for deterministic rendering.

    ``is_grouping_node`` marks aggregate labels (Other / JV) that may be drawn
    but must not enter a governed legal-entity graph as a legal person.
    """

    node_id: str
    display_name: str
    role: str
    is_guarantor: bool = False
    is_grouping_node: bool = False
    center_x_px: float
    top_y_px: float
    width_px: float
    height_px: float


class StructureLink(_Strict):
    link_id: str
    parent_id: str
    child_id: str
    ownership_percent: float | None = None


class CorporateStructureProvenance(_Strict):
    """Everything an auditor needs to reproduce or challenge the diagram."""

    source_document_id: str | None = None
    document_hash: str
    json_schema_version: str
    artifact_type: str
    selected_company_key: str
    company_display_name: str
    simulation_status: str | None = None
    simulation_disclaimer: str | None = None
    footer_text: str | None = None
    node_ids: list[str] = Field(default_factory=list)
    relationship_ids: list[str] = Field(default_factory=list)
    json_pointers: dict[str, str] = Field(default_factory=dict)
    adapter_version: str = CORP_STRUCTURE_ADAPTER_VERSION
    validator_version: str = CORP_STRUCTURE_VALIDATOR_VERSION
    renderer_version: str = CORP_STRUCTURE_RENDERER_VERSION
    # Set only when the artifact originated from a simplified v1.0 onboarding
    # document that was deterministically transformed to the v2.0 contract.
    onboarding_schema_version: str | None = None
    onboarding_document_type: str | None = None
    onboarding_document_hash: str | None = None
    transform_version: str | None = None


class CorporateStructureArtifact(_Strict):
    """Result of adapting + validating a single-company corporate-structure JSON."""

    schema_version: Literal["bo-m3-corp-structure-1.0"] = "bo-m3-corp-structure-1.0"
    status: CorporateStructureStatus
    message: str | None = None
    provenance: CorporateStructureProvenance | None = None
    # Validated, render-ready values. Empty when status != "available".
    nodes: list[StructureNode] = Field(default_factory=list)
    links: list[StructureLink] = Field(default_factory=list)
    root_node_id: str | None = None
    guarantor_node_ids: list[str] = Field(default_factory=list)
    grouping_node_ids: list[str] = Field(default_factory=list)
    plain_text_tree: str | None = None
    structural_signals: list[StructuralCreditSignal] = Field(default_factory=list)
    # Precise problems when a file is withheld or partially downgraded.
    validation_issues: list[dict] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    is_simulated: bool = False
    # The refined, presentation-ready v2.0 JSON (Deliverable A,
    # ``m3_corporate_structure.json``). Populated when the artifact was produced
    # by transforming a simplified v1.0 onboarding document; empty for a direct
    # v2.0 source. This is the primary corporate-structure output and is never
    # replaced by an SVG/PNG.
    refined_structure: dict | None = None
    # Deterministic round-trip reconciliation of the refined v2.0 output against
    # the validated v1.0 input (entities, links, guarantors). Present only for a
    # transformed onboarding document.
    roundtrip_reconciliation: dict | None = None
