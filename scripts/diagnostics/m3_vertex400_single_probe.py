"""One expressly approved schema-only probe; existing paths prevent reruns.

No HCA data, full specialist instructions, retrieval, retries or fallbacks.
Without --execute-one-approved-probe this performs offline checks only.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.core.hashing import content_hash
from app.models.base import create_engine_and_session, init_db
from app.models.orm import Case
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.prompts.registry import PromptRegistry
from app.services.bo.m3_diagnostics import (
    load_recorded_m3_run, record_generation, record_provider_preflight,
)
from app.services.llm.client import LLMClient, LLMRequest
from app.services.llm.providers_vertex import VertexProviderBackend
from app.services.llm.providers_vertex_m3 import (
    VERTEX_M3_SCHEMA_ADAPTER_VERSION, vertex_m3_response_schema,
)
from app.services.pipeline.artifacts import StageRecorder, verified_artifacts


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "examples/bo_m3/vertex400_diagnostics/minimal_probe_plan.json"
CASE = "M3_VERTEX400_SCHEMA_PROBE_20261026_01"


def check_plan():
    plan = json.loads(PLAN.read_text())
    schema = vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    assert VERTEX_M3_SCHEMA_ADAPTER_VERSION == "vertex-m3-1.4"
    assert content_hash(schema) == plan["schema_sha256"]
    assert plan["proposed_generation_request_count"] == plan["sdk_attempts"] == 1
    assert plan["application_retries"] == 0 and plan["fallbacks"] is False
    assert plan["max_output_tokens"] == 512
    assert plan["model"] == "gemini-3.1-flash-lite" and plan["location"] == "global"
    db = ROOT / plan["fresh_database_candidate"]
    output = ROOT / plan["fresh_output_candidate"]
    if db.exists() or output.exists():
        raise SystemExit("Probe storage already exists. Refusing another execution or overwrite.")
    return plan, schema, db, output


def main():
    plan, schema, db, output = check_plan()
    if sys.argv[1:] != ["--execute-one-approved-probe"]:
        if sys.argv[1:]:
            raise SystemExit("Unknown argument; no provider request made.")
        print(json.dumps({"offline_check": "passed", "schema_sha256": content_hash(schema),
                          "paths_unused": True, "provider_calls": 0}))
        return

    # Secrets are never printed. Existing ADC is used without editing credentials.
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    settings = Settings().model_copy(update={
        "llm_provider": "vertex", "vertex_api_key": None,
        "google_cloud_location": "global",
        "vertex_model_narrow": plan["model"],
        "llm_max_output_tokens": 512, "llm_timeout_seconds": 120,
        "llm_temperature": 1.0, "debug": False,
    })
    assert settings.google_cloud_project
    backend = VertexProviderBackend(settings)
    backend.check_configuration()
    output.mkdir(parents=True, exist_ok=False)
    db.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    engine, factory = create_engine_and_session("sqlite:///" + str(db))
    init_db(engine)
    session = factory()
    session.add(Case(case_id=CASE))
    prompts = PromptRegistry(session)
    prompt = prompts.register("m3_schema_acceptance_diagnostic", template=plan["system_instruction"],
                              response_schema=BO_M3_JSON_SCHEMA, role="schema_diagnostic")
    request = LLMRequest(method="m3_corporate_management", prompt=prompt,
        inputs={"diagnostic_instruction": plan["contents"]}, evidence_ids=[], case_id=CASE,
        case_version=1, model_tier="narrow", temperature=1.0, record_private_diagnostics=True)
    payload = {"model": plan["model"], "contents": plan["contents"], "config": {
        "system_instruction": plan["system_instruction"], "response_json_schema": schema,
        "response_mime_type": "application/json", "temperature": 1.0,
        "max_output_tokens": 512,
        "http_options": {"timeout": 120000, "retry_options": {"attempts": 1}},
    }, "_schema_sha256": content_hash(schema), "_inputs_sha256": content_hash(request.inputs),
       "_requested_temperature": 1.0}

    class MinimalProbeBackend(VertexProviderBackend):
        def build_request_payload(self, req):
            assert req is request
            return payload

    state = {"sdk_generation_invocations": 0, "generation_http_attempts": 0,
             "http_status": None, "finish_reasons": []}
    clients = []

    def on_http_request(http_request):
        # Authentication traffic is managed by ADC; only generation is counted here.
        if http_request.method == "POST" and http_request.url.path.endswith(":generateContent"):
            state["generation_http_attempts"] += 1
            if state["generation_http_attempts"] != 1:
                raise RuntimeError("Single generation HTTP request budget exceeded.")
            StageRecorder(session, CASE, 1).record("generation_http_attempt", {
                "ordinal": 1, "method": "POST", "path": http_request.url.path,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            session.commit()

    def on_http_response(http_response):
        if http_response.request.url.path.endswith(":generateContent"):
            state["http_status"] = http_response.status_code
            StageRecorder(session, CASE, 1).record("generation_http_response", {
                "http_status": http_response.status_code,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            session.commit()

    def client_factory():
        from google import genai
        sdk = genai.Client(vertexai=True, project=settings.google_cloud_project,
            location="global", http_options={"timeout": 120000,
                "retry_options": {"attempts": 1}, "client_args": {"event_hooks": {
                    "request": [on_http_request], "response": [on_http_response]}}})
        clients.append(sdk)

        def generate_content(**kwargs):
            state["sdk_generation_invocations"] += 1
            if state["sdk_generation_invocations"] != 1:
                raise RuntimeError("Single SDK invocation budget exceeded.")
            StageRecorder(session, CASE, 1).record("sdk_invocation_started", {
                "invocation": 1, "maximum_attempts": 1, "fallbacks": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            session.commit()
            # The only paid generation invocation in this entire script.
            response = sdk.models.generate_content(**kwargs)
            state["finish_reasons"] = [str(c.finish_reason.value) if hasattr(c.finish_reason, "value")
                else str(c.finish_reason) for c in (response.candidates or [])]
            StageRecorder(session, CASE, 1).record("provider_response_body", {
                "response": response.model_dump(mode="json", exclude_none=True,
                                                exclude={"sdk_http_response"}),
                "note": "Complete SDK model response, excluding HTTP headers/transport wrapper.",
            })
            session.commit()
            return response

        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    probe_backend = MinimalProbeBackend(settings, client_factory=client_factory)
    client = LLMClient(probe_backend, prompts=prompts, session=session)
    StageRecorder(session, CASE, 1).record("approved_probe_scope", {
        "generation_request_budget": 1, "model": plan["model"], "location": "global",
        "authentication": "existing_ADC", "schema_sha256": content_hash(schema),
        "schema_adapter": VERTEX_M3_SCHEMA_ADAPTER_VERSION, "no_company_evidence": True,
        "output_token_cap": 512, "sdk_attempts": 1, "application_retries": 0,
        "fallbacks": False, "plan_sha256": content_hash(plan),
    })
    preflight = record_provider_preflight(session, request, probe_backend)
    session.commit()  # Durable request metadata before any provider I/O.
    started_at = datetime.now(timezone.utc).isoformat()
    result = client.generate_only(request)  # exactly once; no retry loop
    diagnostics = record_generation(session, client, result, started_at=started_at,
                                    preflight_stage=preflight)
    public = {**diagnostics, **state, "case_id": CASE, "scope": "schema_acceptance_only",
        "schema_adapter": VERTEX_M3_SCHEMA_ADAPTER_VERSION,
        "database": str(db), "output_directory": str(output),
        "application_retries": 0, "fallbacks": False,
        "specialist_evidence_gate": "not_run", "validator_7": "not_run",
        "analytical_quality_evaluated": False,
        "validation_detail": result.validation_detail,
        "stopped_after_single_invocation": True}
    StageRecorder(session, CASE, 1).record("single_probe_summary", public)
    session.commit()
    load_recorded_m3_run(session, diagnostics["model_run_id"])
    verified_artifacts(session, CASE, 1)
    with (output / "summary.json").open("x") as f:
        json.dump(public, f, indent=2)
        f.write("\n")
    print(json.dumps(public, indent=2))
    for sdk in clients:
        sdk.close()
    session.close()
    engine.dispose()


if __name__ == "__main__":
    main()
