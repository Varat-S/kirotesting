"""FEAT-002 — Vertex-only, M3-only response-schema adaptation (OFFLINE ONLY).

Proves the provider-facing Module 3 schema produced by
``vertex_m3_response_schema``:

* is structurally valid JSON Schema (Draft 2020-12);
* is materially smaller than ``strict_schema(BO_M3_JSON_SCHEMA)`` (strictly
  fewer forced-required, fewer ``additionalProperties: False``, and reduced
  props/arrays/enums) while keeping m3_assessment's 11 required sections and
  the five relaxed arrays' keys;
* is applied ONLY to M3 Vertex requests — NARROW/orchestrator/challenge and
  M1/M2/M4 Vertex requests still use ``strict_schema``, and the OpenAI
  RealProviderBackend is unaffected;
* leaves the canonical ``BO_M3_JSON_SCHEMA`` byte-for-byte unchanged; and
* accepts complete canonical samples, while malformed loose items are rejected
  by the unchanged local canonical validator. Provider validity alone is NOT
  sufficient for acceptance.

No network and no paid model call occur anywhere here.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

import jsonschema
import pytest

from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA, BO_NARROW_JSON_SCHEMA
from app.services.llm.client import LLMRequest
from app.services.llm.providers import strict_schema
from app.services.llm.providers_vertex import VertexProviderBackend
from app.services.llm.providers_vertex_m3 import (
    _LOOSE_ITEM,
    _RELAXED_M3_ARRAYS,
    is_m3_request,
    vertex_m3_response_schema,
    vertex_m3_output_instructions,
)

from tests.integration.test_bo_m3_live_readiness import _live_valid_response


M3_SECTIONS = [
    "schema_version", "borrower_identification", "legal_entities",
    "ownership_relationships", "cash_flow_locations",
    "cash_transfer_relationships", "structural_credit_findings",
    "management_assessments", "governance_screening", "information_gaps",
    "downstream_dependencies",
]


# --- a minimal stand-in prompt/request (no DB, no registry) -----------------


@dataclass
class _FakePrompt:
    name: str
    template: str
    response_schema: dict[str, Any]


def _request(method: str, schema: dict[str, Any], *, model_tier="narrow") -> LLMRequest:
    prompt = _FakePrompt(name=method, template="T", response_schema=schema)
    return LLMRequest(method=method, prompt=prompt, inputs={"x": 1},
                      model_tier=model_tier)


def _settings():
    from app.core.config import Settings

    return Settings(
        llm_provider="vertex", google_cloud_project="proj",
        google_cloud_location="us-central1", vertex_model_narrow="gemini-narrow",
        vertex_model_orchestrator="gemini-orch", vertex_model_challenge="gemini-chal",
        llm_max_input_bytes=1_000_000, llm_max_output_tokens=8000,
    )


# --- a small recursive schema-shape counter ---------------------------------


def _count(schema: dict[str, Any]) -> dict[str, int]:
    """Count forced-required entries, additionalProperties:False, props, arrays, enums."""
    tally = {"required": 0, "addl_false": 0, "props": 0, "arrays": 0, "enums": 0}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                if node.get("additionalProperties") is False:
                    tally["addl_false"] += 1
                tally["required"] += len(node.get("required", []) or [])
                tally["props"] += len(node.get("properties", {}) or {})
            if node.get("type") == "array":
                tally["arrays"] += 1
            if "enum" in node:
                tally["enums"] += 1
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return tally


# --- (a) structural validity ------------------------------------------------


def test_adapted_schema_is_structurally_valid():
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    # Does not raise -> well-formed JSON Schema; NOT proof of Gemini acceptance.
    jsonschema.Draft202012Validator.check_schema(adapted)


# --- (b) materially smaller than strict_schema ------------------------------


def test_adapted_schema_is_materially_smaller_than_strict():
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    strict = strict_schema(BO_M3_JSON_SCHEMA)
    a, s = _count(adapted), _count(strict)
    # Strictly fewer forced-required and fewer closed objects (Stage 1), and
    # reduced breadth across props/arrays/enums (Stage 2).
    assert a["required"] < s["required"]
    assert a["addl_false"] < s["addl_false"]
    assert a["props"] < s["props"]
    assert a["arrays"] < s["arrays"]
    assert a["enums"] < s["enums"]


# --- (c) m3_assessment keeps its 11 required sections -----------------------


def test_adapted_keeps_m3_assessment_required_sections():
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    assert "m3_assessment" in adapted["required"]
    m3 = adapted["properties"]["m3_assessment"]
    assert set(m3["required"]) == set(M3_SECTIONS) | {"management_stability"}
    # The separate sections themselves remain present as keys.
    for section in M3_SECTIONS:
        assert section in m3["properties"]


def test_historical_v1_3_structural_requirements_do_not_change_governance_or_canonical():
    before = copy.deepcopy(BO_M3_JSON_SCHEMA)
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="v1_3")
    properties = adapted["properties"]["m3_assessment"]["properties"]
    required = {"analytical_dimension", "structural_observation", "referenced_node_ids",
                "referenced_link_ids", "onboarding_source_reference", "credit_direction",
                "assessment_availability", "qualifications", "missing_information"}
    assert required <= set(properties["structural_credit_findings"]["items"]["required"])
    original_governance = before["properties"]["m3_assessment"]["properties"]["governance_screening"]["items"]
    assert properties["governance_screening"]["items"]["required"] == original_governance["required"]
    assert BO_M3_JSON_SCHEMA == before


def test_historical_v14_stability_is_provider_open_but_local_contract_rejects_malformed_facts():
    from pathlib import Path

    fixture = json.loads(Path("examples/bo_m3/management_stability/fictional_negative_fixture.json").read_text())
    sample = fixture["response"]
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact")
    assert adapted["properties"]["m3_assessment"]["properties"]["management_stability"] == _LOOSE_ITEM
    jsonschema.validate(sample, adapted)
    jsonschema.validate(sample, BO_M3_JSON_SCHEMA)
    sample["m3_assessment"]["management_stability"]["executives"][0]["facts"][0]["date"] = 123
    jsonschema.validate(sample, adapted)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(sample, BO_M3_JSON_SCHEMA)


def test_m3_provider_requires_explicit_parameter_link_arrays_without_inventing_citations():
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact")
    sample = _live_valid_response()
    sample["parameters"] = [{
        "parameter_id": "management_quality_assessment", "value_type": "categorical",
        "status": "unavailable", "value": None, "evidence_ids": [], "hypothesis_ids": [],
    }]
    jsonschema.validate(sample, adapted)
    jsonschema.validate(sample, BO_M3_JSON_SCHEMA)
    for field in ("evidence_ids", "hypothesis_ids"):
        missing = copy.deepcopy(sample)
        del missing["parameters"][0][field]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(missing, adapted)


# --- (d) the five relaxed arrays advertise a loose item ---------------------


def test_historical_v14_relaxes_the_five_largest_arrays():
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact")
    m3_props = adapted["properties"]["m3_assessment"]["properties"]
    for key in _RELAXED_M3_ARRAYS:
        assert m3_props[key]["type"] == "array"
        assert m3_props[key]["items"] == {"type": "object", "additionalProperties": True}
        assert m3_props[key]["items"] == _LOOSE_ITEM
    # v1.4 additionally relaxed structural findings; governance stayed typed.
    assert m3_props["structural_credit_findings"]["items"] == _LOOSE_ITEM
    assert m3_props["governance_screening"]["items"] != _LOOSE_ITEM


# --- M3-only detection -------------------------------------------------------


def test_is_m3_request_true_for_m3_method_and_schema():
    assert is_m3_request(_request("m3_corporate_management", BO_M3_JSON_SCHEMA)) is True
    # Detected by schema shape even if the method name differs.
    assert is_m3_request(_request("something_else", BO_M3_JSON_SCHEMA)) is True


def test_is_m3_request_false_for_non_m3():
    for method in ("m1_revenue_quality", "m2_industry", "m4_strategy_capital",
                   "bo_v2_orchestrator", "bo_v2_challenger", "business_model"):
        assert is_m3_request(_request(method, BO_NARROW_JSON_SCHEMA)) is False


# --- M3-only application at the Vertex seam ---------------------------------


def test_vertex_seam_uses_adapted_schema_only_for_m3():
    backend = VertexProviderBackend(_settings())

    m3_payload = backend.build_request_payload(
        _request("m3_corporate_management", BO_M3_JSON_SCHEMA))
    assert m3_payload["config"]["response_json_schema"] == \
        vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    # Not the strict schema.
    assert m3_payload["config"]["response_json_schema"] != strict_schema(BO_M3_JSON_SCHEMA)

    for method, tier in (("m2_industry", "narrow"),
                         ("bo_v2_orchestrator", "orchestrator"),
                         ("bo_v2_challenger", "challenge")):
        payload = backend.build_request_payload(
            _request(method, BO_NARROW_JSON_SCHEMA, model_tier=tier))
        assert payload["config"]["response_json_schema"] == \
            strict_schema(BO_NARROW_JSON_SCHEMA)


def test_openai_backend_is_unaffected_by_m3_adaptation():
    # Drive the REAL OpenAI path over a mocked httpx transport for an M3-shaped
    # prompt and assert the wire schema is the strict schema (the adaptation is
    # Vertex-only and never touches the OpenAI backend).
    import httpx
    from pydantic import SecretStr

    from app.core.config import Settings
    from app.services.llm.providers import RealProviderBackend

    sent: dict[str, Any] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "r1", "status": "completed", "model": "m",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"parameters": [], "hypotheses": []}'}]}],
        })

    settings = Settings(
        _env_file=None, debug=False, llm_provider="openai", llm_model="m",
        llm_api_key=SecretStr("fake-test-secret"))
    backend = RealProviderBackend(settings, transport=httpx.MockTransport(handle))
    backend.generate(_request("m3_corporate_management", BO_M3_JSON_SCHEMA))
    wire_schema = sent["body"]["text"]["format"]["schema"]
    assert wire_schema == strict_schema(BO_M3_JSON_SCHEMA)


# --- canonical schema is byte-for-byte unchanged ----------------------------


def test_canonical_schema_is_byte_for_byte_unchanged():
    before = json.dumps(BO_M3_JSON_SCHEMA, sort_keys=True)
    vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    after = json.dumps(BO_M3_JSON_SCHEMA, sort_keys=True)
    assert before == after


# --- complete samples and the independent canonical gate --------------------


def test_sample_valid_against_adapted_and_canonical():
    sample = _live_valid_response()
    adapted = vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact")
    # This sample is valid against both; this is not a general implication.
    jsonschema.validate(sample, adapted)
    jsonschema.validate(sample, BO_M3_JSON_SCHEMA)


def _complete_response():
    """Fictional test case with nonempty items in all five relaxed arrays."""
    from tests.unit.test_bo_m3_corporate_refinement import proposal

    sample = _live_valid_response()
    sample["m3_assessment"] = proposal()
    for finding in sample["m3_assessment"]["structural_credit_findings"]:
        finding.pop("validation_reasons")  # validator output, not a model field
        finding["hypothesis_id"] = None
        finding.update(analytical_dimension="cash_flow_access",
                       structural_observation="Fictional entities have a distribution dependency.",
                       referenced_node_ids=["holdco", "opco"], referenced_link_ids=[],
                       onboarding_source_reference=None, credit_direction="negative",
                       assessment_availability="insufficient_evidence", qualifications=[],
                       missing_information=["Definitive transfer rights require legal review."])
    for observation in sample["m3_assessment"]["management_assessments"]:
        observation.pop("validation_reasons")
    return sample


def test_nonempty_relaxed_arrays_valid_against_both_contracts():
    sample = _complete_response()
    for section in _RELAXED_M3_ARRAYS:
        assert sample["m3_assessment"][section]
    jsonschema.validate(sample, BO_M3_JSON_SCHEMA)
    jsonschema.validate(sample, vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact"))


@pytest.mark.parametrize("section", (*_RELAXED_M3_ARRAYS, "structural_credit_findings"))
def test_provider_valid_malformed_items_fail_local_canonical_validation(section, db_session):
    from app.prompts.registry import PromptRegistry
    from app.services.llm.client import FakeLLMBackend, LLMClient

    sample = _complete_response()
    sample["m3_assessment"][section] = [{}]
    before = copy.deepcopy(sample)
    jsonschema.validate(sample, vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact"))
    backend = FakeLLMBackend()
    backend.register("m3_corporate_management", sample)
    result = LLMClient(backend, prompts=PromptRegistry(db_session)).generate_only(
        _request("m3_corporate_management", BO_M3_JSON_SCHEMA))
    assert result.parsed is None
    assert result.validation_outcome == "rejected"
    assert "Response failed schema validation" in result.validation_detail
    assert sample == before  # no defaults, repairs, or evidence IDs were inserted
    assert json.loads(result.raw.raw_response) == before


@pytest.mark.parametrize("section", M3_SECTIONS)
def test_each_substantive_section_still_required(section):
    sample = _live_valid_response()
    del sample["m3_assessment"][section]
    for schema in (BO_M3_JSON_SCHEMA, vertex_m3_response_schema(BO_M3_JSON_SCHEMA)):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(sample, schema)


def test_generated_contract_covers_all_omitted_item_fields_and_enums():
    canonical = copy.deepcopy(BO_M3_JSON_SCHEMA)
    instructions = vertex_m3_output_instructions(canonical)
    props = canonical["properties"]["m3_assessment"]["properties"]
    positions = []
    for section in _RELAXED_M3_ARRAYS:
        line = next(line for line in instructions.splitlines()
                    if line.startswith(f"m3_assessment.{section}[]:"))
        positions.append(instructions.index(line))
        item = props[section]["items"]
        for name, field in item["properties"].items():
            presence = "required" if name in item["required"] else "optional"
            assert f"{name} ({presence}):" in line
            if "enum" in field:
                assert json.dumps(field["enum"], separators=(",", ":")) in line
    assert positions == sorted(positions)
    # Changes to the canonical field contract propagate without a parallel schema.
    props["management_assessments"]["items"]["properties"]["role"]["type"] = ["string", "null"]
    assert "role (required): string|null" in vertex_m3_output_instructions(canonical)
    assert "role (required): string|null" not in instructions


def test_m3_payload_contains_contract_and_bounded_transport_only_for_m3():
    backend = VertexProviderBackend(_settings())
    req = _request("m3_corporate_management", BO_M3_JSON_SCHEMA)
    payload = backend.build_request_payload(req)
    config = payload["config"]
    assert vertex_m3_output_instructions(BO_M3_JSON_SCHEMA) in config["system_instruction"]
    assert config["http_options"] == {
        "timeout": 120_000, "retry_options": {"attempts": 1}}
    for method in ("m1_revenue_quality", "m2_industry", "m4_strategy_capital"):
        other = backend.build_request_payload(_request(method, BO_NARROW_JSON_SCHEMA))
        assert "http_options" not in other["config"]
        assert "M3 output contract" not in other["config"]["system_instruction"]


def test_m3_contract_counts_toward_input_limit():
    from app.services.llm.providers import ProviderCallError

    settings = _settings().model_copy(update={"llm_max_input_bytes": 1000})
    with pytest.raises(ProviderCallError, match="M3 input and output instructions exceed"):
        VertexProviderBackend(settings).build_request_payload(
            _request("m3_corporate_management", BO_M3_JSON_SCHEMA))


@pytest.mark.parametrize("http_status", [200, 400, 503])
def test_installed_google_sdk_serializes_m3_and_never_retries_with_mock_transport(http_status):
    """Exercise SDK conversion and actual ClientError metadata without network/ADC."""
    import httpx

    genai = pytest.importorskip("google.genai")
    from app.core.hashing import content_hash
    from app.services.llm.providers import ProviderCallError

    sent = []
    secret = "PRIVATE-provider-body-must-not-appear"
    sample = _live_valid_response()

    def handle(request):
        sent.append(json.loads(request.content))
        if http_status != 200:
            status = "INVALID_ARGUMENT" if http_status == 400 else "UNAVAILABLE"
            return httpx.Response(http_status, json={"error": {
                "code": http_status, "status": status, "message": secret}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": [
                {"text": json.dumps(sample)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50,
                              "totalTokenCount": 150},
        })

    client = genai.Client(vertexai=True, api_key="offline-fake-key", http_options={
        "client_args": {"transport": httpx.MockTransport(handle)}})
    backend = VertexProviderBackend(_settings(), client_factory=lambda: client)
    request = _request("m3_corporate_management", BO_M3_JSON_SCHEMA)
    try:
        if http_status == 200:
            result = backend.generate(request)
            assert json.loads(result.raw_response) == sample
            assert result.model_config["input_tokens"] == 100
            assert result.model_config["canonical_schema_sha256"] == content_hash(BO_M3_JSON_SCHEMA)
            assert result.model_config["schema_adapter"] == "vertex-m3-1.5"
        else:
            with pytest.raises(ProviderCallError) as error:
                backend.generate(request)
            assert f"HTTP {http_status}" in str(error.value)
            assert secret not in str(error.value)
    finally:
        client.close()
    assert len(sent) == 1  # even HTTP 503 does not trigger an SDK retry
    assert sent[0]["generationConfig"]["responseJsonSchema"] == \
        vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
    assert "httpOptions" not in sent[0]["generationConfig"]
