"""Response JSON Schemas for the agentic agents (Milestones 10-17).

Each agent's raw output is validated against a FIXED schema before use (same
discipline as the baseline LLM prompts). Four shapes cover the roster:

* NARROW / EXTRACTION agents emit ``{"parameters": [AgentParameter...]}`` — a
  bounded list of the parameters the agent owns, each an LLM/hybrid observation
  that still has to pass deterministic validation before it becomes an official
  ParameterResult. Numeric financial-statement values are NEVER emitted here;
  only interpretations, classifications and (for covenants) extracted contractual
  terms tagged for hybrid validation.
* MITIGANT agents emit ``{"proposals": [MitigantProposal...]}`` — bounded
  risk-to-mitigant suggestions, not final structures.
* ORCHESTRATORS emit a ``TopicConclusion`` of typed ConclusionClaims.
* CHALLENGERS emit ``{"challenges": [ChallengeFinding...]}``.

Schemas are intentionally permissive on free-text fields and strict on the
structural contract (ids, evidence refs, enums) so the deterministic validation
layer (M8) does the real gating.
"""

from __future__ import annotations

from typing import Any

# Status values an agent may emit MUST match the application ParameterStatus
# enum (item 4). An unknown status fails schema validation; it never silently
# becomes OK. ``proposed_new_calculation`` is deterministic-only and is NOT an
# allowed agent status.
AGENT_PARAMETER_STATUSES = [
    "ok", "provisional", "unavailable", "not_applicable", "requires_review",
]

# A single parameter observation emitted by a narrow/extraction agent.
_AGENT_PARAMETER: dict[str, Any] = {
    "type": "object",
    "required": ["parameter_id", "value_type", "status"],
    "properties": {
        "parameter_id": {"type": "string"},
        "value": {},  # any JSON type or null
        "value_type": {"type": "string"},
        "method": {"type": "string", "enum": ["llm", "hybrid"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "status": {"type": "string", "enum": AGENT_PARAMETER_STATUSES},
        "materiality": {"type": "string", "enum": ["low", "medium", "high"]},
        "notes": {"type": "string"},
        # For hybrid numeric contractual terms (e.g. max_net_leverage = 3.50x):
        "extracted_term": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "numeric_value": {"type": ["number", "null"]},
                "unit": {"type": ["string", "null"]},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}

# A structured, NON-SCORING sector observation (e.g. a medical-device risk
# dimension). Emitted only when the task input carries ``sector_context``.
SECTOR_OBSERVATION_RELEVANCE = ["relevant", "not_relevant", "not_assessable"]
SECTOR_OBSERVATION_DIRECTION = ["adverse", "neutral", "favourable", "unclear"]
SECTOR_OBSERVATION_CONFIDENCE = ["low", "medium", "high"]

_SECTOR_OBSERVATION: dict[str, Any] = {
    "type": "object",
    "required": ["dimension_id", "relevance"],
    "properties": {
        "dimension_id": {"type": "string"},
        "relevance": {"type": "string", "enum": SECTOR_OBSERVATION_RELEVANCE},
        "direction": {"type": "string", "enum": SECTOR_OBSERVATION_DIRECTION},
        "confidence": {"type": "string", "enum": SECTOR_OBSERVATION_CONFIDENCE},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "review_flag": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "additionalProperties": True,
}

NARROW_AGENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["parameters"],
    "properties": {
        "parameters": {"type": "array", "items": _AGENT_PARAMETER},
        "sector_observations": {"type": "array", "items": _SECTOR_OBSERVATION},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}

_MITIGANT_PROPOSAL: dict[str, Any] = {
    "type": "object",
    # A proposal must be structured and explicitly BOUNDED to feed candidate
    # generation (item 11). The LLM proposes; the deterministic engine evaluates.
    "required": ["parameter_id", "mitigant_type", "bounded"],
    "properties": {
        "parameter_id": {"type": "string"},
        "mitigant_type": {"type": "string"},
        "proposed_value": {"type": ["number", "string", "null"]},
        "unit": {"type": ["string", "null"]},
        "addresses_risk": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "bounded": {"type": "boolean"},
    },
    "additionalProperties": True,
}

MITIGANT_AGENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["proposals"],
    "properties": {
        "proposals": {"type": "array", "items": _MITIGANT_PROPOSAL},
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}

_CONCLUSION_CLAIM: dict[str, Any] = {
    "type": "object",
    "required": ["claim_id", "category", "text"],
    "properties": {
        "claim_id": {"type": "string"},
        "category": {
            "type": "string",
            "enum": ["strength", "weakness", "driver", "risk", "assessment",
                     "mitigant", "limitation"],
        },
        "text": {"type": "string"},
        "parameter_result_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "materiality": {"type": "string", "enum": ["low", "medium", "high"]},
        "uncertainty": {"type": ["string", "null"]},
        # A claim MAY quote exact validated numbers, bound to an EXACT accepted
        # ParameterResult id (item 6); logical-name mappings are rejected.
        "quoted_values": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["parameter_result_id", "value"],
                "properties": {
                    "parameter_result_id": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": ["string", "null"]},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}

ORCHESTRATOR_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["overall_assessment"],
    "properties": {
        "overall_assessment": _CONCLUSION_CLAIM,
        "strengths": {"type": "array", "items": _CONCLUSION_CLAIM},
        "weaknesses": {"type": "array", "items": _CONCLUSION_CLAIM},
        "key_drivers": {"type": "array", "items": _CONCLUSION_CLAIM},
        "material_risks": {"type": "array", "items": _CONCLUSION_CLAIM},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "unresolved_contradictions": {"type": "array", "items": {"type": "string"}},
        # Structuring orchestrator may name the selected feasible candidate.
        "selected_candidate_id": {"type": ["string", "null"]},
    },
    "additionalProperties": True,
}

_CHALLENGE_FINDING: dict[str, Any] = {
    "type": "object",
    "required": ["challenge_id", "issue_type", "severity", "reason"],
    "properties": {
        "challenge_id": {"type": "string"},
        "target": {"type": ["string", "null"]},
        "issue_type": {"type": "string"},
        "severity": {"type": "string",
                     "enum": ["low", "medium", "high", "material"]},
        "reason": {"type": "string"},
        "affected_agent_ids": {"type": "array", "items": {"type": "string"}},
        "affected_parameter_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "requires_reanalysis": {"type": "boolean"},
        "requested_rerun_scope": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}

CHALLENGE_AGENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["challenges"],
    "properties": {
        "challenges": {"type": "array", "items": _CHALLENGE_FINDING},
    },
    "additionalProperties": True,
}


def schema_for_task_type(task_type: str) -> dict[str, Any]:
    """Return the response schema for an agent's task type."""
    if task_type == "orchestrate":
        return ORCHESTRATOR_JSON_SCHEMA
    if task_type == "challenge":
        return CHALLENGE_AGENT_JSON_SCHEMA
    if task_type == "mitigant":
        return MITIGANT_AGENT_JSON_SCHEMA
    return NARROW_AGENT_JSON_SCHEMA  # extract / interpret / classify / narrow
