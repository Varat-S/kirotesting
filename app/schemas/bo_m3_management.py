"""Additive, evidence-bearing senior-management stability contract.

Dates retain their disclosed precision (YYYY, YYYY-MM or YYYY-MM-DD). Derived
tenure, transition types and completeness states are authored by the validator,
never trusted merely because they appeared in model JSON.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MANAGEMENT_STABILITY_VERSION = "bo-m3-management-stability-1.0"
CONTINUITY_DIMENSIONS = {"leadership_continuity", "relevant_experience", "transition_risk"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagementSource(_Strict):
    evidence_id: str
    quote: str


class ExecutiveFact(_Strict):
    fact_id: str
    kind: Literal[
        "current_role", "appointment", "company_join", "prior_internal_role",
        "external_role", "external_appointment", "no_prior_company_experience",
        "departure", "planned_succession", "internal_promotion", "role_expansion", "transition_risk",
    ]
    value: str
    date: str | None = None
    end_date: str | None = None
    source: ManagementSource
    verification_status: Literal["verified", "unverified"] = "unverified"
    validation_reasons: list[str] = Field(default_factory=list)



class ExecutiveProfile(_Strict):
    executive_id: str
    name: str
    current_role: str
    facts: list[ExecutiveFact] = Field(default_factory=list)
    # Derived, optional for historic/unaudited proposals; always recomputed.
    prior_company_experience: Literal["established", "explicitly_absent", "unknown", "disputed"] = "unknown"
    transition_type: Literal["internal_promotion", "internal_succession", "external_appointment", "role_expansion", "unknown"] = "unknown"
    recent_appointment: Literal["yes", "no", "unknown"] = "unknown"
    tenure: dict[str, int | str] = Field(default_factory=dict)
    company_tenure: dict[str, int | str] = Field(default_factory=dict)
    validation_reasons: list[str] = Field(default_factory=list)


class ManagementStability(_Strict):
    schema_version: Literal["bo-m3-management-stability-1.0"] = MANAGEMENT_STABILITY_VERSION
    as_of_date: str | None = None
    as_of_source: ManagementSource | None = None
    recent_change_window_years: int = Field(default=3, ge=1)
    review_scope: str
    history_start: str | None = None
    history_end: str | None = None
    history_source: ManagementSource | None = None
    executives: list[ExecutiveProfile] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    history_coverage: Literal["complete", "partial"] = "partial"
    change_history_status: Literal["changes_established", "no_changes_established", "insufficient_information"] = "insufficient_information"
    validation_reasons: list[str] = Field(default_factory=list)
    source_metadata: dict[str, dict] = Field(default_factory=dict)


def stability_json_schema():
    """Inline the small typed contract for the existing plain-schema registry."""
    schema = ManagementStability.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, list):
            return [inline(value) for value in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return inline(deepcopy(definitions[node["$ref"].split("/")[-1]]))
        result = {key: inline(value) for key, value in node.items() if key not in {"title", "default"}}
        if "const" in result:
            result["enum"] = [result.pop("const")]
        if "anyOf" in result and len(result["anyOf"]) == 2:
            variants = result["anyOf"]
            if any(v == {"type": "null"} for v in variants):
                other = next(v for v in variants if v != {"type": "null"})
                if other.get("type") in {"string", "integer", "object"}:
                    result.pop("anyOf")
                    result.update(other)
                    result["type"] = [other["type"], "null"]
        return result

    return inline(schema)
