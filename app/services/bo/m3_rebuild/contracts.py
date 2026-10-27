"""Authoritative extraction schema and explicit Vertex compatibility transform."""
from copy import deepcopy
import jsonschema

SCHEMA_VERSION = "m3-management-response-2.0"
ADAPTER_VERSION = "m3-shared-specialist-2.0"


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def string():
    return {"type": "string"}


ROLE_SCHEMA = obj({k: string() for k in
                   ("position", "start_date", "end_date", "source_id", "quote")})
EXECUTIVE_SCHEMA = obj({
    "name": string(), "position": string(), "appointment_date": string(),
    "source_id": string(), "quote": string(),
    "prior_internal_roles": {"type": "array", "items": ROLE_SCHEMA},
    "external_recruitment_quote": string(), "external_source_id": string(),
})
EXTRACTION_SCHEMA = obj({
    "roster_date": string(), "roster_source_id": string(), "roster_quote": string(),
    "executives": {"type": "array", "items": EXECUTIVE_SCHEMA},
})
LEGACY_EXTRACTION_SCHEMA = deepcopy(EXTRACTION_SCHEMA)
QUOTE_SCHEMA = obj({"source_id": string(), "quote": string()})
SIGNAL_SCHEMA = {"enum": ["credit_positive", "credit_negative", "neutral", "insufficient_evidence"]}
# Additional fragments retain their individual canonical IDs when a biography
# crosses passage boundaries. Primary quote fields remain compatible with saved facts.
for schema in (ROLE_SCHEMA, EXECUTIVE_SCHEMA):
    schema["properties"]["supporting_citations"] = {"type": "array", "items": QUOTE_SCHEMA}
EXTRACTION_SCHEMA["properties"].update({
    "facts": {"type": "array", "items": obj({
        "fact_id": string(), "executive": string(), "statement": string(),
        "citations": {"type": "array", "items": QUOTE_SCHEMA},
    })},
    "judgments": {"type": "array", "items": obj({
        "judgment_id": string(), "executive": string(), "signal": SIGNAL_SCHEMA,
        "category": {"enum": ["transition", "integration", "continuity", "information_gap"]},
        "supporting_fact_ids": {"type": "array", "items": string()},
        "explanation": string(), "uncertainties": {"type": "array", "items": string()},
    })},
    "overall_assessment": obj({
        "signal": {"enum": ["credit_positive", "credit_negative", "neutral", "mixed", "insufficient_evidence"]},
        "explanation": string(),
        "supporting_judgment_ids": {"type": "array", "items": string()},
        "uncertainties": {"type": "array", "items": string()},
        "missing_information": {"type": "array", "items": string()},
    }),
})
EXTRACTION_SCHEMA["required"] = list(EXTRACTION_SCHEMA["properties"])


def provider_schema(schema=None):
    """Vertex's raw JSON Schema subset; local closed-object checks stay strict.

    Deliberately no independent provider contract, type coercion, nullable unions,
    references, defaults or removal of required fields. No service acceptance is
    asserted until a separately authorized live probe succeeds.
    """
    def convert(value):
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()
                    if k != "additionalProperties"}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value
    return convert(deepcopy(schema or EXTRACTION_SCHEMA))


def validate_extraction(payload):
    jsonschema.Draft202012Validator(EXTRACTION_SCHEMA).validate(payload)
    return payload

# Final artifacts are application-owned and validated independently of extraction.
def array(items):
    return {"type": "array", "items": items}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


CITATION_SCHEMA = obj({"citation_id": string(), "source_id": string(), "physical_page": {"type": "integer", "minimum": 1},
                      "quote": string(), "source_sha256": string(), "quotation_check": {"const": "passed"}})
PROFILE_SCHEMA = obj({
    "name": string(), "position": string(), "roster_position": string(), "appointment_date": string(),
    "appointment_precision": {"enum": ["year", "month", "day", "unavailable"]},
    "recency": {"enum": ["stable", "recent", "boundary_uncertain", "unavailable"]},
    "prior_internal_roles": array(obj({"position": string(), "start_date": string(), "end_date": string(),
        "start_precision": string(), "end_precision": string(), "citation_ids": array(string())})),
    "succession": {"enum": ["internal", "confirmed_external", "unavailable"]},
    "citation_ids": array(string()), "semantic_support_check": {"const": "passed_bounded_rules"},
})
OBSERVATION_SCHEMA = obj({"observation_id": string(), "executive": nullable(string()), "rule": string(),
    "direction": {"enum": ["positive", "negative", "neutral", "unavailable"]},
    "category": {"enum": ["transition", "integration", "continuity", "information_gap"]},
    "text": string(), "citation_ids": array(string())})
