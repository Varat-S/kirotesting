"""Versioned contract for the governed BO-to-Structural-Overview boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.schemas.bo_m3 import (
    BorrowerIdentification,
    CashFlowLocation,
    CashTransferRelationship,
    LegalEntity,
    M3ValidationIssue,
    ManagementObservation,
    OwnershipRelationship,
    StructuralCreditFinding,
)


ASSESSMENT_SCHEMA_VERSION = "structural-overview-assessment-1.1"
# 1.2 is produced from a handoff carrying the Module 3 (m3-rebuild) block: it adds the two typed M3
# components and the management claims derived from them. 1.1 output is unchanged.
ASSESSMENT_SCHEMA_VERSION_M3 = "structural-overview-assessment-1.2"
ASSESSMENT_SCHEMA_VERSIONS = Literal[
    "structural-overview-assessment-1.1", "structural-overview-assessment-1.2"]
M3_FIELDS = ("m3_rebuild", "management_claims")
TRANSFORMATION_VERSION = "bo-to-so-1.1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BorrowerContext(StrictModel):
    reporting_entity_name: str
    legal_name: str | None = None
    entity_id: str | None = None
    identification_status: Literal["identified", "unavailable", "disputed"]
    identification_evidence_ids: list[str] = Field(default_factory=list)
    identification_limitation: str | None = None
    case_id: str
    as_of_date: str | None = None
    evidence_cutoff_timestamp: str | None = None
    evidence_snapshot_version: int
    source_bo_run_id: str


class FindingProvenance(StrictModel):
    source_handoff_id: str
    source_run_id: str
    source_module: str
    source_section: str
    source_hypothesis_id: str
    source_claim_ids: list[str] = Field(default_factory=list)


class NormalizedFinding(StrictModel):
    claim: str
    claim_type: str
    direction: str
    disposition: str
    admissibility: str
    confidence: str
    materiality: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)
    review_required: bool = False
    accepted_for_handoff: bool = False
    provenance: FindingProvenance


class CuratedClaim(StrictModel):
    claim_id: str
    title: str
    text: str
    section: str
    materiality: str
    hypothesis_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    qualification: Literal["accepted", "qualified"]


class MitigantClaim(StrictModel):
    capability: str
    existence: str
    effectiveness: Literal["validated", "unproven"]
    source_hypothesis_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    qualification: Literal["accepted", "qualified"]


class CompoundRisk(CuratedClaim):
    component_hypothesis_ids: list[str] = Field(default_factory=list)


class BusinessAssessment(StrictModel):
    business_model_and_revenue_drivers: list[NormalizedFinding] = Field(default_factory=list)
    industry_and_competitive_risks: list[NormalizedFinding] = Field(default_factory=list)
    management_and_operational_findings: list[NormalizedFinding] = Field(default_factory=list)
    strategy_and_capital_allocation_findings: list[NormalizedFinding] = Field(default_factory=list)
    material_business_vulnerabilities: list[CuratedClaim] = Field(default_factory=list)
    business_strengths: list[CuratedClaim] = Field(default_factory=list)
    mitigants: list[MitigantClaim] = Field(default_factory=list)
    compound_risks: list[CompoundRisk] = Field(default_factory=list)


class CorporateStructureAudit(StrictModel):
    """Non-consumable M3 material retained only for traceability."""

    validation_issues: list[M3ValidationIssue] = Field(default_factory=list)
    rejected_items: list[dict[str, Any]] = Field(default_factory=list)
    model_proposal: dict[str, Any] = Field(default_factory=dict)


class CorporateStructureAssessment(StrictModel):
    """Governed M3 output crossing the BO-to-SO boundary.

    Lists outside ``audit`` contain only records admitted by validator 7.  SO
    must still independently verify obligor identity, ranking, restrictions,
    and facility-specific legal effect.
    """

    source_status: Literal["validated", "unavailable"]
    source_schema_version: str | None = None
    validator_id: str | None = None
    validator_version: str | None = None
    borrower_identification: BorrowerIdentification
    legal_entities: list[LegalEntity] = Field(default_factory=list)
    ownership_relationships: list[OwnershipRelationship] = Field(default_factory=list)
    cash_flow_locations: list[CashFlowLocation] = Field(default_factory=list)
    cash_transfer_relationships: list[CashTransferRelationship] = Field(default_factory=list)
    structural_credit_findings: list[StructuralCreditFinding] = Field(default_factory=list)
    management_assessments: list[ManagementObservation] = Field(default_factory=list)
    governance_screening: list[StructuralCreditFinding] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    downstream_dependencies: list[dict[str, str]] = Field(default_factory=list)
    audit: CorporateStructureAudit = Field(default_factory=CorporateStructureAudit)


class EvidenceReference(StrictModel):
    evidence_id: str
    source_hypothesis_ids: list[str] = Field(default_factory=list)
    source_claim_ids: list[str] = Field(default_factory=list)
    source_sections: list[str] = Field(default_factory=list)


class GovernanceState(StrictModel):
    conclusion_status: str
    accepted_conclusion_ids: list[str] = Field(default_factory=list)
    qualified_conclusion_ids: list[str] = Field(default_factory=list)
    unresolved_challenges: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    human_review_status: str


class DownstreamSection(StrictModel):
    status: Literal["complete", "partial", "blocked", "unavailable"]
    reason: str
    source_ids: list[str] = Field(default_factory=list)


class DownstreamDependencies(StrictModel):
    bo_to_fo_handoffs: list[dict[str, Any]] = Field(default_factory=list)
    additional_financial_questions: list[str] = Field(default_factory=list)
    structural_or_facility_topics: list[str] = Field(default_factory=list)
    sections: dict[str, DownstreamSection]


class AssessmentAuditMetadata(StrictModel):
    transformation_version: str
    source_schema_version: str
    source_handoff_content_hash: str
    source_execution_mode: str
    canonical_hash_algorithm: Literal["sha256"] = "sha256"
    canonical_hash_scope: str
    canonical_references: dict[str, Any] = Field(default_factory=dict)


class ManagementMemoClaim(StrictModel):
    """One Module 3 management observation, carried to the memo with its citations."""

    observation_id: str
    executive: str | None
    direction: Literal["positive", "negative", "neutral", "unavailable"]
    category: Literal["transition", "integration", "continuity", "information_gap"]
    text: str
    citation_ids: list[str]


class StructuralOverviewAssessment(StrictModel):
    schema_version: ASSESSMENT_SCHEMA_VERSIONS = ASSESSMENT_SCHEMA_VERSION
    assessment_id: str
    status: Literal["complete", "partial", "blocked"]
    source_handoff_id: str
    source_bo_run_id: str
    borrower: BorrowerContext
    business_assessment: BusinessAssessment
    corporate_structure: CorporateStructureAssessment
    # Module 3 workflow only (schema 1.2); never serialized on a 1.1 assessment.
    m3_rebuild: dict[str, Any] | None = None
    management_claims: list[ManagementMemoClaim] = Field(default_factory=list)
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    governance: GovernanceState
    downstream_dependencies: DownstreamDependencies
    audit_metadata: AssessmentAuditMetadata
    final_credit_score: int | None = None
    facility_approval: str | None = None
    content_hash: str

    @model_validator(mode="after")
    def validate_m3_fields(self):
        if self.schema_version == ASSESSMENT_SCHEMA_VERSION:
            if self.m3_rebuild is not None or self.management_claims:
                raise ValueError("Module 3 fields require structural-overview-assessment-1.2.")
            return self
        from app.services.bo.m3_rebuild.narrative import validate_claims
        validate_claims([c.model_dump(mode="json") for c in self.management_claims], self.m3_rebuild)
        return self

    @model_serializer(mode="wrap")
    def _omit_m3_fields(self, handler):
        data = handler(self)
        if self.schema_version == ASSESSMENT_SCHEMA_VERSION:
            for key in M3_FIELDS:
                data.pop(key, None)
        return data
