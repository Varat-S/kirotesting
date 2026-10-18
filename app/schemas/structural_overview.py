"""Versioned contract for the BO-to-Structural-Overview integration slice."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ASSESSMENT_SCHEMA_VERSION = "structural-overview-assessment-1.0"
TRANSFORMATION_VERSION = "bo-to-so-1.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BorrowerContext(StrictModel):
    legal_name: str
    entity_id: str | None = None
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
    provisional_business_risk_band: int | None = None
    business_risk_score_status: str
    scoring_limitations: list[str] = Field(default_factory=list)


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


class StructuralOverviewAssessment(StrictModel):
    schema_version: Literal["structural-overview-assessment-1.0"] = ASSESSMENT_SCHEMA_VERSION
    assessment_id: str
    status: Literal["complete", "partial", "blocked"]
    source_handoff_id: str
    source_bo_run_id: str
    borrower: BorrowerContext
    business_assessment: BusinessAssessment
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    governance: GovernanceState
    downstream_dependencies: DownstreamDependencies
    audit_metadata: AssessmentAuditMetadata
    final_credit_score: int | None = None
    facility_approval: str | None = None
    content_hash: str
