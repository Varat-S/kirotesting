"""Pydantic contracts for the Module 4 Debt Structure Specialist Agent.

This is the **Debt Structure, Refinancing History & Debt Utilisation** specialist
(Business Overview Architecture V6) -- one of Module 4's two agents. The other,
Strategy & Capital Allocation (``m4_strategy_capital``), owns acquisition/capex
strategy and the qualitative capital-allocation / stewardship judgement; this
specialist owns the DEBT: structure, creditor priority, refinancing, maturity,
and how capital deployment was funded and affected leverage. The two no longer
overlap. It is an INDEPENDENT specialist:
it reads the shared SEC 10-K + structured financial data and produces its own
four deliverables. It does NOT consume the outputs of Modules 1/2/3 and it does
NOT draft loan term sheets, covenants or security agreements (that belongs to
the downstream Risk Mitigation & Structuring agent). Cross-module synthesis is a
separate downstream agent's job.

The four deliverables, with their EXACT required column structures, are:

* :class:`Deliverable1` -- Existing Debt & Creditor Priority Map
* :class:`Deliverable2` -- Historical Refinancing & Debt Maturity Analysis
* :class:`Deliverable3` -- Debt Utilisation & Leverage Impact Assessment
* :class:`Deliverable4` -- Module 4 Credit Conclusion

These schemas are the single source of truth the Module 4 LLM output is
validated against (mirroring :mod:`app.schemas.llm`). The matching JSON Schema
is exported as :data:`MODULE4_JSON_SCHEMA` for use by the prompt registry and
the LLMClient before any output is consumed.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class PriorityTier(str, Enum):
    """Creditor priority tier in the structural-subordination framework.

    * ``TIER_1`` -- Direct OpCo first-lien / secured obligations.
    * ``TIER_2`` -- Senior unsecured debt WITH upstream (HoldCo) guarantees.
    * ``TIER_3`` -- Unguaranteed HoldCo (structurally subordinated) debt.
    """

    TIER_1 = "tier_1_direct_opco_secured"
    TIER_2 = "tier_2_senior_unsecured_guaranteed"
    TIER_3 = "tier_3_unguaranteed_holdco"


class SecurityStatus(str, Enum):
    """Collateral / security status of a debt obligation."""

    SECURED = "secured"
    UNSECURED = "unsecured"


class CreditRatingVerdict(str, Enum):
    """Overall Module 4 credit-rating verdict band."""

    STRONG = "strong"
    SATISFACTORY = "satisfactory"
    ADEQUATE = "adequate"
    WEAK = "weak"


class LoanPlacementTier(str, Enum):
    """Where a prospective bank loan would rank under each assumption."""

    SECURED_TIER_1 = "secured_tier_1"
    SENIOR_UNSECURED_TIER_2 = "senior_unsecured_tier_2"


# ---------------------------------------------------------------------------
# Deliverable 1: Existing Debt & Creditor Priority Map
# ---------------------------------------------------------------------------


class ExtractionSourceMapping(BaseModel):
    """Narrative mapping the deliverable's facts to specific 10-K locations.

    Every deliverable opens with an explicit extraction-source narrative citing
    where in the filing the evidence was drawn from (Item 7 MD&A, Item 8 notes,
    Item 15 exhibit index, with page references). This keeps the specialist
    grounded and auditable.
    """

    model_config = ConfigDict(extra="forbid")

    narrative: str
    item_references: list[str] = Field(default_factory=list)
    page_references: list[str] = Field(default_factory=list)


class SeniorNoteTranche(BaseModel):
    """One row of the Itemized Senior Unsecured Notes & Debentures Schedule."""

    model_config = ConfigDict(extra="forbid")

    senior_note_series: str = Field(description="Senior Note Series / Description")
    legal_borrower: str = Field(description="Legal Borrower")
    principal_amount_m: float = Field(description="Principal Amount ($M)")
    coupon_interest_rate: str = Field(description="Coupon / Interest Rate")
    maturity_year: int = Field(description="Maturity Year")
    collateral_security_status: SecurityStatus = Field(
        description="Collateral / Security Status"
    )
    guarantors_credit_support: str = Field(description="Guarantors & Credit Support")
    priority_ranking_tier: PriorityTier = Field(description="Priority Ranking Tier")


class NonNoteDebtObligation(BaseModel):
    """One row of the Itemized Non-Note Debt Obligations Schedule."""

    model_config = ConfigDict(extra="forbid")

    obligation_facility: str = Field(description="Obligation / Facility")
    legal_borrower: str = Field(description="Legal Borrower")
    principal_drawn_m: float = Field(description="Principal / Drawn ($M)")
    available_facility_limit_m: float | None = Field(
        default=None, description="Available Facility Limit ($M)"
    )
    interest_rate_terms: str = Field(description="Interest Rate / Terms")
    maturity: str = Field(description="Maturity")
    collateral_security_status: SecurityStatus = Field(
        description="Collateral / Security Status"
    )
    guarantors_credit_support: str = Field(description="Guarantors / Credit Support")
    priority_ranking_tier: PriorityTier = Field(description="Priority Ranking Tier")


class CreditorPriorityFramework(BaseModel):
    """Creditor Priority & Structural Subordination Framework.

    Classifies obligations into the three tiers, computes the Structural
    Subordination Drag Ratio, and states where a prospective bank loan would be
    placed under both a Secured (Tier 1) and a Senior Unsecured (Tier 2)
    assumption.
    """

    model_config = ConfigDict(extra="forbid")

    tier_1_direct_opco_secured_m: float = Field(
        description="Tier 1 direct OpCo first-lien/secured obligations ($M)"
    )
    tier_2_senior_unsecured_guaranteed_m: float = Field(
        description="Tier 2 senior unsecured w/ upstream guarantees ($M)"
    )
    tier_3_unguaranteed_holdco_m: float = Field(
        description="Tier 3 unguaranteed HoldCo debt ($M)"
    )
    consolidated_total_indebtedness_m: float = Field(
        description="Consolidated Total Indebtedness ($M)"
    )
    structural_subordination_drag_ratio: float = Field(
        description="Tier 1 direct OpCo secured / Consolidated total indebtedness"
    )
    prospective_loan_secured_placement: str = Field(
        description="Prospective bank loan placement under a Secured (Tier 1) assumption"
    )
    prospective_loan_senior_unsecured_placement: str = Field(
        description="Prospective bank loan placement under a Senior Unsecured (Tier 2) assumption"
    )


class Deliverable1(BaseModel):
    """Deliverable 1 -- Existing Debt & Creditor Priority Map."""

    model_config = ConfigDict(extra="forbid")

    extraction_source: ExtractionSourceMapping
    senior_notes_schedule: list[SeniorNoteTranche] = Field(default_factory=list)
    non_note_obligations_schedule: list[NonNoteDebtObligation] = Field(
        default_factory=list
    )
    creditor_priority_framework: CreditorPriorityFramework


# ---------------------------------------------------------------------------
# Deliverable 2: Historical Refinancing & Debt Maturity Analysis
# ---------------------------------------------------------------------------


class RefinancingTransactionCategory(str, Enum):
    """Category of a logged refinancing / maturity transaction."""

    NEW_ISSUANCE = "new_issuance"
    REPAYMENT_AT_MATURITY = "repayment_at_maturity"
    EARLY_REDEMPTION = "early_redemption"
    FACILITY_AMENDMENT = "facility_amendment"
    TENDER_EXCHANGE = "tender_exchange"


class RefinancingLogEntry(BaseModel):
    """One row of the Itemized Refinancing Log (FY2023-FY2025)."""

    model_config = ConfigDict(extra="forbid")

    transaction_category: RefinancingTransactionCategory = Field(
        description="Transaction Category"
    )
    obligation_series_description: str = Field(
        description="Obligation / Series Description"
    )
    principal_amount_m: float = Field(description="Principal Amount ($M)")
    coupon_interest_terms: str = Field(description="Coupon / Interest Terms")
    execution_date_fiscal_period: str = Field(
        description="Execution Date / Fiscal Period"
    )
    original_retired_maturity: str | None = Field(
        default=None, description="Original / Retired Maturity"
    )
    new_extended_maturity: str | None = Field(
        default=None, description="New Extended Maturity"
    )
    use_of_proceeds_impact: str = Field(
        description="Use of Proceeds / Refinancing Impact"
    )


class RefinancingMetrics(BaseModel):
    """The deterministic refinancing metrics for Deliverable 2."""

    model_config = ConfigDict(extra="forbid")

    weighted_avg_refinancing_lead_time_months: float = Field(
        description="Weighted Average Refinancing Lead Time (tau_ref), months before maturity"
    )
    refinancing_cost_spread_delta_bps: float = Field(
        description="Refinancing Cost Spread Delta (delta-S), basis points"
    )
    weighted_avg_maturity_extension_years: float = Field(
        description="Weighted Average Maturity Extension Horizon (delta-T), years"
    )
    historical_refinancing_distress_events: int = Field(
        default=0, ge=0, description="Count of historical refinancing distress events"
    )


class Deliverable2(BaseModel):
    """Deliverable 2 -- Historical Refinancing & Debt Maturity Analysis."""

    model_config = ConfigDict(extra="forbid")

    extraction_source: ExtractionSourceMapping
    refinancing_log: list[RefinancingLogEntry] = Field(default_factory=list)
    metrics: RefinancingMetrics
    refinancing_capacity_discipline_verdict: str


# ---------------------------------------------------------------------------
# Deliverable 3: Debt Utilisation & Leverage Impact Assessment
#
# SCOPE BOUNDARY (Module 4 dual-specialist split): this deliverable covers only
# how capital deployment was FUNDED and its impact on LEVERAGE / debt-service
# capacity -- the debt specialist's lane. The QUALITATIVE judgement of capex
# efficiency and M&A / integration discipline belongs to the Strategy & Capital
# Allocation agent (``m4_strategy_capital``) and is deliberately NOT assessed
# here, so the two Module 4 agents do not overlap.
# ---------------------------------------------------------------------------


class CapitalDeploymentEntry(BaseModel):
    """One row of the Itemized Macro Capital Deployment Reconciliation.

    Describes where capital went, how much, and — the debt-relevant part — how it
    was FUNDED (operating cash flow vs debt) and the resulting effect on leverage.
    It does NOT judge strategic merit (that is the strategy agent's remit).
    """

    model_config = ConfigDict(extra="forbid")

    capital_allocation_stream: str = Field(
        description="Capital Allocation Stream / Project"
    )
    specific_use_description: str = Field(description="Specific Use / Description")
    cumulative_amount_3y_m: float = Field(
        description="Cumulative Amount ($M) (3-Year)"
    )
    primary_funding_source: str = Field(
        description="Primary Funding Source (operating cash flow vs debt-funded)"
    )
    leverage_impact: str = Field(
        description="Effect on leverage / debt-service capacity "
        "(e.g. debt-funded, increased net debt; self-funded, leverage-neutral). "
        "Debt-relevant funding impact ONLY -- not a strategic-merit judgement."
    )


class DebtUtilisationMatrix(BaseModel):
    """Debt-utilisation / leverage-impact assessment (debt specialist's lane).

    Scoped to funding and leverage effects. Capex-efficiency and M&A-integration
    QUALITY are intentionally excluded -- they belong to ``m4_strategy_capital``.
    """

    model_config = ConfigDict(extra="forbid")

    debt_funded_vs_internally_funded_deployment: str = Field(
        description="Split of capital deployment funded by debt vs internal cash, "
        "and what that implies for the debt load."
    )
    shareholder_distributions_vs_leverage_balance: str = Field(
        description="Dividends / buybacks relative to leverage headroom and debt "
        "policy -- how distributions affect the debt profile."
    )


class Deliverable3(BaseModel):
    """Deliverable 3 -- Debt Utilisation & Leverage Impact Assessment."""

    model_config = ConfigDict(extra="forbid")

    extraction_source: ExtractionSourceMapping
    capital_deployment_reconciliation: list[CapitalDeploymentEntry] = Field(
        default_factory=list
    )
    debt_utilisation_matrix: DebtUtilisationMatrix
    debt_utilisation_leverage_verdict: str


# ---------------------------------------------------------------------------
# Deliverable 4: Module 4 Credit Conclusion
# ---------------------------------------------------------------------------


class SynthesisMatrixRow(BaseModel):
    """One row of the Evidence-Driven Synthesis Matrix."""

    model_config = ConfigDict(extra="forbid")

    analytical_scope_pillar: str = Field(description="Analytical Scope / Pillar")
    core_empirical_evidence: str = Field(
        description="Core Empirical Evidence & Key Metrics"
    )
    deliverable_verdict: str = Field(description="Deliverable Verdict")
    bank_lender_implications: str = Field(description="Bank Lender Implications")


class ExecutiveCreditSummaryBox(BaseModel):
    """The Executive Credit Summary Box closing Deliverable 4."""

    model_config = ConfigDict(extra="forbid")

    target_entity_and_period: str
    overall_credit_rating_verdict: CreditRatingVerdict
    executive_takeaways: list[str] = Field(default_factory=list)
    recommended_loan_placement: LoanPlacementTier


class Deliverable4(BaseModel):
    """Deliverable 4 -- Module 4 Credit Conclusion."""

    model_config = ConfigDict(extra="forbid")

    evidence_synthesis_narrative: str
    core_credit_assessment: str
    synthesis_matrix: list[SynthesisMatrixRow] = Field(default_factory=list)
    executive_credit_summary_box: ExecutiveCreditSummaryBox


# ---------------------------------------------------------------------------
# Top-level Module 4 output
# ---------------------------------------------------------------------------


class Module4Output(BaseModel):
    """The complete, schema-validated Module 4 specialist output.

    Bundles all four deliverables. This is the object the Module 4 LLM output is
    validated into before any downstream consumption (Req 19.4 style contract).
    """

    model_config = ConfigDict(extra="forbid")

    deliverable_1: Deliverable1
    deliverable_2: Deliverable2
    deliverable_3: Deliverable3
    deliverable_4: Deliverable4


MODULE4_JSON_SCHEMA = Module4Output.model_json_schema()