MANAGEMENT_SCHEMA = obj({
    "schema_version": {"const": "m3-management-assessment-1.0"},
    "artifact_type": {"const": "senior_management_assessment"}, "company": string(), "filing_period": string(),
    "source": obj({"path": string(), "sha256": string(), "section_ids": array(string())}),
    "roster_date": nullable(string()), "roster_citation_id": nullable(string()), "citations": array(CITATION_SCHEMA),
    "review_window": nullable(obj({"start": string(), "end": string(), "boundary": string(), "years": {"const": 3}})),
    "profiles": array(PROFILE_SCHEMA), "observations": array(OBSERVATION_SCHEMA),
    "overall_assessment": obj({"outcome": {"enum": ["mixed", "positive", "negative", "unavailable"]},
        "information_gaps": {"type": "boolean"}, "policy": string(),
        "information_direction": {"enum": ["negative", "neutral", "unavailable"]}}),
    "coverage": obj({"expected": nullable({"type": "integer"}), "admitted": {"type": "integer"},
                     "missing": array(string()), "verifiable": {"type": "boolean"}}),
    "rejected_profiles": array(obj({"name": string(), "stage": string(), "detail": string(),
        "quotation_check": string(), "semantic_support_check": string()})),
    "limitations": array(string()), "completion_status": {"enum": ["complete", "partial"]},
})


def validate_management(payload):
    schema = MANAGEMENT_SCHEMA
    if payload.get("schema_version") in {"m3-management-assessment-2.0", "m3-management-assessment-3.0"}:
        schema = deepcopy(MANAGEMENT_SCHEMA)
        schema["properties"]["schema_version"] = {"const": payload['schema_version']}
        schema["properties"]["facts"] = {"type": "array", "items": obj({
            "fact_id": string(), "executive": string(), "statement": string(),
            "citation_ids": array(string()),
        })}
        schema["properties"]["model_assessment"] = EXTRACTION_SCHEMA["properties"]["overall_assessment"]
        schema["properties"]["validation"] = {"type": "object"}
        schema["properties"]["overall_assessment"]["properties"]["outcome"]["enum"].append("neutral")
        schema["properties"]["observations"]["items"]["properties"].update({
            "signal": SIGNAL_SCHEMA, "supporting_fact_ids": array(string()),
            "uncertainties": array(string()),
        })
        schema["properties"]["profiles"]["items"]["properties"]["tenure"] = {"type": "object"}
        schema["required"] += ["facts", "model_assessment", "validation"]
        if payload['schema_version']=='m3-management-assessment-3.0':
            schema['properties']['citations']['items']['properties'].update({
                'document_id':nullable(string()), 'passage_offset':{'type':'integer'},
                'span_start':{'type':'integer'},'span_end':{'type':'integer'},
                'page_char_start':{'type':'integer'},'page_char_end':{'type':'integer'},
                'original_source_text':string(),'normalized_quote':string(),
                'matching_method':{'enum':['exact','whitespace_typography','pdf_word_boundaries']},
                'outcome':{'const':'accepted'},
            })
            schema['properties']['observations']['items']['properties'].update({
                'review_outcome':{'enum':['accepted','qualified']},'review_reason':string(),
            })
    jsonschema.Draft202012Validator(schema).validate(payload)
    citation_ids = [c["citation_id"] for c in payload["citations"]]
    names = [p["name"] for p in payload["profiles"]]
    if len(citation_ids) != len(set(citation_ids)) or len(names) != len(set(names)):
        raise ValueError("Duplicate final citation/profile identity")
    valid_ids = set(citation_ids)
    refs = []
    if payload["roster_citation_id"]:
        refs.append(payload["roster_citation_id"])
    for profile in payload["profiles"]:
        refs.extend(profile["citation_ids"])
        for role in profile["prior_internal_roles"]:
            refs.extend(role["citation_ids"])
    facts = payload.get("facts", [])
    fact_ids = [f["fact_id"] for f in facts]
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("Duplicate final fact identity")
    for fact in facts:
        refs.extend(fact["citation_ids"])
    accepted_claim_ids=set(fact_ids)
    if payload['schema_version']=='m3-management-assessment-3.0':
        accepted_claim_ids.update(c['claim_id'] for c in payload['validation']['claim_results']
                                  if c['outcome'] in ('accepted','qualified'))
    observation_ids = []
    for observation in payload["observations"]:
        observation_ids.append(observation["observation_id"])
        refs.extend(observation["citation_ids"])
        if not set(observation.get("supporting_fact_ids", [])) <= accepted_claim_ids:
            raise ValueError("Unresolved supporting fact reference")
        if observation["executive"] is not None and observation["executive"] not in names:
            raise ValueError("Observation refers to an unknown executive")
    if len(observation_ids) != len(set(observation_ids)) or not set(refs) <= valid_ids:
        raise ValueError("Unresolved/duplicate final observation or citation reference")
    if any(c["source_sha256"] != payload["source"]["sha256"] or c["source_id"] not in payload["source"]["section_ids"] for c in payload["citations"]):
        raise ValueError("Final citations mix source filings")
    if payload["coverage"]["admitted"] != len(names):
        raise ValueError("Final roster coverage count is inconsistent")
    return payload
