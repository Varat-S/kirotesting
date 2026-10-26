"""Module 3-specific, Vertex-only response-schema adaptation (FEAT-002).

The Module 3 request was rejected with HTTP 400 (INVALID_ARGUMENT).
Schema complexity is the leading, unproven explanation: the canonical
``BO_M3_JSON_SCHEMA`` is substantially broader than the
narrow-specialist schema that Vertex accepts, and ``strict_schema`` makes it
worse by force-promoting EVERY property to ``required`` and stamping
``additionalProperties: False`` on every object.

This module builds a *smaller* schema to ADVERTISE to Vertex for M3 requests
only, without touching the canonical schema that ``LLMClient._validate_raw``
and Validator 7 use for authoritative local validation. The transform is pure:
it deep-copies its input and NEVER mutates it.

Baseline reduction plus a targeted projection:

* **Stage 1 — preserve-authored-required.** Walk every object node; drop
  ``default``; set ``additionalProperties = False``; and set ``required`` to the
  node's EXISTING authored ``required`` list (instead of
  ``strict_schema``'s "all properties"). This removes the
  forced required fields that ``strict_schema`` introduces, so the
  advertised schema requires only what the authored contract requires.

* **Stage 2 — breadth reduction (M3 only).** Five required
  ``m3_assessment`` array subtrees are the bulk of the schema's breadth. For the
  provider only, their ``items`` are relaxed to a loose
  ``{"type": "object", "additionalProperties": True}`` — the array keys and the
  ``m3_assessment`` ``required`` section list are kept intact. The canonical
  schema still validates these subtrees in full locally; a response that
  satisfies the loose provider items need NOT satisfy the canonical schema.
  The omitted item contracts are supplied as generated instructions, and the
  full canonical schema still gates every response. No missing data is filled.

* **Stage 3 — typed core (v1.5 baseline).** Restore canonical-derived
  executive, fact and source shapes, plus essential management/structural
  reference fields. Leave the other broad item contracts open. This is not the
  full strict schema. It was accepted live but permits omitted finding fields.

Version 1.4 is the live-accepted, open-shape compatibility baseline, retained as
variant ``compact``. The live-accepted v1.5 (``typed_core``) adds only
canonical-derived critical nested types. These historical schemas remain
unchanged for replay. The aligned standalone draft/codec lives in
``app.services.bo.m3_contract``; other variants are never automatic fallbacks.

The provider schema additionally requires the top-level ``m3_assessment`` and
explicit parameter ``evidence_ids`` / ``hypothesis_ids`` arrays (which may be
empty for honest unavailable classifications);
the shared canonical envelope keeps its existing optionality for compatibility.

Kept deliberately dependency-light: it imports nothing from ``bo_prompts`` and
only needs the standard library.
"""

from __future__ import annotations

import copy
import json
from typing import Any

VERTEX_M3_SCHEMA_ADAPTER_VERSION = "vertex-m3-1.5"

# Five required m3_assessment array subtrees. Relaxing their item
# schemas (provider-side only) removes the bulk of the schema breadth that the
# narrow-specialist schema does not carry. The array KEYS and the m3_assessment
# ``required`` list are preserved; only the per-item shape is loosened.
_RELAXED_M3_ARRAYS: tuple[str, ...] = (
    "legal_entities",
    "ownership_relationships",
    "cash_flow_locations",
    "cash_transfer_relationships",
    "management_assessments",
)

# The loose item schema advertised to the provider for the relaxed arrays.
_LOOSE_ITEM: dict[str, Any] = {"type": "object", "additionalProperties": True}
STRUCTURAL_EXPLICIT_FIELDS = (
    "analytical_dimension", "structural_observation", "referenced_node_ids", "referenced_link_ids",
    "onboarding_source_reference", "credit_direction", "assessment_availability", "qualifications", "missing_information",
)


