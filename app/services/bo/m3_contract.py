"""M3-only generation draft, explicit assembly and governed-output validation.

The canonical downstream contract is unchanged. Model proposals never supply
admissibility or validator-owned leadership metadata. Canonical input validation
checks the draft's model-owned projection; full canonical output validation is
performed only after Validator 7 actually produces its outcomes.
"""
from copy import deepcopy

import jsonschema

from app.core.hashing import content_hash

DRAFT_ID = "urn:bo:m3-generation-draft:1.0"
DRAFT_ADAPTER_VERSION = "vertex-m3-1.6.2"
COMPACT_ID = DRAFT_ID + ":compact"
FINDINGS = ("structural_credit_findings", "management_assessments", "governance_screening")
PROFILE_DERIVED = {"prior_company_experience", "transition_type", "recent_appointment", "tenure", "company_tenure", "validation_reasons"}
FACT_DERIVED = {"verification_status", "validation_reasons"}
STABILITY_DERIVED = {"recent_change_window_years", "history_coverage", "change_history_status", "validation_reasons", "source_metadata"}
STATUS_FIELDS = {"legal_entities": "verification_status", "ownership_relationships": "confirmation_status",
                 "cash_flow_locations": "provenance_status", "cash_transfer_relationships": "confirmation_status"}


def is_draft_schema(schema):
    return schema.get("$id") in {DRAFT_ID, COMPACT_ID}


def _label(index):
    result = ""
    while True:
        result = chr(97 + index % 26) + result
        index = index // 26 - 1
        if index < 0: return result


def compact_generation_schema():
    """Alias JSON property names only. Descriptions preserve their meaning."""
    def encode_schema(node):
        result = deepcopy(node)
        if 'properties' in node:
            labels = {key: _label(i) for i, key in enumerate(node['properties'])}
            result['properties'] = {}
            for key, value in node['properties'].items():
                field = encode_schema(value)
                field['description'] = key + (': ' + field['description'] if field.get('description') else '')
                result['properties'][labels[key]] = field
            result['required'] = [labels[key] for key in node.get('required', [])]
        if isinstance(node.get('items'), dict): result['items'] = encode_schema(node['items'])
        return result
    result = encode_schema(generation_schema())
    result['$id'] = COMPACT_ID
    return result


def transport_aliases():
    rows = []
    def walk(node, path='', wire=''):
        for i, (key, value) in enumerate(node.get('properties', {}).items()):
            child = path + '/' + key; alias = wire + '/' + _label(i)
            rows.append({'canonical_draft_path': child, 'transport_path': alias})
            walk(value, child, alias)
        if isinstance(node.get('items'), dict): walk(node['items'], path+'/[]', wire+'/[]')
    walk(generation_schema())
    return rows


def encode_transport(value):
    """Encode authored offline fixtures; production generation supplies aliases."""
    jsonschema.validate(value, generation_schema())
    def convert(value, node):
        if isinstance(value, dict):
            labels = {key: _label(i) for i, key in enumerate(node['properties'])}
            return {labels[key]: convert(item, node['properties'][key]) for key, item in value.items()}
        if isinstance(value, list): return [convert(item, node['items']) for item in value]
        return value
    return convert(value, generation_schema())


def decode_transport(value, recorded_schema=None):
    # Validate required structure/types before decoding. Semantic enums are then
    # strictly checked by the full local draft contract, without normalisation.
    schema = recorded_schema if recorded_schema is not None else compact_generation_schema()
    if schema.get('$id') != COMPACT_ID:
        raise ValueError('A recorded compact draft contract is required for decoding.')
    jsonschema.validate(value, provider_generation_schema(schema))
    def convert(value, node):
        if isinstance(value, dict):
            # Each registry-pinned field description begins with its canonical
            # name. Replay therefore needs the recorded schema, not today's
            # property order or a mutable external alias-map file.
            labels = {key: field['description'].split(': ', 1)[0] for key, field in node['properties'].items()}
            return {labels[key]: convert(item, node['properties'][key]) for key, item in value.items()}
        if isinstance(value, list): return [convert(item, node['items']) for item in value]
        return value
    return convert(value, schema)


