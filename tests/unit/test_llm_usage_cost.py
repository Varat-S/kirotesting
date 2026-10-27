"""Per-run token meter and cost estimate. MOCKED providers only; no live calls."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.prompts.registry import PromptRegistry
from app.services.llm import usage
from app.services.llm.client import LLMRequest
from app.services.llm.providers import ProviderCallError, RealProviderBackend
from app.services.llm.providers_vertex import VertexProviderBackend

from tests.unit.test_vertex_provider import _FakeClient, _FakeResponse, _request, _settings


@pytest.fixture(autouse=True)
def _clean_meter():
    usage.reset()
    yield
    usage.reset()


def test_cost_from_prices_with_thinking_at_output_rate():
    usage.record("m", {"input_tokens": 1_000_000, "output_tokens": 500_000, "thinking_tokens": 500_000})
    usage.record("m", {"input_tokens": 1_000_000})
    cost = usage.summary({"m": {"input": 0.10, "output": 0.40}})
    assert cost["calls"] == 2
    assert cost["by_model"]["m"]["cost_usd"] == pytest.approx(0.2 + 0.4)
    assert cost["total_cost_usd"] == pytest.approx(0.6)
    assert usage.describe(cost) == "Cost: ~US$0.6000 (2 model call(s), 3,000,000 tokens)"


def test_unpriced_model_reports_tokens_and_no_total():
    usage.record("priced", {"input_tokens": 1_000_000})
    usage.record("unknown", {"input_tokens": 10, "output_tokens": 5})
    cost = usage.summary({"priced": {"input": 1.0, "output": 2.0}})
    assert cost["total_cost_usd"] is None
    assert cost["priced_cost_usd"] == pytest.approx(1.0)
    assert cost["unpriced_models"] == ["unknown"]
    assert "no price set for unknown" in usage.describe(cost)


def test_vertex_call_is_metered(db_session):
    recorder = {}
    backend = VertexProviderBackend(
        _settings(), client_factory=lambda: _FakeClient(_FakeResponse(text='{"a": 1}'), recorder))
    backend.generate(_request(db_session, model_tier="narrow"))
    cost = usage.summary({})
    assert cost["by_model"]["gemini-narrow"]["input_tokens"] == 100
    assert cost["by_model"]["gemini-narrow"]["output_tokens"] == 40


def test_vertex_empty_output_is_still_metered(db_session):
    backend = VertexProviderBackend(
        _settings(), client_factory=lambda: _FakeClient(_FakeResponse(text=""), {}))
    with pytest.raises(ProviderCallError):
        backend.generate(_request(db_session))
    assert usage.summary({})["calls"] == 1


def test_openai_usage_is_captured_and_metered(db_session):
    prompt = PromptRegistry(db_session).register_catalogue()[0]

    def handle(request):
        return httpx.Response(200, json={
            "id": "r", "status": "completed", "model": "actual-model",
            "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"facts": [], "warnings": []})}]}],
        })

    backend = RealProviderBackend(
        Settings(_env_file=None, llm_provider="openai", llm_model="m",
                 llm_api_key=SecretStr("fake-test-secret")),
        transport=httpx.MockTransport(handle))
    raw = backend.generate(LLMRequest(method="extract", prompt=prompt, inputs={}))
    assert raw.model_config["input_tokens"] == 12 and raw.model_config["output_tokens"] == 7
    assert usage.summary({})["by_model"]["actual-model"]["input_tokens"] == 12


def test_llm_prices_parses_from_env(monkeypatch):
    monkeypatch.setenv("LLM_PRICES", '{"gemini-x": {"input": 0.1, "output": 0.4}}')
    assert Settings(_env_file=None).llm_prices == {"gemini-x": {"input": 0.1, "output": 0.4}}


def test_each_backend_meters_only_its_own_calls(db_session):
    def backend():
        return VertexProviderBackend(
            _settings(), client_factory=lambda: _FakeClient(_FakeResponse(text='{"a": 1}'), {}))

    first, second = backend(), backend()
    first.generate(_request(db_session))
    first.generate(_request(db_session))
    second.generate(_request(db_session))
    assert usage.summary({}, meters=[first.usage])["calls"] == 2
    assert usage.summary({}, meters=[second.usage])["calls"] == 1
    assert usage.summary({}, meters=[first.usage, second.usage, None])["tokens"] == 3 * 140
    assert usage.summary({})["calls"] == 3  # the process meter sees all of them


def test_web_job_reports_cost_per_section_and_total(monkeypatch):
    from app.services import bo_fo

    bo_backend, fo_backend = RealProviderBackend(Settings(_env_file=None)), RealProviderBackend(Settings(_env_file=None))
    bo_backend.usage.record("m", {"input_tokens": 1_000_000})
    fo_backend.usage.record("m", {"output_tokens": 1_000_000})
    monkeypatch.setattr(bo_fo, "run_bo_fo", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(usage, "summary", lambda prices=None, meters=None, _real=usage.summary:
                        _real({"m": {"input": 1.0, "output": 2.0}}, meters))
    jobs = bo_fo.Jobs()
    job_id = jobs.start(folder="f", bo_backend=bo_backend, fo_backend=fo_backend)
    cost = jobs.get(job_id)["cost"]
    assert cost["bo"]["total_cost_usd"] == 1.0 and cost["fo"]["total_cost_usd"] == 2.0
    assert cost["total"]["total_cost_usd"] == 3.0 and cost["total"]["text"].startswith("~US$3.0000")
    assert jobs.recent()[0]["cost"]["total"]["calls"] == 2


def test_scripted_web_job_has_no_cost():
    from app.services import bo_fo

    jobs = bo_fo.Jobs()
    job = jobs._with_cost({"job_id": "unknown"})
    assert job["cost"]["total"]["text"] == "no model calls"
