"""Strict, additive Module 3 corporate-structure assessment contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.bo import Admissibility, Confidence, Disposition
from app.schemas.agentic import Materiality


M3_ASSESSMENT_VERSION = "bo-m3-corporate-assessment-1.0"
M3_VALIDATOR_VERSION = "validator-7-1.0.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegalEntity(_Strict):
    entity_id: str
    legal_name: str
    jurisdiction: str | None = None
    classification: Literal[
        "ultimate_parent", "intermediate_holding_company", "reporting_parent",
        "operating_subsidiary", "joint_venture", "partially_owned_subsidiary",
        "proposed_borrower", "other_material_entity",
    ]
    verification_status: Literal["verified", "qualified", "unverified", "disputed"]
    evidence_ids: list[str] = Field(default_factory=list)
    materiality_reason: str


class OwnershipRelationship(_Strict):
    relationship_id: str
    parent_entity_id: str
    subsidiary_entity_id: str
    ownership_percentage: float | None = None
    control_basis: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confirmation_status: Literal["verified", "qualified", "unverified", "disputed"]
    uncertainty: str | None = None


class CashFlowLocation(_Strict):
    cash_flow_id: str
    entity_id: str
    amount: float | None = None
    period: str | None = None
    currency: str | None = None
    units: str | None = None
    accounting_basis: str | None = None
    reporting_scope: Literal["consolidated", "segment", "entity"]
    cash_flow_roles: list[Literal[
        "operating_cash_generator", "legal_cash_controller", "cash_holder",
        "debt_service_obligor",
    ]] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_status: Literal["verified", "qualified", "unverified", "disputed"]
    missing_information: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def amount_metadata(self):
        if self.amount is not None and not all(
            (self.period, self.currency, self.units, self.accounting_basis,
             self.evidence_ids)
        ):
            raise ValueError(
                "a cash-flow amount requires period, currency, units, accounting_basis and evidence_ids"
            )
        return self


class CashTransferRelationship(_Strict):
    transfer_id: str
    source_entity_id: str
    destination_entity_id: str
    mechanism: Literal[
        "dividend", "intercompany_loan", "cash_pool", "management_fee",
        "distribution", "other",
    ]
    transfer_evidence_state: Literal[
        "capacity_only", "documented_actual_transfer", "proposed", "unknown",
    ]
    known_restrictions_or_dependencies: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confirmation_status: Literal["verified", "qualified", "unverified", "disputed"]
    uncertainty: str | None = None


class StructuralCreditFinding(_Strict):
    finding_id: str
    hypothesis_id: str | None = None
    credit_hypothesis: str
    causal_mechanism: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    supporting_evidence_investigated: bool = False
    contradictory_evidence_investigated: bool = False
    disposition: Disposition
    admissibility: Admissibility
    confidence: Confidence = Confidence.MEDIUM
    materiality: Materiality = Materiality.MEDIUM
    downstream_verification_requirements: list[str] = Field(default_factory=list)
    borrower_specific: bool = False
    validation_reasons: list[str] = Field(default_factory=list)


class ManagementObservation(_Strict):
    observation_id: str
    management_id: str
    legal_name: str
    role: str
    effective_from: str | None = None
    effective_to: str | None = None
    dimension: Literal["competence", "credibility_integrity"]
    hypothesis: str
    credit_mechanism: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    supporting_evidence_investigated: bool = False
    contradictory_evidence_investigated: bool = False
    disposition: Disposition
    admissibility: Admissibility
    confidence: Confidence = Confidence.MEDIUM
    materiality: Materiality = Materiality.MEDIUM
    missing_information: list[str] = Field(default_factory=list)
    validation_reasons: list[str] = Field(default_factory=list)


class BorrowerIdentification(_Strict):
    status: Literal["identified", "unavailable", "disputed"]
    entity_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    limitation: str | None = None

    @model_validator(mode="after")
    def identified_is_evidenced(self):
        if self.status == "identified" and (not self.entity_id or not self.evidence_ids):
            raise ValueError("an identified borrower requires entity_id and evidence_ids")
        if self.status != "identified" and not self.limitation:
            raise ValueError("an unavailable or disputed borrower requires a limitation")
        return self


class M3ValidationIssue(_Strict):
    rule_id: str
    item_id: str | None = None
    action: Literal["rejected", "downgraded", "recorded_gap"]
    reason: str


class M3CorporateAssessment(_Strict):
    schema_version: Literal["bo-m3-corporate-assessment-1.0"] = M3_ASSESSMENT_VERSION
    validator_id: Literal["validator_7"] = "validator_7"
    validator_version: Literal["validator-7-1.0.0"] = M3_VALIDATOR_VERSION
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
    validation_issues: list[M3ValidationIssue] = Field(default_factory=list)
    rejected_items: list[dict] = Field(default_factory=list)
    model_proposal: dict = Field(default_factory=dict)
