"""400 isolation: actual schema fixtures, local SDK and privacy boundaries."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.llm.providers_vertex import VertexProviderBackend
from app.services.llm.providers import ProviderCallError
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema, vertex_m3_output_instructions, STRUCTURAL_EXPLICIT_FIELDS
from app.services.llm.vertex_m3_diagnostics import failure_metadata
from examples.bo_m3.vertex400_diagnostics.compare_offline import variants, schema_stats, structural_diff, sdk_wire_check
from tests.unit.test_vertex_m3_schema import _request, _settings


def test_exact_a_to_b_schema_diff_has_only_the_three_documented_changes():
    choices = variants()
    b = json.loads(Path("examples/bo_m3/vertex400_diagnostics/vertex_m3_1_3_provider_schema.json").read_text())
    assert choices["B_v1_3_failed_reconstructed"] == b
    changes = structural_diff(choices["A_phase2_accepted"], b)
    assert {c["path"] for c in changes} == {
        "/properties/m3_assessment/properties/management_stability",
        "/properties/m3_assessment/properties/schema_version/enum",
        "/properties/m3_assessment/properties/structural_credit_findings/items/required",
    }
    required = next(c for c in changes if c["path"].endswith("/required"))
    assert set(required["after"]) - set(required["before"]) == set(STRUCTURAL_EXPLICIT_FIELDS)


@pytest.mark.parametrize("name", list(variants()))
def test_real_installed_sdk_preserves_raw_json_schema_for_each_variant_offline(name):
    pytest.importorskip("google.genai")
    result = sdk_wire_check(variants()[name])
    assert result["raw_json_schema_preserved"]
    assert not result["remote_acceptance_tested"]


def test_no_provider_keyword_or_required_reference_defect_is_new():
    before = deepcopy(BO_M3_JSON_SCHEMA)
    choices = variants()
    for schema in choices.values():
        stats = schema_stats(schema)
        assert not stats["unsupported_keyword_paths"]
        assert not stats["required_not_in_properties"]
        assert stats["anyOf"] == 0 and stats["oneOf"] == 0
    compact = schema_stats(choices["D_compact_v1_4"])
    failed = schema_stats(choices["B_v1_3_failed_reconstructed"])
    assert compact["properties"] < failed["properties"]
    assert compact["enums"] < failed["enums"]
    assert compact["nullable_unions"] < failed["nullable_unions"]
    assert BO_M3_JSON_SCHEMA == before
    instructions = vertex_m3_output_instructions(BO_M3_JSON_SCHEMA)
    assert "m3_assessment.structural_credit_findings[]:" in instructions
    assert all(field in instructions for field in STRUCTURAL_EXPLICIT_FIELDS)


def test_structured_error_keeps_private_reason_but_never_headers_or_tokens():
    secret = "configured-private-key-value"
    exc = SimpleNamespace(code=400, status="INVALID_ARGUMENT", message="Schema rejected; " + secret,
        details={"error": {"details": [{"@type": "type.googleapis.com/google.rpc.BadRequest", "headers": {"Authorization": secret},
            "fieldViolations": [{"field": "generationConfig.responseJsonSchema", "description": "Sensitive filing excerpt; Bearer token-value"}]}]}})
    meta = failure_metadata(exc, {"backend": "vertex"}, sdk_started=True, private=True, secrets=(secret,))
    assert meta["request_attempted"] is True and meta["service_accepted"] is False
    assert meta["http_response_received"] is True and meta["model_response_received"] is False
    private = meta["private_service_error"]
    assert "Schema rejected" in private["message"] and secret not in json.dumps(meta)
    assert "headers" not in json.dumps(private) and "token-value" not in json.dumps(private)
    assert "Sensitive filing excerpt" in json.dumps(private)  # preserved privately, not a public error


@pytest.mark.parametrize("error,started,expected", [(ValueError("local"), True, False), (RuntimeError("ambiguous network"), True, None), (RuntimeError("not configured"), False, False)])
def test_attempt_states_preserve_uncertainty(error, started, expected):
    meta = failure_metadata(error, {}, sdk_started=started)
    assert meta["request_attempted"] is expected
    assert meta["service_accepted"] is None
    assert "private_service_error" not in meta


def test_provider_never_attempts_a_generation_when_input_preparation_fails():
    backend = VertexProviderBackend(_settings().model_copy(update={"llm_max_input_bytes": 2}),
                                    client_factory=lambda: pytest.fail("No SDK client should be constructed"))
    with pytest.raises(ProviderCallError) as result:
        backend.prepare_diagnostics(_request("m3_corporate_management", BO_M3_JSON_SCHEMA))
    assert result.value.diagnostic_metadata["request_attempted"] is False
    assert result.value.diagnostic_metadata["error_phase"] == "local_preparation"


def test_typed_response_schema_is_not_interchangeable_with_raw_json_schema():
    genai = pytest.importorskip("google.genai")
    import httpx
    sent = []
    def handle(request):
        sent.append(request)
        return httpx.Response(200, json={})
    client = genai.Client(vertexai=True, api_key="offline-test", http_options={"client_args": {"transport": httpx.MockTransport(handle)}})
    try:
        # Config construction accepts a dictionary; typed schema conversion
        # later rejects list-valued nullable types before the HTTP layer.
        config = {"response_mime_type": "application/json", "response_schema": vertex_m3_response_schema(BO_M3_JSON_SCHEMA)}
        genai.types.GenerateContentConfig.model_validate(config)
        with pytest.raises(ValueError):
            client.models.generate_content(model="gemini-3.1-flash-lite", contents="offline", config=config)
        assert sent == []
        genai.types.GenerateContentConfig.model_validate({"response_mime_type": "application/json", "response_json_schema": vertex_m3_response_schema(BO_M3_JSON_SCHEMA)})
    finally:
        client.close()
