"""Real request construction and failure handling with an injected HTTP transport."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.orm import ModelRun
from app.prompts.registry import PromptRegistry
from app.services.llm.client import LLMClient, LLMRequest
from app.services.llm.providers import (
    ProviderCallError,
    ProviderNotConfiguredError,
    RealProviderBackend,
)


def settings(**kwargs):
    return Settings(
        _env_file=None,
        debug=False,
        llm_provider="openai",
        llm_model="configured-test-model",
        llm_api_key=SecretStr("fake-test-secret"),
        **kwargs,
    )


def test_responses_request_uses_exact_evidence_and_strict_schema(db_session):
    prompt = PromptRegistry(db_session).register_catalogue()[0]

    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert json.loads(body["input"][1]["content"]) == {
            "canonical_evidence": {"test": "source"}
        }
        assert body["store"] is False
        fmt = body["text"]["format"]
        assert fmt["strict"] is True and fmt["schema"]["additionalProperties"] is False
        assert set(fmt["schema"]["required"]) == set(fmt["schema"]["properties"])
        return httpx.Response(
            200,
            json={
                "id": "response-test",
                "status": "completed",
                "model": "actual-model",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"facts":[],"warnings":[]}',
                            }
                        ],
                    }
                ],
            },
        )

    backend = RealProviderBackend(settings(), transport=httpx.MockTransport(handle))
    raw = backend.generate(
        LLMRequest("extract", prompt, inputs={"canonical_evidence": {"test": "source"}})
    )
    assert raw.model_id == "actual-model"
    assert "fake-test-secret" not in str(raw.model_config)
    assert raw.model_config["request_inputs_sha256"]


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "incomplete", "output": []},
        {
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}
            ],
        },
        {"status": "completed", "output": []},
    ],
)
def test_refusal_incomplete_empty_are_rejected(db_session, reply):
    prompt = PromptRegistry(db_session).register_catalogue()[0]
    backend = RealProviderBackend(
        settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply)),
    )
    with pytest.raises(ProviderCallError):
        backend.generate(LLMRequest("extract", prompt))


@pytest.mark.parametrize(
    "failure", ["timeout", "unauthorized", "invalid_json", "invalid_schema"]
)
def test_provider_errors_are_recorded_without_credentials(db_session, failure):
    prompts = PromptRegistry(db_session)
    prompts.register_catalogue()

    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("fake-test-secret", request=request)
        if failure == "unauthorized":
            return httpx.Response(401, text="fake-test-secret")
        if failure == "invalid_json":
            return httpx.Response(200, text="bad-json")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"invented":true}'}
                        ],
                    }
                ],
            },
        )

    backend = RealProviderBackend(settings(), transport=httpx.MockTransport(handle))
    run = LLMClient(backend, prompts=prompts, session=db_session).extract(
        {}, case_id="PROVIDER", case_version=1
    )
    assert not run.is_valid
    row = db_session.get(ModelRun, run.run_id)
    assert row.validation_outcome == "rejected"
    assert "fake-test-secret" not in str(vars(row))


def test_compatible_local_backend_and_input_limits(db_session):
    prompt = PromptRegistry(db_session).register_catalogue()[0]

    def handle(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["response_format"]["json_schema"]["strict"]
        return httpx.Response(
            200,
            json={
                "model": "local",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"facts":[],"warnings":[]}'},
                    }
                ],
            },
        )

    config = Settings(
        _env_file=None,
        debug=False,
        llm_provider="openai_compatible",
        llm_model="local",
        llm_base_url="http://127.0.0.1:1234/v1",
        llm_max_input_bytes=1000,
    )
    backend = RealProviderBackend(config, transport=httpx.MockTransport(handle))
    assert backend.generate(LLMRequest("extract", prompt)).model_id == "local"
    with pytest.raises(ProviderCallError, match="silently truncated"):
        backend.generate(LLMRequest("extract", prompt, inputs={"large": "x" * 2000}))
    config.llm_base_url = "http://external.example/v1"
    with pytest.raises(ProviderNotConfiguredError):
        backend.check_configuration()