def is_m3_request(request: Any) -> bool:
    """True iff ``request`` targets the Module 3 corporate-management specialist.

    Detected either by the request method (``m3_corporate_management``) or,
    defensively, by the presence of the ``m3_assessment`` property in the
    prompt's response schema. Any other request (NARROW specialists, the
    orchestrator, the challenger, Modules 1/2/4) returns ``False``.
    """
    if getattr(request, "method", None) == "m3_corporate_management":
        return True
    prompt = getattr(request, "prompt", None)
    schema = getattr(prompt, "response_schema", None)
    if isinstance(schema, dict):
        properties = schema.get("properties") or {}
        return "m3_assessment" in properties
    return False


def vertex_m3_response_schema(canonical_schema: dict[str, Any], *, variant="typed_core") -> dict[str, Any]:
    """Return a provider-facing M3 schema; NEVER mutates ``canonical_schema``.

    See the module docstring for the two-stage transform. The returned schema is
    a fresh deep copy; the canonical schema is left byte-for-byte unchanged.
    """
    result = copy.deepcopy(canonical_schema)
    _preserve_authored_required(result)
    _relax_m3_arrays(result)
    if "m3_assessment" in result.get("properties", {}):
        required = result.setdefault("required", [])
        if "m3_assessment" not in required:
            required.append("m3_assessment")
        parameter = result["properties"]["parameters"]["items"]
        for key in ("evidence_ids", "hypothesis_ids"):
            if key not in parameter["required"]:
                parameter["required"].append(key)
        # Keep canonical optionality for historic/local validation. Advertise
        # explicit structural fields to new generations without forcing values
        # or making the governance array inherit the shared finding mutation.
        structural = result["properties"]["m3_assessment"]["properties"]["structural_credit_findings"]
        # Reconstruct the live-accepted open management baseline first. The
        # v1.5 candidate restores only its critical nested types below.
        if "management_stability" in result["properties"]["m3_assessment"]["properties"]:
            result["properties"]["m3_assessment"]["properties"]["management_stability"] = copy.deepcopy(_LOOSE_ITEM)
        structural["items"] = copy.deepcopy(structural["items"])
        for key in STRUCTURAL_EXPLICIT_FIELDS:
            if key not in structural["items"]["required"]:
                structural["items"]["required"].append(key)
        if variant in {"compact", "fact_core", "typed_core"}:
            # One further subtree follows the already-established loose-item
            # generation pattern. Its FULL contract is given in instructions;
            # canonical validation + Validator 7 retain every existing check.
            structural["items"] = copy.deepcopy(_LOOSE_ITEM)
        elif variant == "requirements_removed":
            structural["items"]["required"] = [k for k in structural["items"]["required"] if k not in STRUCTURAL_EXPLICIT_FIELDS]
        elif variant == "without_management":
            # Offline isolation only; never selected by the production backend.
            result["properties"]["m3_assessment"]["properties"].pop("management_stability", None)
        elif variant != "v1_3":
            raise ValueError("Unknown M3 schema diagnostic variant")
        if variant in {"fact_core", "typed_core"}:
            _enforce_typed_core(result, canonical_schema, references=variant == "typed_core")
    return result


def _project_object(canonical, fields, *, required=(), closed=False):
    """Select existing canonical field definitions; never maintain a second model.

    Open projections preserve omitted canonical fields for local validation.
    Closed projections omit only validator-owned executive/fact metadata.
    """
    # Preserve authored property order, also used by the generated instructions.
    # Fail on unknown selections rather than silently dropping a typo.
    if set(fields) - set(canonical["properties"]):
        raise ValueError("Projection references an unknown canonical property")
    ordered = [key for key in canonical["properties"] if key in fields]
    properties = {key: copy.deepcopy(canonical["properties"][key]) for key in ordered}
    needed = set(canonical.get("required", [])) | set(required)
    return {"type": "object", "properties": properties,
            "required": [key for key in ordered if key in needed], "additionalProperties": not closed}


