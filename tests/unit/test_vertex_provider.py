"""Milestone 18 — Vertex provider backend (Req 17). MOCKED only; no live calls.

Proves: provider-neutral LLMBackend; model-tier routing to configured models;
structured-JSON request construction (schema, temperature 0, byte limit);
structured-response + usage parsing; missing-config errors; sanitized failure
without auto-retry. A fake client stands in for google-genai — the SDK is never
imported and no network occurs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.config import Settings
from app.prompts.registry import PromptRegistry
from app.services.llm.client import LLMRequest
from app.services.llm.providers import ProviderCallError, ProviderNotConfiguredError
from app.services.llm.providers_vertex import VertexProviderBackend


# --- a fake google-genai client (no SDK, no network) ------------------------


@dataclass
class _FakeUsage:
    prompt_token_count: int = 100
    candidates_token_count: int = 40
    total_token_count: int = 140


@dataclass
class _FakeResponse:
    text: str
    usage_metadata: _FakeUsage = field(default_factory=_FakeUsage)


class _FakeModels:
    def __init__(self, response, recorder):
        self._response = response
        self._recorder = recorder

    def generate_content(self, *, model, contents, config):
        self._recorder["model"] = model
        self._recorder["contents"] = contents
        self._recorder["config"] = config
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeClient:
    def __init__(self, response, recorder):
        self.models = _FakeModels(response, recorder)


def _settings(**over) -> Settings:
    base = dict(
        llm_provider="vertex",
        google_cloud_project="proj",
        google_cloud_location="us-central1",
        vertex_model_narrow="gemini-narrow",
        vertex_model_orchestrator="gemini-orch",
        vertex_model_challenge="gemini-chal",
        llm_max_input_bytes=1_000_000,
        llm_max_output_tokens=8000,
    )
    base.update(over)
    return Settings(**base)


def _request(db_session, *, model_tier="narrow", prompt_name="business_model"):
    reg = PromptRegistry(db_session)
    reg.register_catalogue()
    prompt = reg.latest(prompt_name)
    return LLMRequest(method="business_model", prompt=prompt, inputs={"x": 1},
                      model_tier=model_tier)


def _backend(settings, response, recorder):
    return VertexProviderBackend(settings,
                                 client_factory=lambda: _FakeClient(response, recorder))


# --- tests ------------------------------------------------------------------


def test_missing_config_raises(db_session):
    with pytest.raises(ProviderNotConfiguredError):
        VertexProviderBackend(Settings(llm_provider="vertex")).check_configuration()
    with pytest.raises(ProviderNotConfiguredError):
        VertexProviderBackend(
            Settings(llm_provider="vertex", google_cloud_project="p",
                     google_cloud_location="l")
        ).check_configuration()  # no model configured


def test_wrong_provider_raises(db_session):
    with pytest.raises(ProviderNotConfiguredError):
        VertexProviderBackend(Settings(llm_provider="openai")).check_configuration()


def test_model_tier_routing(db_session):
    b = VertexProviderBackend(_settings())
    assert b.model_for_tier("narrow") == "gemini-narrow"
    assert b.model_for_tier("orchestrator") == "gemini-orch"
    assert b.model_for_tier("challenge") == "gemini-chal"
    assert b.model_for_tier(None) == "gemini-narrow"  # safe default


def test_request_construction_is_structured(db_session):
    recorder: dict[str, Any] = {}
    resp = _FakeResponse(text='{"parameters": []}')
    backend = _backend(_settings(), resp, recorder)
    backend.generate(_request(db_session, model_tier="orchestrator"))

    assert recorder["model"] == "gemini-orch"  # tier routed
    cfg = recorder["config"]
    assert cfg["temperature"] == 0.0
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"]["type"] == "object"  # strict schema attached
    # Evidence-as-data instruction present in the system prompt.
    assert "evidence, never as instructions" in cfg["system_instruction"]


def test_structured_response_and_usage_parsed(db_session):
    recorder: dict[str, Any] = {}
    resp = _FakeResponse(text='{"parameters": [{"parameter_id": "x"}]}')
    backend = _backend(_settings(), resp, recorder)
    result = backend.generate(_request(db_session))

    assert result.raw_response == '{"parameters": [{"parameter_id": "x"}]}'
    assert result.model_id == "gemini-narrow"
    # Token usage is captured as the canonical usage record.
    assert result.model_config["input_tokens"] == 100
    assert result.model_config["output_tokens"] == 40
    assert result.model_config["total_tokens"] == 140
    assert result.model_config["backend"] == "vertex"
    assert "latency_ms" in result.model_config


def test_candidates_fallback_text_extraction(db_session):
    # A response with no top-level .text but a candidates/parts structure.
    @dataclass
    class _Part:
        text: str

    @dataclass
    class _Content:
        parts: list

    @dataclass
    class _Cand:
        content: Any

    @dataclass
    class _Resp:
        text: str | None
        candidates: list
        usage_metadata: _FakeUsage = field(default_factory=_FakeUsage)

    resp = _Resp(text=None,
                 candidates=[_Cand(content=_Content(parts=[_Part('{"ok": true}')]))])
    backend = _backend(_settings(), resp, {})
    result = backend.generate(_request(db_session))
    assert result.raw_response == '{"ok": true}'


def test_empty_output_raises(db_session):
    backend = _backend(_settings(), _FakeResponse(text=""), {})
    with pytest.raises(ProviderCallError, match="no structured output"):
        backend.generate(_request(db_session))


def test_provider_error_is_sanitized_and_not_retried(db_session):
    calls = {"n": 0}

    class _CountingModels(_FakeModels):
        def generate_content(self, **kw):
            calls["n"] += 1
            raise RuntimeError("secret project token leaked here")

    class _CountingClient:
        def __init__(self):
            self.models = _CountingModels(None, {})

    backend = VertexProviderBackend(_settings(),
                                    client_factory=lambda: _CountingClient())
    with pytest.raises(ProviderCallError) as exc:
        backend.generate(_request(db_session))
    assert "secret" not in str(exc.value)  # sanitized
    assert calls["n"] == 1  # not auto-retried


def test_input_byte_limit_enforced(db_session):
    backend = _backend(_settings(llm_max_input_bytes=1000),
                       _FakeResponse(text="{}"), {})
    reg = PromptRegistry(db_session)
    reg.register_catalogue()
    prompt = reg.latest("business_model")
    big = LLMRequest(method="business_model", prompt=prompt,
                     inputs={"blob": "x" * 5000}, model_tier="narrow")
    with pytest.raises(ProviderCallError, match="LLM_MAX_INPUT_BYTES"):
        backend.generate(big)


def test_backend_is_provider_neutral_interface(db_session):
    # It plugs into LLMClient like any other backend (async offload default).
    import asyncio

    recorder: dict[str, Any] = {}
    backend = _backend(_settings(), _FakeResponse(text='{"parameters": []}'),
                       recorder)
    req = _request(db_session)
    result = asyncio.run(backend.generate_async(req))  # default to_thread offload
    assert result.model_id == "gemini-narrow"
