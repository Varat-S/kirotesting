"""Prepare/check a future approved diagnostic. This file has NO live client.

Executing this module only checks a plan and an authored expected response.
Separate human approval is required before any caller sends the payload.
"""
import json
from pathlib import Path

import jsonschema

from app.core.hashing import content_hash
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.schemas.bo_m3 import M3CorporateAssessment
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema

HERE = Path(__file__).resolve().parent


def prepared_request():
    plan = json.loads((HERE / "minimal_live_probe_plan.json").read_text())
    schema = vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    if content_hash(schema) != plan["schema_sha256"]:
        raise ValueError("Schema changed since plan preparation; review before execution.")
    assert plan["paid_requests"] == plan["sdk_attempts"] == 1
    assert plan["application_retries"] == 0 and plan["fallbacks"] is False
    return {"model": plan["model"], "contents": plan["contents"], "config": {
        "system_instruction": plan["system_instruction"],
        "response_mime_type": "application/json", "response_json_schema": schema,
        "temperature": 1.0, "max_output_tokens": plan["max_output_tokens"],
        "http_options": {"timeout": 120_000, "retry_options": {"attempts": 1}},
    }}


def inspect_probe_response(raw):
    """Independent local postconditions; empty executives/facts cannot pass.

    Raises on invalid output. Returning counts proves only the short diagnostic
    contract, not general source truth or a Validator 7 analytical result.
    """
    plan = json.loads((HERE / "minimal_live_probe_plan.json").read_text())
    response = json.loads(raw)
    jsonschema.validate(response, prepared_request()["config"]["response_json_schema"])
    jsonschema.validate(response, BO_M3_JSON_SCHEMA)
    M3CorporateAssessment.model_validate(response["m3_assessment"])
    executives = response["m3_assessment"]["management_stability"]["executives"]
    if not executives or not all(e["facts"] for e in executives):
        raise ValueError("Inconclusive: nested executive fact objects were not demonstrated.")
    if len(executives) != 1 or executives[0]["name"] != "Alex Example":
        raise ValueError("Unexpected synthetic executive; no invented biographies allowed.")
    for fact in executives[0]["facts"]:
        source = fact["source"]
        if source["evidence_id"] != plan["synthetic_source"]["evidence_id"] or not source["quote"] or source["quote"] not in plan["synthetic_source"]["text"]:
            raise ValueError("Probe source reference does not match supplied synthetic text.")
        if fact["kind"] != "current_role" or fact["value"] != "Chief Executive Officer":
            raise ValueError("Probe fact does not match the supplied current role.")
    return {"executives": len(executives), "fact_objects": sum(len(e["facts"]) for e in executives),
            "canonical_validation": "valid", "validator_7": "not_run", "analytical_quality_tested": False}


if __name__ == "__main__":
    payload = prepared_request()
    expected = (HERE / "synthetic_probe_expected.json").read_text()
    result = inspect_probe_response(expected)
    plan = json.loads((HERE / "minimal_live_probe_plan.json").read_text())
    paths = [Path(plan["fresh_database_candidate"]), Path(plan["fresh_output_candidate"])]
    print(json.dumps({"scope": "offline authored example only", "provider_calls": 0,
        "paths_unused": all(not p.exists() for p in paths), "schema_sha256": content_hash(payload["config"]["response_json_schema"]),
        "authored_example_check": result}, indent=2))
