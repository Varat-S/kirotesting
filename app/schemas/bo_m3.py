"""Strict, additive Module 3 corporate-structure assessment contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.bo import Admissibility, Confidence, Direction, Disposition
from app.schemas.agentic import Materiality


M3_ASSESSMENT_VERSION = "bo-m3-corporate-assessment-1.2"
M3_VALIDATOR_VERSION = "validator-7-1.1.0"

# The 1.0 contract (and its validator stamp) remain accepted so scripted
# proposals authored before the Layer-2 additive extension still validate.
M3_ASSESSMENT_VERSIONS = (
    "bo-m3-corporate-assessment-1.0", "bo-m3-corporate-assessment-1.1",
    "bo-m3-corporate-assessment-1.2")
M3_VALIDATOR_VERSIONS = ("validator-7-1.0.0", "validator-7-1.1.0")

# The six Layer-2 analytical dimensions the specialist may address. Kept as a
# small, closed vocabulary so a finding always declares which structural credit
# question it answers; ``None`` (legacy) stays permitted.
StructuralAnalyticalDimension = Literal[
    "borrower_position", "guarantor_coverage", "cash_flow_access",
    "operating_debt_structure", "minority_or_jv", "structural_complexity",
]


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
    identity_type: Literal[
        "legal_entity", "operating_unit", "collective_grouping", "unresolved",
    ] = "unresolved"
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
    measure_type: Literal[
        "revenue", "ebitda", "operating_income", "operating_cash_flow",
        "free_cash_flow", "cash_balance", "distributable_cash",
        "actual_upstream_distribution", "unclassified",
    ] = "unclassified"
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
    # --- Layer 2 additive structural-credit linkage (all OPTIONAL; safe
    #     defaults so 1.0 proposals still validate). ------------------------
    # Which structural credit question this finding answers.
    analytical_dimension: StructuralAnalyticalDimension | None = None
    # The factual structural observation the finding rests on (kept distinct
    # from the analytical credit_hypothesis / causal_mechanism).
    structural_observation: str | None = None
    # Exact stable corporate facts the finding references.
    referenced_node_ids: list[str] = Field(default_factory=list)
    referenced_link_ids: list[str] = Field(default_factory=list)
    # The onboarding source the structural facts come from (doc id and/or hash).
    onboarding_source_reference: str | None = None
    # Credit direction is kept SEPARATE from disposition: neutral is the safe
    # default and never forces a positive/negative stance.
    credit_direction: Direction = Direction.NEUTRAL
    # insufficient_evidence is represented SEPARATELY from a neutral direction.
    assessment_availability: Literal["sufficient", "insufficient_evidence"] = "sufficient"
    qualifications: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class ManagementObservation(_Strict):
    observation_id: str
    management_id: str
    legal_name: str
    role: str
    effective_from: str | None = None
    effective_to: str | None = None
    dimension: Literal["competence", "credibility_integrity", "leadership_continuity",
                       "relevant_experience", "transition_risk"]
    evidence_basis: Literal[
        "reported_experience", "credentials_or_tenure", "outcome_evidence",
        "control_assurance", "misconduct_or_enforcement", "unclassified", "disclosed_leadership_history",
    ] = "unclassified"
    attribution: Literal[
        "company_reported", "independently_observed", "auditor_assured",
        "validator_inference", "unclassified",
    ] = "unclassified"
    assurance_scope: str | None = None
    assurance_date: str | None = None
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
    # Credit direction is kept SEPARATE from disposition (credibility): a credible
    # explanation of an adverse development never flips the direction positive.
    credit_direction: Direction = Direction.NEUTRAL
    credibility_vs_direction_note: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    validation_reasons: list[str] = Field(default_factory=list)
    executive_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    mitigating_considerations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def continuity_direction_is_explicit(cls, values):
        if isinstance(values, dict) and values.get("dimension") in {
            "leadership_continuity", "relevant_experience", "transition_risk"
        } and "credit_direction" not in values:
            raise ValueError("new stability findings require explicit credit_direction; missing is not neutral")
        return values


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
