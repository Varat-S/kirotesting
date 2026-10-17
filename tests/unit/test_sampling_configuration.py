"""Provider sampling configuration through mocked requests; never live calls."""
import json

import httpx
import pytest

from app.core.config import Settings
from app.services.llm.providers import RealProviderBackend
from app.services.llm.sampling import sampling_temperature
from tests.unit.test_vertex_provider import _backend, _FakeResponse, _request, _settings


@pytest.mark.parametrize("model,expected", [
    ("gemini-3-pro-preview", 1.0), ("gemini-3.1-pro-preview", 1.0),
    ("publishers/google/models/gemini-3-flash-preview", 1.0),
    ("gemini-2.5-pro", 0.0),
])
def test_vertex_model_default_and_recorded_effective_temperature(db_session, model, expected):
    recorder = {}
    backend = _backend(_settings(vertex_model_narrow=model), _FakeResponse('{"ok": true}'), recorder)
    result = backend.generate(_request(db_session))
    assert recorder["config"]["temperature"] == expected
    assert recorder["model"] == result.model_id == model
    assert result.model_config["requested_temperature"] is None
    assert result.model_config["effective_temperature"] == expected
    assert result.model_config["backend"] == "vertex"


def test_vertex_explicit_override_precedes_config(db_session):
    recorder = {}
    backend = _backend(_settings(vertex_model_narrow="gemini-3-pro-preview", llm_temperature=.7), _FakeResponse('{}'), recorder)
    request = _request(db_session)
    assert backend.generate(request).model_config["effective_temperature"] == .7
    request.temperature = 0
    result = backend.generate(request)
    assert recorder["config"]["temperature"] == 0
    assert result.model_config["requested_temperature"] == 0


@pytest.mark.parametrize("value", [-.1, 2.1, float("nan"), float("inf")])
def test_invalid_temperature_fails_before_provider_call(db_session, value):
    recorder = {}
    backend = _backend(_settings(vertex_model_narrow="gemini-3-pro-preview"), _FakeResponse('{}'), recorder)
    request = _request(db_session)
    request.temperature = value
    with pytest.raises(ValueError, match="Temperature"):
        backend.generate(request)
    assert not recorder


def test_declared_model_range_and_unsupported_parameters(db_session):
    settings = _settings(llm_temperature_max=1)
    with pytest.raises(ValueError):
        sampling_temperature(settings, 1.1, "gemini-3-pro-preview")
    settings = _settings(llm_temperature_supported=False)
    recorder = {}
    backend = _backend(settings, _FakeResponse('{}'), recorder)
    backend.generate(_request(db_session))
    assert "temperature" not in recorder["config"]
    with pytest.raises(ValueError, match="support"):
        sampling_temperature(settings, .5, "unsupported-model")


@pytest.mark.parametrize("provider,supported,override,expected", [
    ("openai", None, None, None),
    ("openai", True, .4, .4),
    ("openai_compatible", None, None, 0),
    ("openai_compatible", False, None, None),
])
def test_real_provider_request_and_metadata(db_session, provider, supported, override, expected):
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content))
        if provider == "openai":
            return httpx.Response(200, json={"status":"completed", "model":"test-model", "output":[{"type":"message", "content":[{"type":"output_text", "text":"{}"}]}]})
        return httpx.Response(200, json={"model":"test-model", "choices":[{"finish_reason":"stop", "message":{"content":"{}"}}]})
    settings = Settings(llm_provider=provider, llm_model="test-model", llm_api_key="test-only",
                        llm_temperature=override, llm_temperature_supported=supported)
    backend = RealProviderBackend(settings, transport=httpx.MockTransport(handler))
    result = backend.generate(_request(db_session))
    assert captured.get("temperature") == expected
    assert ("temperature" in captured) == (expected is not None)
    assert result.model_config["effective_temperature"] == expected
    assert result.model_config["requested_temperature"] == override


def test_unconfirmed_openai_override_fails_before_network(db_session):
    settings = Settings(llm_provider="openai", llm_model="test-model", llm_api_key="test-only", llm_temperature=.5)
    with pytest.raises(ValueError, match="confirmed model support"):
        RealProviderBackend(settings).generate(_request(db_session))


def test_cache_identity_changes_with_sampling_and_model_tier():
    from app.services.llm.providers_vertex import VertexProviderBackend
    from app.services.bo.transcript import RecordingBackend
    first = VertexProviderBackend(_settings(vertex_model_narrow="gemini-3-pro-preview"))
    changed = VertexProviderBackend(_settings(vertex_model_narrow="gemini-3-pro-preview", llm_temperature=.2))
    assert first.cache_identity("narrow") != changed.cache_identity("narrow")
    assert first.cache_identity("narrow")[0] != first.cache_identity("challenge")[0]
    assert RecordingBackend(changed).cache_identity("narrow") == changed.cache_identity("narrow")


def test_executor_cache_key_uses_provider_sampling_configuration(db_session):
    from app.services.agents.executor import ExecutionContext
    from app.services.llm.providers_vertex import VertexProviderBackend
    from tests.integration.test_dag_executor import _builder, _executor, _registry
    context = ExecutionContext(analysis_run_id="AR1", case_id="C1", snapshot_version=1)
    task = _builder(_registry().get("a"), context)
    first = VertexProviderBackend(_settings(vertex_model_narrow="gemini-3-pro-preview"))
    changed = VertexProviderBackend(_settings(vertex_model_narrow="gemini-3-pro-preview", llm_temperature=.2))
    before = _executor(db_session, first)._cache_identity(task)
    after = _executor(db_session, changed)._cache_identity(task)
    assert before.model_config["effective_temperature"] == 1.0
    assert after.model_config["effective_temperature"] == .2
    assert before.key() != after.key()
