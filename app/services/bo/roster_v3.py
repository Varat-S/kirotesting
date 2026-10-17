"""Business Overview roster v3: four internal modules, exclusive ownership.

Pure data (no I/O, no LLM). Specification: ``BO_ARCHITECTURE_V3_SPEC.md``.

* Module 1 has five primary specialists (M1S1-M1S5) and Agent 6 (M1A6).
* Modules 2-4 are declared as ownership contracts only (``executable=False``):
  their agents own parameters so ownership stays unique, but they are never run
  and their parameters are reported ``not_assessed``.
* Parameter ids are those of v2. Every ownership move is listed in
  ``BO_V2_TO_V3_PARAMETER_OWNER``; v1/v2 remain buildable, so records written
  under earlier rosters keep their meaning.

Retrieval queries reuse the v2 terms (renamed to the v3 agent) and add a few
for remits that v2 did not cover on their own (switching, pricing limits, cost
structure). Ownership is exclusive; evidence is not: two agents may receive the
same admitted passage.
"""

from __future__ import annotations

from dataclasses import replace

from app.schemas.agentic import Method, ParameterResult, ParameterStatus, Topic
from app.services.bo.plans import BO_AGENT_SPECS, BoAgentSpec, RetrievalQuery, _q
from app.services.bo.sectors import CoverageCheck

ROSTER_V3_VERSION = "bo-roster-3.0.0"

_V2_QUERIES: dict[str, RetrievalQuery] = {
    q.query_id: q for spec in BO_AGENT_SPECS for q in spec.plan
}


def _reuse(prefix: str, query_id: str, *, purpose: str | None = None) -> RetrievalQuery:
    """A v2 query under a v3 id (terms unchanged)."""
    source = _V2_QUERIES[query_id]
    return replace(source, query_id=f"{prefix}.{query_id.split('.', 1)[1]}",
                   purpose=purpose or source.purpose)


M1_REVENUE_QUALITY = BoAgentSpec(
    "M1S1", "m1_revenue_quality", "Revenue Quality & Demand Defensiveness",
    ("business_model_classification", "revenue_recurrence_assessment",
     "demand_defensiveness_assessment", "cyclicality_seasonality_assessment"),
    tuple(_reuse("m1s1", q) for q in (
        "bo1.revenue_model", "bo1.demand_resilience", "bo1.demand_fragility",
        "bo1.stabilisers", "bo1.stabiliser_limits")),
    indicator_groups=("growth", "segment", "payer", "capital"),
    dimension="revenue_quality_demand_defensiveness", validator_id="validator_1",
)

M1_CUSTOMER_STICKINESS = BoAgentSpec(
    "M1S2", "m1_customer_stickiness", "Customer Stickiness & Concentration",
    ("customer_dependence_assessment", "contract_termination_risk_assessment",
     "switching_cost_assessment"),
    (
        _reuse("m1s2", "bo2.customer_payer_mix"),
        _reuse("m1s2", "bo2.contract_terms"),
        _reuse("m1s2", "bo2.contract_limits"),
        _q("m1s2.switching", "support", r"switching", r"retention", r"narrow networks?",
           r"out-of-network", r"relationships? with", r"loyal"),
        _q("m1s2.stickiness_limits", "contradict", r"shift\w* (?:to|away)",
           r"alternative (?:sites?|providers?)", r"lose\w* (?:a )?(?:significant|major)",
           r"exclu\w* from (?:networks?|plans?)"),
        _q("m1s2.offsets", "mitigant", r"diversif\w* (?:payer|customer)",
           r"long-term (?:contracts?|agreements?)", r"multi-?year"),
        _q("m1s2.offset_limits", "mitigant_challenge", r"concentrat",
           r"significant portion", r"depend\w* (?:up)?on"),
        # Context only: geographic exposure is owned by M2. M1S2 may use it to
        # describe where its customers are, never as evidence of stickiness.
        _q("m1s2.geographic_context", "support", r"geographic\w* concentrat",
           r"states? of", k=2),
    ),
    indicator_groups=("payer", "growth", "geography"),
    dimension="customer_stickiness_concentration", validator_id="validator_2",
)

