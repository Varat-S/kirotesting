"""Single authorized full HCA run through the existing standalone CLI.

Adds a private transport witness and one-request guard, not analytical behavior.
Existing destinations cause refusal. No execution without the explicit flag.
"""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
CASE = "HCA_DANIEL_M3_LIVE_V14_20261026_01"
DB = ROOT / "data/m3_v14_hca_live_20261026_01.db"
TRANSPORT = ROOT / "data/m3_v14_hca_live_20261026_01_transport.db"
OUTPUT = ROOT / "output/bo_m3/live_verification" / CASE


def main():
    os.chdir(ROOT)
    for path in [DB, TRANSPORT, OUTPUT, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")]:
        if path.exists():
            raise SystemExit("A destination already exists; refusing overwrite or another call.")
    from app.core.hashing import content_hash
    from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
    from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema, VERTEX_M3_SCHEMA_ADAPTER_VERSION
    assert VERTEX_M3_SCHEMA_ADAPTER_VERSION == "vertex-m3-1.4"
    schema_hash = content_hash(vertex_m3_response_schema(BO_M3_JSON_SCHEMA))
    assert schema_hash == "6238ae584c5931311dc231c568ef1d676d2f6b0d4cc4f1523d8a6e0970398320"
    if sys.argv[1:] != ["--execute-one-approved-hca-run"]:
        if sys.argv[1:]:
            raise SystemExit("Unknown argument; no provider execution.")
        print(json.dumps({"offline_check": "passed", "paths_unused": True,
                          "schema_sha256": schema_hash, "provider_calls": 0}))
        return
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    from app.core.config import Settings
    project = Settings().google_cloud_project
    if not project:
        raise SystemExit("Configure GOOGLE_CLOUD_PROJECT before a live run; no generation attempted.")
    os.environ.update(LLM_PROVIDER="vertex", VERTEX_API_KEY="",
        GOOGLE_CLOUD_PROJECT=project, GOOGLE_CLOUD_LOCATION="global",
        VERTEX_MODEL_NARROW="gemini-3.1-flash-lite", LLM_TIMEOUT_SECONDS="120",
        LLM_MAX_OUTPUT_TOKENS="12000", DEBUG="false")
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.services.llm.providers_vertex import VertexProviderBackend
    from app import cli
    from google import genai
    fd = os.open(TRANSPORT, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    witness = sqlite3.connect(TRANSPORT)
    witness.execute("CREATE TABLE records (name TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")

    def record(name, value):
        witness.execute("INSERT INTO records VALUES (?, ?)", (name, json.dumps(value, ensure_ascii=False)))
        witness.commit()

    state = {"sdk_generation_invocations": 0, "generation_http_attempts": 0,
             "http_status": None, "finish_reasons": [], "application_retries": 0, "fallbacks": False}
    clients = []
    record("scope", {"case_id": CASE, "request_budget": 1, "schema_sha256": schema_hash,
        "model": "gemini-3.1-flash-lite", "location": "global", "sdk_attempts": 1,
        "max_output_tokens": 12000, "authentication": "existing_ADC"})

    def on_request(req):
        if req.method == "POST" and req.url.path.endswith(":generateContent"):
            state["generation_http_attempts"] += 1
            if state["generation_http_attempts"] != 1:
                raise RuntimeError("Single generation HTTP request budget exceeded.")
            record("http_attempt", {"method": "POST", "path": req.url.path, "ordinal": 1})

    def on_response(resp):
        if resp.request.url.path.endswith(":generateContent"):
            state["http_status"] = resp.status_code
            record("http_status", {"status": resp.status_code})

    def guarded_client(backend):
        settings = backend._settings
        assert not backend._api_key()
        sdk = genai.Client(vertexai=True, project=settings.google_cloud_project,
            location="global", http_options={"timeout": 120000, "retry_options": {"attempts": 1},
                "client_args": {"event_hooks": {"request": [on_request], "response": [on_response]}}})
        clients.append(sdk)

        def generate_content(**kwargs):
            state["sdk_generation_invocations"] += 1
            if state["sdk_generation_invocations"] != 1:
                raise RuntimeError("Single SDK generation invocation budget exceeded.")
            assert kwargs["model"] == "gemini-3.1-flash-lite"
            config = kwargs["config"]
            assert content_hash(config["response_json_schema"]) == schema_hash
            assert config["http_options"]["retry_options"]["attempts"] == 1
            assert config["max_output_tokens"] == 12000
            assert "bo-m3-corporate-assessment-1.2" in config["system_instruction"]
            assert "bo-m3-management-stability-1.0" in config["system_instruction"]
            inputs = json.loads(kwargs["contents"])
            assert inputs["management_review_policy"]["recent_change_window_years"] == 3
            record("configured_request", kwargs)  # No headers or authentication objects.
            response = sdk.models.generate_content(**kwargs)  # Only paid invocation.
            state["finish_reasons"] = [str(c.finish_reason.value) if hasattr(c.finish_reason, "value")
                else str(c.finish_reason) for c in (response.candidates or [])]
            record("response_body", response.model_dump(mode="json", exclude_none=True,
                                                        exclude={"sdk_http_response"}))
            return response
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    VertexProviderBackend._client = guarded_client
    stdout = io.StringIO()
    exit_code = 0
    try:
        with contextlib.redirect_stdout(stdout):
            cli.main(["--database", str(DB), "--output", str(OUTPUT), "run-m3", CASE,
                "--package", "examples/healthcare/hca-2025/package.json",
                "--corporate-structure", "data/test_inputs/hca_simulated_corporate_structure.json",
                "--live"])
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        exit_code = 1
        record("local_failure_type", {"type": type(exc).__name__})
    record("cli_stdout", {"text": stdout.getvalue(), "exit_code": exit_code})
    record("final_transport_summary", {**state, "cli_exit_code": exit_code})
    if OUTPUT.is_dir():
        with (OUTPUT / "transport_summary.json").open("x") as f:
            json.dump({**state, "cli_exit_code": exit_code}, f, indent=2)
    print(json.dumps({**state, "cli_exit_code": exit_code, "database": str(DB),
                      "output": str(OUTPUT), "private_transport_recording": str(TRANSPORT)}, indent=2))
    for sdk in clients:
        sdk.close()
    witness.close()


if __name__ == "__main__":
    main()