def _enforce_typed_core(result, canonical_schema, *, references):
    original = canonical_schema["properties"]["m3_assessment"]["properties"]
    target = result["properties"]["m3_assessment"]
    stability = original["management_stability"]
    profile = stability["properties"]["executives"]["items"]
    fact = profile["properties"]["facts"]["items"]
    # Depth is confined to the indispensable executive -> facts -> source path.
    fact_core = _project_object(fact, ("fact_id", "kind", "value", "date", "end_date", "source"), closed=True)
    fact_core["properties"]["source"] = _project_object(fact["properties"]["source"], ("evidence_id", "quote"), closed=True)
    profile_core = _project_object(profile, ("executive_id", "name", "current_role", "facts"), required=("facts",), closed=True)
    profile_core["properties"]["facts"]["items"] = fact_core
    core = _project_object(stability, ("schema_version", "review_scope", "as_of_date", "as_of_source", "executives"),
                           required=("schema_version", "executives"))
    core["properties"]["executives"]["items"] = profile_core
    target["properties"]["management_stability"] = core
    if "management_stability" not in target["required"]:
        target["required"].append("management_stability")
    # New generation targets the current canonical assessment; local replay
    # still accepts every historical version without migration or repair.
    target["properties"]["schema_version"]["enum"] = [original["schema_version"]["enum"][-1]]
    if references:
        management = original["management_assessments"]["items"]
        target["properties"]["management_assessments"]["items"] = _project_object(management, (
            "observation_id", "management_id", "legal_name", "role", "hypothesis", "credit_mechanism",
            "executive_ids", "fact_ids", "credit_direction"), required=("executive_ids", "fact_ids", "credit_direction"))
        structural = original["structural_credit_findings"]["items"]
        target["properties"]["structural_credit_findings"]["items"] = _project_object(structural, (
            "finding_id", "hypothesis_id", "credit_hypothesis", "causal_mechanism", "referenced_node_ids", "referenced_link_ids"),
            required=("referenced_node_ids", "referenced_link_ids"))


def vertex_m3_output_instructions(canonical_schema: dict[str, Any], *, variant="typed_core") -> str:
    """Describe ONLY the item contracts omitted from the provider schema.

    Derived from the authoritative contract, not hand-maintained examples or
    invented values. Field ordering matches the canonical/provider sections.
    These instructions guide generation; they never replace local validation.
    """
    properties = canonical_schema["properties"]["m3_assessment"]["properties"]
    compact = variant in {"compact", "fact_core", "typed_core"}
    typed = variant in {"fact_core", "typed_core"}
    lines = [
        f"M3 output contract: return m3_assessment with all {len(properties) if compact else 'eleven'} sections. "
        f"The {'six' if compact else 'five'} arrays below have {'fully or partially open' if typed else 'open'} item shapes in the provider schema "
        "to reduce decoding complexity. Every emitted item must follow these "
        "field contracts (required means the key must be present).",
        "Use admitted evidence and validated structural facts only. Do not "
        "invent entities, analytical values, evidence IDs or quotes. Use null "
        "only where allowed. Use empty arrays only when the evidence supports "
        "no items, and state missing evidence in information_gaps or "
        "missing_information; do not omit supported management or structural "
        "analysis to satisfy the format. Do not treat a simulated structure "
        "as proof of actual guarantees or cash access. Full local canonical "
        "validation and Validator 7 still apply; no defaults will repair output.",
    ]
    for section, array in properties.items():
        relaxed = (*_RELAXED_M3_ARRAYS, "structural_credit_findings") if compact else _RELAXED_M3_ARRAYS
        if section not in relaxed:
            continue
        item = array["items"]
        required = item.get("required", [])
        fields = []
        for name, field in item["properties"].items():
            presence = "required" if name in required else "optional"
            fields.append(f"{name} ({presence}): {_field_contract(field)}")
        lines.append(f"m3_assessment.{section}[]: " + "; ".join(fields) + ".")
    if "management_stability" in properties:
        if typed:
            enforced = set(vertex_m3_response_schema(canonical_schema, variant=variant)["properties"]["m3_assessment"]
                           ["properties"]["management_stability"]["properties"])
            omitted = {k: v for k, v in properties["management_stability"]["properties"].items()
                       if k not in enforced and k not in {"validation_reasons", "source_metadata"}}
            lines.append("management_stability is required for the current assessment. Follow the provider's "
                "typed executive/fact/source schema; facts are objects and fact_ids are references. Do not emit "
                "validator-owned profile/fact metadata. The remaining optional management fields are governed "
                "locally by these canonical definitions: " + json.dumps(omitted, separators=(",", ":")))
        else:
            lines.append("For M3 version 1.2, management_stability is required. Its exact local JSON contract is: " +
                         json.dumps(properties["management_stability"], separators=(",", ":")))
    if variant == "compact":
        profile = executive_fact_example()
        lines.append("Atomic facts versus references: management_stability.executives[].facts is an array "
                     "of COMPLETE OBJECTS, NEVER strings or fact IDs. Each object's fact_id defines a fact. "
                     "Only management_assessments[].fact_ids contains strings referencing those existing objects. "
                     "The same fact_id must resolve to a fact in the referenced executive's facts array. "
                     "Schema-accurate profile TEMPLATE (placeholders are NOT company facts or evidence; "
                     "replace every placeholder from admitted evidence): " + json.dumps(profile, separators=(",", ":")) +
                     '. A finding referencing this example would use fact_ids=["FACT_ID_DEFINED_ABOVE"]. '
                     "Never emit derived verification fields as proof, convert IDs to invented facts, or omit "
                     "supported officers merely to simplify the output.")
    if compact:
        lines.append("For each structural_credit_findings item explicitly supply " + ", ".join(STRUCTURAL_EXPLICIT_FIELDS) +
                     ". When citing multiple ownership links with different stakes, bind each asserted percentage "
                     "in structural_observation as link:<parent_id>-><child_id>=percentage%. "
                     "Empty/null values must reflect genuine gaps. No field, legal fact or graph reference will be repaired locally.")
    return "\n".join(lines)