M1_PRICING_POWER = BoAgentSpec(
    "M1S3", "m1_pricing_power", "Pricing Power & Revenue Realisation",
    ("pricing_power_assessment", "revenue_realisation_assessment"),
    (
        _reuse("m1s3", "bo3.pricing"),
        _reuse("m1s3", "bo2.realisation"),
        _q("m1s3.pricing_limits", "contradict", r"price transparency",
           r"reimbursement (?:cuts?|reductions?)", r"rate (?:reductions?|cuts?)",
           r"site-neutral", r"pricing pressure"),
        _q("m1s3.realisation_offsets", "mitigant", r"supplemental payments?",
           r"collection (?:efforts|procedures)", r"eligibility"),
        _reuse("m1s3", "bo3.defence_limits"),
    ),
    indicator_groups=("payer", "realisation", "growth"),
    dimension="pricing_power_revenue_realisation", validator_id="validator_3",
)

M1_OPERATING_MODEL = BoAgentSpec(
    "M1S4", "m1_operating_model", "Operating Model & Cost Resilience",
    ("workforce_cost_resilience_assessment", "supplier_dependence_assessment"),
    (
        _q("m1s4.cost_structure", "support", r"fixed costs?", r"operating leverage",
           r"salaries", r"supplies", r"utili[sz]ation", r"capacity"),
        _reuse("m1s4", "bo4.workforce"),
        _reuse("m1s4", "bo4.workforce_initiatives"),
        _reuse("m1s4", "bo4.initiative_limits"),
        _reuse("m1s4", "bo4.procurement"),
        _reuse("m1s4", "bo2.suppliers"),
    ),
    indicator_groups=("workforce", "margin"),
    dimension="operating_model_cost_resilience", validator_id="validator_4",
)

M1_COMPETITIVE_DURABILITY = BoAgentSpec(
    "M1S5", "m1_competitive_durability", "Competitive Durability",
    ("competitive_position_assessment", "competitive_pressure_assessment",
     "barriers_to_entry_assessment"),
    tuple(_reuse("m1s5", q) for q in (
        "bo3.position", "bo3.threats", "bo3.defences", "bo3.defence_limits")),
    indicator_groups=("growth", "payer", "segment"),
    dimension="competitive_durability", validator_id="validator_5",
)

M1_AGENT_6 = BoAgentSpec(
    "M1A6", "m1_resilience_integration", "Business Resilience, Risk Interactions & Synthesis",
    ("compound_risk_assessment", "mitigating_capability_assessment"),
    tuple(_reuse("m1a6", q) for q in (
        "bo6.interactions", "bo6.capabilities", "bo6.capability_limits")),
    indicator_groups=("growth", "payer", "workforce", "realisation", "geography"),
    dimension="overall_business_resilience", validator_id="agent6_governance",
)

M2_INDUSTRY = BoAgentSpec(
    "M2", "m2_industry", "Industry Information",
    ("regulatory_dependency_assessment", "external_event_exposure_assessment"),
    (), module="M2", dimension="industry", executable=False, validator_id="validator_6",
)

M3_CORPORATE_MANAGEMENT = BoAgentSpec(
    "M3", "m3_corporate_management", "Corporate Structure & Senior Management",
    ("management_quality_assessment", "key_person_dependency_assessment",
     "governance_concern_assessment", "litigation_risk_assessment",
     "material_event_assessment"),
    (), module="M3", dimension="corporate_structure_management", executable=False,
    validator_id="validator_7",
)

M4_STRATEGY_CAPITAL = BoAgentSpec(
    "M4", "m4_strategy_capital", "Strategy & Capital Allocation / Financial Stewardship",
    ("acquisition_strategy_assessment", "integration_risk_assessment",
     "capex_strategy_assessment"),
    (), module="M4", dimension="strategy_capital_allocation", executable=False,
    validator_id="validator_8",
)

