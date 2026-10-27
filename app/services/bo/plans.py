"""Business Overview v2 roster data: agent codes, ownership and retrieval plans.

Pure data (no I/O, no LLM). The agent registry builds the six v2 BO narrow agents
from this module, and the BO retriever executes the plans. A plan is a bounded
list of term queries, each with a declared PURPOSE, so every specialist looks for
evidence that supports its hypotheses, evidence that contradicts them, mitigating
capabilities, and evidence that a claimed mitigant is unproven.

Queries run only over narrative passages already admitted to the
``CanonicalEvidenceSnapshot``; they never fetch anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.hashing import content_hash

PURPOSES = ("support", "contradict", "mitigant", "mitigant_challenge")


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    purpose: str  # one of PURPOSES
    terms: tuple[str, ...]  # case-insensitive regular expressions
    max_passages: int = 3

    def __post_init__(self) -> None:
        if self.purpose not in PURPOSES:
            raise ValueError(f"Unknown retrieval purpose {self.purpose!r}.")


@dataclass(frozen=True)
class BoAgentSpec:
    code: str  # BO1..BO6
    agent_id: str
    title: str
    owned_parameters: tuple[str, ...]
    plan: tuple[RetrievalQuery, ...]
    # Structured task sections; a broad agent is split rather than multiplied.
    task_sections: tuple[str, ...] = ()
    # Which groups of deterministic indicators this specialist receives.
    indicator_groups: tuple[str, ...] = ()
    # Roster v3 metadata (defaults keep v1/v2 specs unchanged). ``module`` is the
    # internal BO module; ``dimension`` is the credit dimension the agent owns;
    # an agent with ``executable=False`` declares ownership only and is never
    # run; ``validator_id`` names its logically separate validator.
    module: str = "M1"
    dimension: str | None = None
    executable: bool = True
    validator_id: str | None = None

    def plan_hash(self) -> str:
        return content_hash(
            [[q.query_id, q.purpose, list(q.terms), q.max_passages] for q in self.plan]
        )

    def all_terms(self) -> tuple[str, ...]:
        return tuple(term for query in self.plan for term in query.terms)


def _q(query_id, purpose, *terms, k=3):
    return RetrievalQuery(query_id, purpose, tuple(terms), k)


BO_AGENT_SPECS: tuple[BoAgentSpec, ...] = (
    BoAgentSpec(
        "BO1", "bo_business_model_revenue", "Business Model & Revenue Quality",
        ("business_model_classification", "revenue_recurrence_assessment",
         "demand_defensiveness_assessment", "cyclicality_seasonality_assessment"),
        (
            _q("bo1.revenue_model", "support", r"sources? of revenue", r"revenues? by",
               r"segments?", r"services? (?:we|the company) provid", r"facilit(?:y|ies)",
               r"business strategy"),
            _q("bo1.demand_resilience", "support", r"demand for", r"essential",
               r"non-?discretionary", r"recurring", r"admissions", r"occupancy"),
            _q("bo1.demand_fragility", "contradict", r"seasonal", r"cyclical",
               r"economic (?:downturn|conditions)", r"recession", r"unemployment",
               r"elective", r"declin\w* in (?:volumes?|demand|admissions)"),
            _q("bo1.stabilisers", "mitigant", r"diversif", r"outpatient",
               r"mix of services", r"long-term (?:contracts?|agreements?)"),
            _q("bo1.stabiliser_limits", "mitigant_challenge", r"concentrat",
               r"significant portion", r"depend\w* (?:up)?on", r"capital intensive"),
        ),
        indicator_groups=('growth', 'segment', 'payer', 'capital'),
    ),
    BoAgentSpec(
        "BO2", "bo_customers_contracts", "Customers, Contracts & Revenue Realisation",
        ("customer_dependence_assessment", "supplier_dependence_assessment",
         "contract_termination_risk_assessment", "revenue_realisation_assessment"),
        (
            _q("bo2.customer_payer_mix", "support", r"customers?", r"payers?",
               r"Medicare", r"Medicaid", r"managed care", r"accounted for"),
            _q("bo2.contract_terms", "support", r"contracts?", r"agreements?",
               r"renew", r"negotiat", r"multi-?year"),
            _q("bo2.contract_limits", "contradict", r"terminat", r"no assurance",
               r"not guarantee", r"may not (?:renew|be renewed)", r"narrow networks?",
               r"out-of-network", r"minimum (?:volumes?|purchases?)"),
            _q("bo2.realisation", "contradict", r"uninsured", r"self-pay",
               r"uncompensated", r"charity care", r"bad debts?", r"denials?",
               r"implicit price concessions", r"accounts receivable"),
            _q("bo2.suppliers", "support", r"suppliers?", r"group purchasing",
               r"sole[- ]source", r"single[- ]source", r"vendors?"),
            _q("bo2.offsets", "mitigant", r"diversif\w* (?:payer|customer)",
               r"collection (?:efforts|procedures)", r"eligibility", r"supplemental payments?"),
        ),
        indicator_groups=('payer', 'realisation', 'growth'),
    ),
    BoAgentSpec(
        "BO3", "bo_competition_pricing", "Competition & Pricing Power",
        ("competitive_position_assessment", "pricing_power_assessment",
         "switching_cost_assessment", "competitive_pressure_assessment",
         "barriers_to_entry_assessment"),
        (
            _q("bo3.position", "support", r"compet", r"market share",
               r"market position", r"competitors?"),
            _q("bo3.pricing", "support", r"pric(?:e|es|ing)", r"rate increases?",
               r"reimbursement rates?", r"revenue per", r"negotiat"),
            _q("bo3.threats", "contradict", r"new entrants?", r"substitut",
               r"alternative (?:sites?|providers?)", r"ambulatory", r"tele(?:health|medicine)",
               r"price transparency", r"pressure"),
            _q("bo3.defences", "mitigant", r"barriers? to entry", r"certificate of need",
               r"scale", r"network", r"switching", r"reputation"),
            _q("bo3.defence_limits", "mitigant_challenge", r"repeal\w*",
               r"physician-owned", r"tax-exempt|not-for-profit", r"case mix|acuity",
               r"payer mix"),
        ),
        indicator_groups=('growth', 'payer', 'segment'),
    ),
    BoAgentSpec(
        "BO4", "bo_operations_management", "Operations & Execution",
        ("acquisition_strategy_assessment",
         "integration_risk_assessment", "capex_strategy_assessment",
         "workforce_cost_resilience_assessment"),
        (
            _q("bo4.workforce", "contradict", r"labor", r"nurs", r"physicians?",
               r"staffing", r"contract labor", r"shortage", r"collective bargaining",
               r"turnover"),
            _q("bo4.workforce_initiatives", "mitigant", r"training", r"college",
               r"education", r"pipeline", r"recruit", r"retention"),
            _q("bo4.initiative_limits", "mitigant_challenge", r"no assurance",
               r"may not be (?:able|successful)", r"unable to",
               r"increas\w* (?:in )?(?:labor|costs)"),
            _q("bo4.procurement", "support", r"supply (?:costs?|chain)",
               r"group purchasing", r"procurement", r"purchasing organi[sz]ation", k=2),
            _q("bo4.capital_deployment", "support", r"acquisitions?", r"integrat",
               r"capital expenditures?", r"expansion", r"construction",
               r"divestitures?"),
        ),
        task_sections=(
            "workforce_and_cost_structure",
            "procurement_and_suppliers", "capital_deployment_ma_capex",
        ),
        indicator_groups=('workforce', 'capital', 'margin', 'cash'),
    ),
    BoAgentSpec(
        "BO5", "bo_regulation_external", "Regulation, Litigation & External Risks",
        ("regulatory_dependency_assessment", "litigation_risk_assessment",
         "material_event_assessment", "external_event_exposure_assessment"),
        (
            _q("bo5.regulation", "support", r"regulat", r"legislat", r"compliance",
               r"licens", r"accredit", r"government programs?", r"reimbursement"),
            _q("bo5.litigation", "support", r"litigation", r"lawsuits?",
               r"legal proceedings", r"investigations?", r"False Claims",
               r"professional liability|malpractice", r"contingenc"),
            _q("bo5.external", "support", r"hurricanes?", r"natural disasters?",
               r"weather", r"pandemic", r"cyber", r"geographic\w* concentrat",
               r"state of|states of"),
            _q("bo5.forecast_vs_existing", "contradict", r"cannot predict",
               r"proposed", r"uncertain", r"could (?:adversely|materially)"),
            _q("bo5.protections", "mitigant", r"insurance", r"reserves?",
               r"compliance program", r"self-insur"),
        ),
        indicator_groups=('payer', 'geography', 'segment'),
    ),
    BoAgentSpec(
        "BO6", "bo_resilience_interactions", "Business Resilience & Risk Interactions",
        ("compound_risk_assessment", "mitigating_capability_assessment"),
        (
            _q("bo6.interactions", "support", r"exacerbat", r"together with",
               r"in addition to", r"concentrat", r"depend", k=2),
            _q("bo6.capabilities", "mitigant", r"scale", r"diversif", r"flexib",
               r"contingency", r"business continuity", k=2),
            _q("bo6.capability_limits", "mitigant_challenge", r"no assurance",
               r"may not be (?:able|sufficient)", r"limited ability", k=2),
        ),
        indicator_groups=('growth', 'payer', 'workforce', 'realisation', 'geography'),
    ),
)

BO_SPEC_BY_AGENT = {spec.agent_id: spec for spec in BO_AGENT_SPECS}
BO_SPEC_BY_CODE = {spec.code: spec for spec in BO_AGENT_SPECS}
BO_PRIMARY_AGENT_IDS = tuple(s.agent_id for s in BO_AGENT_SPECS if s.code != "BO6")
BO_RESILIENCE_AGENT_ID = BO_SPEC_BY_CODE["BO6"].agent_id
BO_NARROW_AGENT_IDS = tuple(s.agent_id for s in BO_AGENT_SPECS)
BO_ORCHESTRATOR_ID = "business_orchestrator"
BO_CHALLENGER_ID = "business_challenge"
BO_ACTIVE_AGENT_IDS = (*BO_NARROW_AGENT_IDS, BO_ORCHESTRATOR_ID, BO_CHALLENGER_ID)

# Prompt names of the v2 synthesis agents (distinct from the v1 prompt names so
# the append-only prompt registry never alternates versions between rosters).
BO_ORCHESTRATOR_PROMPT = "bo_v2_business_orchestrator"
BO_CHALLENGER_PROMPT = "bo_v2_business_challenge"

# Permitted categorical values for every BO parameter that is not scored by a
# rubric (rubric-scored parameters take their values from the scoring config).
# "not_established" is always available: an agent must be able to say that the
# evidence does not support a classification instead of forcing one.
PARAMETER_VALUE_SETS: dict[str, tuple[str, ...]] = {
    "revenue_recurrence_assessment": (
        "contractually_committed", "recurring_not_contracted", "transactional",
        "mixed", "not_established"),
    "demand_defensiveness_assessment": (
        "demonstrated_resilient", "resilient_with_caveats", "cyclical",
        "not_established"),
    "cyclicality_seasonality_assessment": (
        "seasonal", "limited_seasonality", "not_seasonal", "cyclical",
        "not_established"),
    "customer_dependence_assessment": (
        "concentrated_counterparties", "concentrated_payer_categories",
        "diversified", "not_established"),
    "supplier_dependence_assessment": ("concentrated", "diversified", "not_established"),
    "contract_termination_risk_assessment": ("high", "moderate", "low", "not_established"),
    "revenue_realisation_assessment": ("strong", "adequate", "pressured", "not_established"),
    "barriers_to_entry_assessment": ("high", "moderate", "low", "not_established"),
    "acquisition_strategy_assessment": (
        "outcomes_evidenced", "stated_strategy_only", "concerns_evidenced",
        "not_established"),
    "integration_risk_assessment": ("high", "moderate", "low", "not_established"),
    "capex_strategy_assessment": (
        "growth_oriented", "maintenance_oriented", "mixed", "not_established"),
    "workforce_cost_resilience_assessment": (
        "demonstrated", "mitigants_unproven", "pressured", "not_established"),
    "regulatory_dependency_assessment": ("high", "moderate", "low", "not_established"),
    "litigation_risk_assessment": (
        "existing_material_proceedings", "existing_proceedings_not_quantified",
        "contingent_exposure_only", "not_established"),
    "material_event_assessment": ("occurred", "none_identified", "not_established"),
    "external_event_exposure_assessment": ("concentrated", "diversified", "not_established"),
    "compound_risk_assessment": (
        "material_interaction_supported", "interaction_plausible_unquantified",
        "none_supported"),
    "mitigating_capability_assessment": (
        "demonstrated", "existing_unproven", "none_identified"),
}
# A short free label, not a sentence.
LABEL_PARAMETERS = {"business_model_classification": 60}

# Values that assert something specific about THIS company. They need at least
# one linked, validated hypothesis resting on company-specific evidence or a
# deterministic calculation; management statements and generic risk-factor
# language are not enough.
REQUIRES_COMPANY_EVIDENCE: dict[str, frozenset[str]] = {
    "key_person_dependency_assessment": frozenset({"high"}),
    "management_quality_assessment": frozenset({"strong", "weak"}),
    "governance_concern_assessment": frozenset({"material", "severe"}),
    "switching_cost_assessment": frozenset({"low", "high"}),
    "pricing_power_assessment": frozenset({"strong"}),
    "competitive_position_assessment": frozenset({"leading", "strong"}),
    "acquisition_strategy_assessment": frozenset({"outcomes_evidenced"}),
    "revenue_recurrence_assessment": frozenset({"contractually_committed"}),
    "demand_defensiveness_assessment": frozenset({"demonstrated_resilient"}),
    "customer_dependence_assessment": frozenset({"concentrated_counterparties"}),
    "workforce_cost_resilience_assessment": frozenset({"demonstrated"}),
    "mitigating_capability_assessment": frozenset({"demonstrated"}),
    "litigation_risk_assessment": frozenset({"existing_material_proceedings"}),
    "material_event_assessment": frozenset({"occurred"}),
}

# Compatibility map for results recorded under the v1 roster and for sector
# configurations that name v1 agents.
BO_V1_TO_V2_AGENT = {
    "business_model": "bo_business_model_revenue",
    "customer_supplier_contract": "bo_customers_contracts",
    "competition_pricing": "bo_competition_pricing",
    "ma_capex_execution": "bo_operations_management",
    "regulatory_material_events": "bo_regulation_external",
}

# Memo sections, in order; each narrow agent feeds exactly one.
BO_SECTIONS = tuple((spec.agent_id, spec.code, spec.title) for spec in BO_AGENT_SPECS)
