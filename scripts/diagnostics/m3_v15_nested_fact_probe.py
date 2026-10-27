"""Execute only the separately approved, single v1.5 nested-fact diagnostic.

Default mode is offline. Existing storage prevents reruns. No production code,
plan, schema, source or model is modified; no retry/fallback path exists.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version

import jsonschema

from app.core.config import Settings
from app.core.hashing import content_hash
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.schemas.bo_m3 import M3CorporateAssessment
from app.services.llm.providers_vertex_m3 import VERTEX_M3_SCHEMA_ADAPTER_VERSION
from app.services.llm.vertex_m3_diagnostics import failure_metadata, redact
from examples.bo_m3.vertex_strict_json.prepared_probe import HERE, prepared_request, inspect_probe_response


def main():
    plan_bytes = (HERE / "minimal_live_probe_plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    payload = prepared_request()
    assert VERTEX_M3_SCHEMA_ADAPTER_VERSION == plan["schema_adapter"] == "vertex-m3-1.5"
    assert payload["model"] == "gemini-3.1-flash-lite" and plan["location"] == "global"
    assert payload["config"]["max_output_tokens"] == 1024
    assert payload["config"]["http_options"]["retry_options"]["attempts"] == 1
    db = Path(plan["fresh_database_candidate"])
    output = Path(plan["fresh_output_candidate"])
    destinations = [db, output, *(Path(str(db) + ext) for ext in ("-wal", "-shm", "-journal"))]
    if any(path.exists() for path in destinations):
        raise SystemExit("Probe storage already exists: no overwrite or further request allowed.")
    jsonschema.Draft202012Validator.check_schema(payload["config"]["response_json_schema"])
    if sys.argv[1:] != ["--execute-one-approved-probe"]:
        if sys.argv[1:]: raise SystemExit("Unknown argument; no provider call.")
        print(json.dumps({"offline_check": "passed", "paths_unused": True, "provider_calls": 0,
                          "schema_sha256": plan["schema_sha256"]}))
        return

    logging.disable(logging.CRITICAL)
    settings = Settings()
    if not settings.google_cloud_project:
        raise SystemExit("Existing Vertex project unavailable; no provider request made.")
    os.umask(0o077)
    output.mkdir(parents=True, exist_ok=False)
    db.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    store = sqlite3.connect(db)
    store.execute("CREATE TABLE records (name TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")

    def record(name, value):
        store.execute("INSERT INTO records VALUES (?, ?)", (name, json.dumps(value, ensure_ascii=False)))
        store.commit()

    state = {"sdk_generation_invocations": 0, "generation_http_attempts": 0, "http_status": None,
        "json_parsing": "not_run", "provider_schema_validation": "not_run", "canonical_schema_validation": "not_run",
        "typed_contract_validation": "not_run", "nested_fact_postconditions": "not_run", "finish_reasons": [],
        "input_tokens": None, "output_tokens": None, "total_tokens": None, "thinking_tokens": None,
        "properly_structured_fact_objects": False, "executives": None, "fact_objects": None,
        "specialist_evidence_gate": "not_run", "validator_7": "not_run", "analytical_quality_tested": False}
    record("approved_scope", {"case_id": plan["case_id"], "schema_adapter": plan["schema_adapter"],
        "schema_sha256": plan["schema_sha256"], "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "model": payload["model"], "location": "global", "authentication": "existing_ADC",
        "max_output_tokens": 1024, "paid_generation_budget": 1, "sdk_attempts": 1,
        "application_retries": 0, "fallbacks": False, "google_genai_version": version("google-genai"),
        "timestamp": datetime.now(timezone.utc).isoformat()})
    record("configured_request", payload)

    def on_request(request):
        if request.method == "POST" and request.url.path.endswith(":generateContent"):
            if state["generation_http_attempts"] >= 1:
                raise RuntimeError("Second generation request blocked before transmission.")
            state["generation_http_attempts"] += 1
            wire = json.loads(request.content)
            assert wire["generationConfig"]["responseJsonSchema"] == payload["config"]["response_json_schema"]
            record("generation_http_request", {"ordinal": 1, "path": request.url.path, "body": wire})

    def on_response(response):
        if response.request.url.path.endswith(":generateContent"):
            state["http_status"] = response.status_code
            # Read and preserve the exact service body before SDK interpretation.
            # No headers or authentication objects are recorded.
            response.read()
            record("generation_http_response", {"http_status": response.status_code,
                "body": response.content.decode("utf-8"), "body_sha256": hashlib.sha256(response.content).hexdigest()})

    client = None
    started = time.monotonic()
    try:
        from google import genai
        client = genai.Client(vertexai=True, project=settings.google_cloud_project, location="global",
            http_options={"timeout": 120_000, "retry_options": {"attempts": 1},
                "client_args": {"event_hooks": {"request": [on_request], "response": [on_response]}}})
        state["sdk_generation_invocations"] = 1
        record("sdk_invocation_started", {"ordinal": 1, "timestamp": datetime.now(timezone.utc).isoformat()})
        response = client.models.generate_content(**payload)  # THE ONLY PAID GENERATION INVOCATION.
        record("original_sdk_response", response.model_dump(mode="json", exclude_none=True, exclude={"sdk_http_response"}))
        state["finish_reasons"] = [getattr(candidate.finish_reason, "value", candidate.finish_reason)
                                   for candidate in (response.candidates or [])]
        usage = response.usage_metadata
        if usage is not None:
            state.update(input_tokens=usage.prompt_token_count, output_tokens=usage.candidates_token_count,
                total_tokens=usage.total_token_count, thinking_tokens=usage.thoughts_token_count)
        raw = response.text or ""
        record("original_model_text", {"text": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()})
        try:
            parsed = json.loads(raw)
            state["json_parsing"] = "valid"
        except (ValueError, TypeError) as exc:
            state["json_parsing"] = "invalid"
            record("json_failure", {"error_type": type(exc).__name__, "message": str(exc)})
        else:
            for field, schema in (("provider_schema_validation", payload["config"]["response_json_schema"]),
                                  ("canonical_schema_validation", BO_M3_JSON_SCHEMA)):
                errors = list(jsonschema.Draft202012Validator(schema).iter_errors(parsed))
                state[field] = "rejected" if errors else "valid"
                record(field, {"errors": [{"path": list(e.absolute_path), "schema_path": list(e.absolute_schema_path),
                    "rule": e.validator, "message": e.message} for e in errors]})
            try:
                M3CorporateAssessment.model_validate(parsed.get("m3_assessment"))
                state["typed_contract_validation"] = "valid"
            except Exception as exc:
                state["typed_contract_validation"] = "rejected"
                record("typed_contract_failure", {"error_type": type(exc).__name__, "message": str(exc)})
            try:
                checked = inspect_probe_response(raw)
                state.update(nested_fact_postconditions="valid", properly_structured_fact_objects=True,
                             executives=checked["executives"], fact_objects=checked["fact_objects"])
            except Exception as exc:
                state["nested_fact_postconditions"] = "rejected"
                record("nested_fact_failure", {"error_type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        meta = failure_metadata(exc, {}, sdk_started=bool(state["sdk_generation_invocations"]), private=True)
        record("provider_failure", {"error_type": type(exc).__name__, "diagnostics": meta,
            "sanitized_description": redact(str(exc))})
        state.update(error_type=type(exc).__name__, service_status=meta.get("service_status"))
    finally:
        if client is not None: client.close()
        complete = (state["http_status"] == 200 and state["finish_reasons"] == ["STOP"]
            and all(state[k] == "valid" for k in ("json_parsing", "provider_schema_validation",
                "canonical_schema_validation", "typed_contract_validation", "nested_fact_postconditions")))
        summary = {**state, "case_id": plan["case_id"], "schema_adapter": plan["schema_adapter"],
            "schema_sha256": plan["schema_sha256"], "model": payload["model"], "location": "global",
            "max_output_tokens": 1024, "application_retries": 0, "fallbacks": False,
            "diagnostic_success": complete, "stopped_after_single_invocation": True,
            "latency_ms": round((time.monotonic()-started)*1000), "private_database": str(db),
            "output_directory": str(output), "completed_at": datetime.now(timezone.utc).isoformat()}
        record("summary", summary)
        with (output / "summary.json").open("x") as file:
            json.dump(summary, file, indent=2)
            file.write("\n")
        store.close()
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