V3_SPECS: tuple[BoAgentSpec, ...] = (
    M1_REVENUE_QUALITY, M1_CUSTOMER_STICKINESS, M1_PRICING_POWER,
    M1_OPERATING_MODEL, M1_COMPETITIVE_DURABILITY, M1_AGENT_6,
    M2_INDUSTRY, M3_CORPORATE_MANAGEMENT, M4_STRATEGY_CAPITAL,
)
V3_SPEC_BY_AGENT = {s.agent_id: s for s in V3_SPECS}
V3_SPEC_BY_CODE = {s.code: s for s in V3_SPECS}
V3_M1_PRIMARY_IDS = tuple(s.agent_id for s in V3_SPECS[:5])
V3_AGENT_6_ID = M1_AGENT_6.agent_id
V3_EXECUTABLE_IDS = tuple(s.agent_id for s in V3_SPECS if s.executable)
V3_NOT_EXECUTED_IDS = tuple(s.agent_id for s in V3_SPECS if not s.executable)
# The eight primary analytical agents, each with its own validator.
V3_PRIMARY_AGENT_IDS = (*V3_M1_PRIMARY_IDS, *V3_NOT_EXECUTED_IDS)

# parameter_id -> (v2 owner, v3 owner). Every v2 Business parameter appears once.
BO_V2_TO_V3_PARAMETER_OWNER: dict[str, tuple[str, str]] = {
    p: (v2.agent_id, v3.agent_id)
    for v3 in V3_SPECS for p in v3.owned_parameters
    for v2 in BO_AGENT_SPECS if p in v2.owned_parameters
}

# Review notes that travel with the ownership decision (17 Oct 2026).
OWNERSHIP_NOTES: dict[str, str] = {
    "litigation_risk_assessment":
        "Owned by M3 subject to a future review of legal-risk ownership.",
    "material_event_assessment":
        "M3 assesses company-specific events; cross-module synthesis assesses "
        "their wider interactions.",
    "external_event_exposure_assessment":
        "Owned by M2. Geographic concentration evidence may be consumed by M1S2 "
        "as context, never as evidence of customer stickiness.",
    "supplier_dependence_assessment": "Moved from BO2 to Operating Model (M1S4).",
}

# Coverage questions: which v3 agent must answer each sector check. Checks not
# listed (for example case-specific ones) follow the v2 -> v3 code map.
V2_CODE_TO_V3_CODE = {"BO1": "M1S1", "BO2": "M1S2", "BO3": "M1S5", "BO4": "M1S4",
                      "BO5": "M2", "BO6": "M1A6"}
V3_CHECK_ASSIGNMENT: dict[str, tuple[str, ...]] = {
    "hc.payer_mix": ("M1S2",),
    "hc.contract_rates_vs_volumes": ("M1S1", "M1S2"),
    "hc.unit_revenue_vs_pricing": ("M1S3",),
    "hc.revenue_realisation": ("M1S3",),
    "hc.operating_entities": ("M3",),
    "hc.workforce": ("M1S4",),
    "hc.geography": ("M2",),
    "hc.local_competition": ("M1S5",),
}


def v3_coverage_checks(checks) -> list[CoverageCheck]:
    """Re-address coverage checks written for v2 agent codes to v3 codes."""
    out = []
    for check in checks:
        codes = V3_CHECK_ASSIGNMENT.get(check.check_id) or tuple(dict.fromkeys(
            V2_CODE_TO_V3_CODE.get(c, c) for c in check.agent_codes))
        out.append(replace(check, agent_codes=tuple(codes)))
    return out


def not_assessed_parameter_results(analysis_run_id: str) -> list[ParameterResult]:
    """One ``not_assessed`` result per parameter owned by a not-executed module.

    No value, no risk signal, no materiality: the absence of an assessment is
    never presented as a neutral or favourable finding.
    """
    results = []
    for spec in V3_SPECS:
        if spec.executable:
            continue
        for parameter_id in spec.owned_parameters:
            results.append(ParameterResult(
                parameter_result_id=f"{analysis_run_id}:{parameter_id}:not_assessed",
                analysis_run_id=analysis_run_id,
                parameter_id=parameter_id,
                topic=Topic.BUSINESS,
                value=None,
                value_type="category",
                method=Method.LLM,
                status=ParameterStatus.NOT_ASSESSED,
                agent_id=spec.agent_id,
                missing_information=[
                    f"{spec.title} ({spec.module}) is declared but not implemented; "
                    "this parameter has not been assessed."],
                notes=OWNERSHIP_NOTES.get(parameter_id),
            ))
    return results
