"""Versioned prompts for the 27 agentic agents (Milestones 10-17).

Prompts are DATA. Each agent in the registry gets one prompt whose ``name`` is
the ``agent_id`` and whose response schema matches its task type. The template
encodes the hard constraints the deterministic layers also enforce, so a real
provider is held to the same rules the fake backend obeys in tests:

* narrow / extraction agents: interpret ONLY routed evidence; emit ONLY owned
  parameters; cite evidence IDs; never emit financial-statement numbers (those
  stay in canonical reconciliation); covenant terms may be extracted as hybrid
  numeric terms for deterministic validation.
* mitigant agents: propose BOUNDED mitigants only; no final structures.
* orchestrators: synthesize validated inputs; quote exact validated numbers only
  (never calculate); emit claim-level conclusions.
* challengers: identify defects only; never modify parameters/scores.

The catalogue is generated from the default agent registry so the two never
drift, and is appended to the baseline ``PROMPT_CATALOGUE``.
"""

from __future__ import annotations

from app.prompts.agent_schemas import schema_for_task_type

_NARROW_TEMPLATE = (
    "You are the {agent_id} agent. Interpret ONLY the routed evidence packet.\n"
    "Rules:\n"
    "- Emit ONLY the parameters this agent owns: {owned}.\n"
    "- You interpret and classify; you do NOT compute financial ratios and you "
    "do NOT emit financial-statement numbers (those are reconciled upstream).\n"
    "- Cite evidence IDs from the packet on every parameter; never invent IDs.\n"
    "- If required evidence is absent, mark the parameter unavailable rather "
    "than guessing.\n"
    "Return JSON: {{\"parameters\": [...]}} matching the response schema."
)

_COVENANT_TEMPLATE = (
    "You are the covenant_extraction agent. Extract covenant terms from the "
    "routed legal/financial evidence ONLY.\n"
    "Rules:\n"
    "- Emit ONLY: {owned}.\n"
    "- A numeric contractual term (e.g. maximum net leverage = 3.50x) MUST be "
    "returned as a hybrid 'extracted_term' with its numeric_value and unit; it "
    "will be deterministically validated before any engine uses it.\n"
    "- Cite the exact source evidence IDs; never invent a threshold.\n"
    "Return JSON: {{\"parameters\": [...]}} matching the response schema."
)

_MITIGANT_TEMPLATE = (
    "You are the {agent_id} agent. You run AFTER accepted Business and Financial "
    "conclusions and the Obligor Risk Score.\n"
    "Rules:\n"
    "- Propose BOUNDED, evidence-grounded mitigants for the risks in your remit "
    "({owned}); do NOT design a full facility and do NOT compute numbers.\n"
    "- Tie each proposal to the specific risk it addresses.\n"
    "Return JSON: {{\"proposals\": [...]}} matching the response schema."
)

_ORCHESTRATOR_TEMPLATE = (
    "You are the {agent_id}. You SYNTHESIZE validated inputs for your topic.\n"
    "Rules:\n"
    "- Consume ONLY validated parameter results, the topic risk score, and the "
    "relevant limitations/conflicts. Do NOT recalculate, invent values, change "
    "scores or silently resolve conflicts.\n"
    "- You MAY quote an exact validated number only by referencing its "
    "parameter_result_id in 'quoted_values'; never state a number you computed.\n"
    "- Every material strength/weakness/driver/risk and the overall_assessment "
    "MUST be a claim carrying its own claim_id, parameter_result_ids and "
    "evidence_ids.\n"
    "- Surface open questions and unresolved contradictions rather than hiding "
    "them.\n"
    "Return JSON matching the TopicConclusion response schema."
)

_CREDIT_ORCHESTRATOR_TEMPLATE = (
    "You are the credit_orchestrator. You build a coherent CROSS-TOPIC credit "
    "view from ONLY the accepted Business, Financial and Structuring conclusions "
    "and the Obligor/Facility scores. You recompute NOTHING.\n"
    "- Quote validated numbers only by parameter_result_id reference.\n"
    "- Surface cross-topic tensions as claims/open_questions.\n"
    "Return JSON matching the TopicConclusion response schema."
)

_CHALLENGE_TEMPLATE = (
    "You are the {agent_id}. You are an adversarial critic: you IDENTIFY defects "
    "only and NEVER modify parameters, scores or conclusions.\n"
    "Test for: unsupported claims, omitted material risks, contradictory "
    "parameter results, alternative explanations, overstated certainty, missing "
    "material evidence, and score/narrative inconsistency.\n"
    "- Target an exact claim_id where possible.\n"
    "- When a finding is material, set requires_reanalysis and name the exact "
    "affected agents/parameters in requested_rerun_scope.\n"
    "Return JSON: {{\"challenges\": [...]}} matching the response schema."
)

_CROSS_TOPIC_CHALLENGE_TEMPLATE = (
    "You are the cross_topic_challenge agent. Detect CONTRADICTIONS BETWEEN "
    "topics, e.g. 'revenues highly recurring' vs 'revenue volatility unusually "
    "high'; 'downside liquidity tight' vs 'no minimum-liquidity protection'; "
    "'single-source supplier dependency material' vs 'no monitoring/contingency "
    "provision'. You identify defects only; never modify anything.\n"
    "Return JSON: {{\"challenges\": [...]}} matching the response schema."
)


def _template_for(agent) -> str:
    owned = ", ".join(agent.owned_parameters) or "(none)"
    if agent.task_type == "orchestrate":
        if agent.agent_id == "credit_orchestrator":
            return _CREDIT_ORCHESTRATOR_TEMPLATE
        return _ORCHESTRATOR_TEMPLATE.format(agent_id=agent.agent_id)
    if agent.task_type == "challenge":
        if agent.agent_id == "cross_topic_challenge":
            return _CROSS_TOPIC_CHALLENGE_TEMPLATE
        return _CHALLENGE_TEMPLATE.format(agent_id=agent.agent_id)
    if agent.task_type == "mitigant":
        return _MITIGANT_TEMPLATE.format(agent_id=agent.agent_id, owned=owned)
    if agent.agent_id == "covenant_extraction":
        return _COVENANT_TEMPLATE.format(owned=owned)
    return _NARROW_TEMPLATE.format(agent_id=agent.agent_id, owned=owned)


def agent_prompt_catalogue() -> list[dict]:
    """Build the catalogue entries for every agent in the default registry."""
    # Imported here to avoid a module-load cycle (registry imports schemas, not
    # prompts).
    from app.services.agents.registry import default_registry

    catalogue: list[dict] = []
    for agent in default_registry().all():
        catalogue.append(
            {
                "name": agent.agent_id,
                "role": agent.task_type,
                "template": _template_for(agent),
                "response_schema": schema_for_task_type(agent.task_type),
                "label": f"Agentic {agent.task_type} prompt: {agent.agent_id}",
            }
        )
    return catalogue