def executive_fact_example():
    """A typed profile template only; no invented biography or admissible quote."""
    return {"executive_id": "EXECUTIVE_ID", "name": "NAME_FROM_ROSTER", "current_role": "DISCLOSED_ROLE",
        "facts": [{"fact_id": "FACT_ID_DEFINED_ABOVE", "kind": "appointment", "value": "DISCLOSED_ROLE",
            "date": "2024-05", "end_date": None, "source": {
                "evidence_id": "ADMITTED_EVIDENCE_ID", "quote": "ACTUAL_CONTIGUOUS_ADMITTED_QUOTATION"}}]}


def _field_contract(field: dict[str, Any]) -> str:
    kind = field["type"]
    label = "|".join(kind) if isinstance(kind, list) else kind
    if kind == "array":
        label += " of " + _field_contract(field["items"])
    if "enum" in field:
        label += " allowed=" + json.dumps(field["enum"], separators=(",", ":"))
    return label


def _preserve_authored_required(node: Any) -> None:
    """Stage 1: recursively drop defaults, close objects, keep AUTHORED required."""
    if isinstance(node, dict):
        node.pop("default", None)
        if node.get("type") == "object":
            node["additionalProperties"] = False
            # Preserve the node's own authored required list rather than forcing
            # every property to be required (what ``strict_schema`` does).
            if "required" in node:
                node["required"] = list(node["required"])
        for value in list(node.values()):
            _preserve_authored_required(value)
    elif isinstance(node, list):
        for value in node:
            _preserve_authored_required(value)


def _relax_m3_arrays(schema: dict[str, Any]) -> None:
    """Stage 2 (M3 only): loosen five required arrays' item schemas."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    m3 = properties.get("m3_assessment")
    if not isinstance(m3, dict):
        return
    m3_properties = m3.get("properties")
    if not isinstance(m3_properties, dict):
        return
    for key in _RELAXED_M3_ARRAYS:
        array_node = m3_properties.get(key)
        if isinstance(array_node, dict) and array_node.get("type") == "array":
            array_node["items"] = copy.deepcopy(_LOOSE_ITEM)
