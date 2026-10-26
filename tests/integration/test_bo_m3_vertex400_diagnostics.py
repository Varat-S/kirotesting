"""Real SDK / in-memory HTTP, secure failure recording and CLI exit status."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.base import create_engine_and_session, init_db
from app.models.orm import ModelRun
from app.services.bo.m3_diagnostics import load_recorded_m3_run
from app.services.bo.m3_demo import write_m3_demo
from app.services.bo.m3_standalone import run_m3
from app.services.llm.providers_vertex import VertexProviderBackend
from app.services.pipeline.artifacts import verified_artifacts
from tests import bo_helpers as h
from tests.unit.test_vertex_provider import _settings


DOC = Path("data/test_inputs/hca_simulated_corporate_structure.json")
SECRET = "configured-credential-must-be-redacted"
EVIDENCE_ECHO = "PRIVATE-FILING-ECHO-DO-NOT-PUBLISH"


def backend_for_400(before_http=None, *, max_input=1_000_000):
    genai = pytest.importorskip("google.genai")
    import httpx
    sent = []

    def handler(request):
        if before_http:
            before_http()
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
            "message": "Schema constraint rejected: " + EVIDENCE_ECHO + " API_KEY=" + SECRET,
            "details": [{"@type": "type.googleapis.com/google.rpc.BadRequest",
                "headers": {"Authorization": SECRET}, "fieldViolations": [{
                    "field": "generationConfig.responseJsonSchema", "description": "Schema detail: " + EVIDENCE_ECHO}]}]}})

    client = genai.Client(vertexai=True, api_key="offline-only-key", http_options={"client_args": {"transport": httpx.MockTransport(handler)}})
    settings = _settings(vertex_api_key=SECRET, google_cloud_location="global",
                         vertex_model_narrow="gemini-3.1-flash-lite", llm_max_input_bytes=max_input)
    return VertexProviderBackend(settings, client_factory=lambda: client, m3_draft_contract=False), client, sent, settings


def test_failed_actual_sdk_request_is_recorded_privately_and_never_misreported_unattempted(db_session, tmp_path):
    def before_http():
        stages = verified_artifacts(db_session, h.CASE, 1)
        preflight = next(s for s in stages if s.name.startswith("m3_provider_preflight_"))
        data = json.loads(preflight.payload_json)
        assert data["phase"] == "before_provider_io"
        assert data["model_config"]["request_attempted"] is False
        assert "contents" in data["model_config"]["provider_request"]

    backend, client, sent, _ = backend_for_400(before_http)
    try:
        result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(), metrics=h.metrics(),
                        live_backend=backend, corporate_structure_document=json.loads(DOC.read_text()))
    finally:
        client.close()
    assert len(sent) == 1 and result.model_called is True
    stage = result.validation_stages
    assert stage["request_attempted"] is True and stage["http_status"] == 400
    assert stage["service_accepted"] is False and stage["http_response_received"] is True
    assert stage["model_response_received"] is False and stage["canonical_validation_occurred"] is False
    assert stage["provider"] == "vertex" and stage["model_id"] == "gemini-3.1-flash-lite"
    assert stage["model_execution"] == "provider_rejected"
    assert stage["module_completion"] == "failed_model_execution"
    bundle = load_recorded_m3_run(db_session, stage["model_run_id"])
    meta = bundle["provider_metadata"]
    assert meta["provider_preflight_stage"].startswith("m3_provider_preflight_")
    assert meta["sdk_version"]
    assert EVIDENCE_ECHO in meta["private_service_error"]["message"]
    assert EVIDENCE_ECHO in json.dumps(meta["private_service_error"]["details"])
    assert SECRET not in json.dumps(bundle)
    assert "headers" not in json.dumps(meta["private_service_error"])
    assert meta["provider_request"]["contents"] == sent[0]["contents"][0]["parts"][0]["text"]
    ordered = json.loads(meta["provider_request_json"])
    assert list(ordered["config"]["response_json_schema"]["properties"]) == list(sent[0]["generationConfig"]["responseJsonSchema"]["properties"])
    assert list(ordered["config"]["response_json_schema"]["properties"]["m3_assessment"]["properties"])[0] == "schema_version"
    assert meta["provider_request"]["config"]["http_options"]["retry_options"]["attempts"] == 1
    paths = write_m3_demo(result, tmp_path)
    assert len(paths) == 6
    for path in paths.values():
        content = path.read_text()
        assert SECRET not in content and EVIDENCE_ECHO not in content
        assert "private_service_error" not in content


@pytest.mark.parametrize("local_failure", [False, True])
def test_cli_exits_nonzero_after_persisting_failure_artifacts(monkeypatch, tmp_path, capsys, local_failure):
    from app import cli

    backend, client, sent, settings = backend_for_400(max_input=1000 if local_failure else 1_000_000)
    monkeypatch.setattr(cli, "_live_backend", lambda: backend)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.SourcePackage, "load", lambda path: object())
    monkeypatch.setattr(cli.CreditMemoPipeline, "prepare_case", lambda *a, **k: SimpleNamespace(
        evidence_snapshot=h.evidence_snapshot(), metrics=h.metrics()))
    database, out = tmp_path / "private.db", tmp_path / "output"
    args = ["--database", str(database), "--output", str(out), "run-m3", h.CASE,
            "--package", "offline-prepared", "--corporate-structure", str(DOC), "--live"]
    try:
        with pytest.raises(SystemExit) as exited:
            cli.main(args)
    finally:
        client.close()
    assert exited.value.code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["validation_stages"]["request_attempted"] is (not local_failure)
    assert summary["model_called"] is (not local_failure)
    assert len(sent) == (0 if local_failure else 1)
    assert len(summary["outputs"]) == 6 and all(Path(p).is_file() for p in summary["outputs"].values())
    assert database.stat().st_mode & 0o777 == 0o600
    assert EVIDENCE_ECHO not in json.dumps(summary)
    engine, factory = create_engine_and_session("sqlite:///" + str(database))
    with factory() as session:
        row = session.scalars(select(ModelRun)).one()
        bundle = load_recorded_m3_run(session, row.run_id)
        assert bundle["provider_metadata"]["request_attempted"] is (not local_failure)
        assert SECRET not in json.dumps(bundle)
    engine.dispose()