def provider_generation_schema(local_schema=None):
    """Keep all required keys/types, reduce categorical decoding branches.

    Allowed values are still derived verbatim from the authoritative local
    schema and strictly validated before assembly. Version/fact-kind enums stay
    provider-enforced. This does not open an object or remove a required key.
    """
    schema = deepcopy(local_schema if local_schema is not None else generation_schema())
    schema.pop("$id", None)  # Registry identity is local metadata, not model data.
    def visit(node, field=""):
        if not isinstance(node, dict): return
        if "enum" in node and field not in {"schema_version", "kind"}:
            import json
            choices = node.pop("enum")
            node["description"] = node.get('description', '') + " Allowed values (strictly checked locally): " + json.dumps(choices, separators=(",", ":"))
        for key, value in node.get("properties", {}).items(): visit(value, key)
        if isinstance(node.get("items"), dict): visit(node["items"], field)
    visit(schema)
    return schema


def _omit(node, fields):
    for key in fields:
        node["properties"].pop(key, None)
    node["required"] = [k for k in node.get("required", []) if k not in fields]


def _rename(node, old, new):
    node["properties"] = {(new if k == old else k): v for k, v in node["properties"].items()}
    node["required"] = [new if k == old else k for k in node.get("required", [])]


def generation_schema(canonical=None, *, assembled=False):
    if canonical is None:
        from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
        canonical = BO_M3_JSON_SCHEMA
    result = deepcopy(canonical)
    result["$id"] = DRAFT_ID
    result["required"] = list(dict.fromkeys([*result["required"], "m3_assessment"]))
    m3 = result["properties"]["m3_assessment"]
    props = m3["properties"]
    m3["required"] = list(dict.fromkeys([*m3["required"], "management_stability"]))
    props["schema_version"]["enum"] = ["bo-m3-corporate-assessment-1.2"]
    for bucket in FINDINGS:
        # Canonical structural/governance entries share a Python dictionary.
        # A projection change must never leak into the other branch.
        item = props[bucket]["items"] = deepcopy(props[bucket]["items"])
        _omit(item, {"admissibility"})
        _rename(item, "disposition", "proposed_disposition")
        additional = (("executive_ids", "fact_ids", "credit_direction") if bucket == "management_assessments" else
            ("analytical_dimension", "structural_observation", "referenced_node_ids", "referenced_link_ids",
             "onboarding_source_reference", "credit_direction", "assessment_availability", "qualifications", "missing_information"))
        if bucket == "governance_screening":
            _omit(item, set(additional) - {"credit_direction", "qualifications", "missing_information"})
            additional = ("credit_direction",)
        item["required"] = list(dict.fromkeys([*item["required"], *additional]))
    for bucket, field in STATUS_FIELDS.items():
        _rename(props[bucket]["items"], field, "proposed_" + field)
    stability = props["management_stability"]
    _omit(stability, STABILITY_DERIVED)
    # Every factual/date/history input is explicit; unknown values must be null,
    # not reconstructed from a missing model field or completed date.
    stability["required"] = list(stability["properties"])
    profile = stability["properties"]["executives"]["items"]
    _omit(profile, PROFILE_DERIVED)
    profile["required"] = list(profile["properties"])
    fact = profile["properties"]["facts"]["items"]
    _omit(fact, FACT_DERIVED)
    fact["required"] = list(fact["properties"])
    _omit(result, {"retrieval_requests"})  # single pass has no follow-up retrieval call
    hypothesis = result["properties"]["hypotheses"]["items"]
    _omit(hypothesis, {"display_priority", "review_required", "corrects_hypothesis_id", "parameter_id"})
    # Always express citations/quotes and the model's uncertainty explicitly.
    hypothesis["required"] = list(dict.fromkeys([*hypothesis["required"], "confidence", "materiality",
        "credit_mechanism", "supporting_evidence_ids", "contradicting_evidence_ids", "evidence_quotes", "missing_information", "evidence_basis"]))
    parameter = result["properties"]["parameters"]["items"]
    parameter["required"] = list(dict.fromkeys([*parameter["required"], "value", "evidence_ids", "hypothesis_ids"]))
    if assembled:
        from app.schemas.bo_m3_management import stability_json_schema
        stability["properties"]["recent_change_window_years"] = deepcopy(stability_json_schema()["properties"]["recent_change_window_years"])
        stability["required"].append("recent_change_window_years")
    # Bound the provider surface to required fields plus existing M3 hypothesis
    # support/quotation fields and useful optional management narrative fields.
    # Optional draft inputs stay typed; no subtree is left as an open object.
    def close(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" or "object" in (node.get("type") or []):
                node["additionalProperties"] = False
            for value in node.values(): close(value)
        elif isinstance(node, list):
            for value in node: close(value)
    close(result)
    return result


def draft_template(template):
    # Output-format correction only; analytical instructions remain intact.
    return template.replace("disposition/admissibility/confidence/materiality states",
        "proposed_disposition/confidence/materiality states (findings are drafts; Validator 7 alone determines admissibility)")


def output_instructions():
    return ("M3 generation contract: return the complete typed assessment DRAFT specified by the response schema. "
        "TRANSPORT FORMAT: use ONLY the short JSON field labels in response_json_schema. Each field's description "
        "identifies its ordinary analytical field name. These short labels are decoded losslessly by the application; "
        "do not output the ordinary long field names instead. Labels are scoped to their own object, not global IDs. "
        "Every required model-owned key must be present, including nullable dates and empty evidence arrays when genuinely unavailable. "
        "Use proposed_disposition on assessment findings and proposed_verification_status/proposed_confirmation_status/"
        "proposed_provenance_status on entity/relationship drafts; these are model opinions, not validation results. "
        "Top-level hypotheses retain their existing disposition field. Do not emit admissibility, validator outcomes, "
        "derived executive tenure/transition/verification states or a review-window setting. The application supplies its configured window. "
        "Never invent dates, citations, investigation flags, confidence or materiality to fill the schema. Report genuine unknowns "
        "or limitations; an unsupported finding may be proposed unverified. All factual observations, source quotes, credit mechanisms "
        "and caveats must come from admitted evidence. Empty outputs do not establish analytical completeness. "
        "The existing analytical instructions apply; the typed draft field names govern output formatting.")


def assemble_draft(response, *, window_years, compact=False):
    decoded = decode_transport(response) if compact else response
    jsonschema.validate(decoded, generation_schema())
    data = deepcopy(decoded)
    data["m3_assessment"]["management_stability"]["recent_change_window_years"] = window_years
    jsonschema.validate(data, generation_schema(assembled=True))
    return data, {"contract": DRAFT_ID, "original_draft_sha256": content_hash(response),
        "assembled_draft_sha256": content_hash(data), "canonical_input_validation": "valid",
        "transport_decode": ({"kind": "lossless_property_aliases", "alias_map_sha256": content_hash(transport_aliases()),
                              "decoded_draft_sha256": content_hash(decoded)} if compact else None),
        "operations": [{"path": "/m3_assessment/management_stability/recent_change_window_years",
                        "owner": "application", "source": "configured management_window_years", "value": window_years}],
        "validator_outcomes_assigned": False, "model_owned_defaults_inserted": False}


def validate_assessment_draft(proposal):
    # Validate using the exact assessment projection, including the sole
    # application-owned field. No permissive Pydantic defaults are applied.
    jsonschema.validate(proposal, generation_schema(assembled=True)["properties"]["m3_assessment"])


def validate_governed_response(response, governed):
    from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
    data = deepcopy(response)
    # The downstream model carries explicit validator audit metadata in addition
    # to the canonical generation fields. It remains fully Pydantic-validated.
    metadata = {"validator_id", "validator_version", "validation_issues", "rejected_items", "model_proposal", "replay_provenance"}
    data["m3_assessment"] = {k: deepcopy(v) for k, v in governed.items() if k not in metadata}
    for bucket in FINDINGS:
        canonical_fields = BO_M3_JSON_SCHEMA["properties"]["m3_assessment"]["properties"][bucket]["items"]["properties"]
        for item in data["m3_assessment"][bucket]:
            for key in ("validation_reasons", "credibility_vs_direction_note"):
                if key not in canonical_fields:
                    item.pop(key, None)
    jsonschema.validate(data, BO_M3_JSON_SCHEMA)
    return data


def ownership(path):
    key = path.rsplit("/", 1)[-1]
    if key in {"admissibility", "verification_status", "confirmation_status", "provenance_status", "disposition"}:
        return "C", "Validator owns final outcome; model supplies an explicitly proposed status, except admissibility which is never generated."
    if key == "recent_change_window_years":
        return "B", "Actual configured review window, recorded as an assembly operation."
    if key in PROFILE_DERIVED | FACT_DERIVED | STABILITY_DERIVED and "/management_stability/" in path:
        return "C", "Computed from evidence by existing management validator; excluded from model draft."
    return "A", "Model must explicitly supply this field, including genuine null/empty values; no assembly default."


def audit_required_gaps(canonical, provider):
    gaps = []
    def visit(c, p, path=""):
        for key in c.get("required", []):
            child = path + "/" + key
            if key not in p.get("required", []):
                owner, resolution = ownership(child)
                gaps.append({"path": child, "ownership": owner, "resolution": resolution})
        for key, value in c.get("properties", {}).items():
            visit(value, p.get("properties", {}).get(key, {}), path + "/" + key)
        if isinstance(c.get("items"), dict): visit(c["items"], p.get("items", {}), path + "/[]")
    visit(canonical, provider)
    return gaps
