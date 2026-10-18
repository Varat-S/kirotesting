"""Focused checks for the temporary recorded-agent PDF exporter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.temporary_agent_io_pdf import (
    AgentReportError,
    _match_model_runs,
    _redact,
    _safe_model_config,
)


def test_redaction_removes_nested_credentials_but_keeps_token_metrics():
    value = {
        "GOOGLE_API_KEY": "secret-value",
        "nested": {"authorization-header": "Bearer secret", "input_tokens": 123},
        "max_output_tokens": 4096,
    }
    assert _redact(value) == {
        "GOOGLE_API_KEY": "[REDACTED]",
        "nested": {"authorization-header": "[REDACTED]", "input_tokens": 123},
        "max_output_tokens": 4096,
    }
    assert _safe_model_config({**value, "unrelated_provider_field": "omit"}) == {
        "max_output_tokens": 4096,
    }


def test_model_run_matching_uses_recorded_identity_and_nearest_timestamp():
    now = datetime.now(UTC)
    agent = SimpleNamespace(
        run_id="agent-1", agent_id="agent", reused_from_cache=False,
        case_id="CASE", snapshot_version=3, prompt_id="prompt", model_id="gemini",
        raw_response="same response", created_at=now,
    )
    older = SimpleNamespace(
        run_id="model-old", case_id="CASE", case_version=3, prompt_id="prompt",
        model_id="gemini", raw_response="same response", created_at=now - timedelta(hours=1),
    )
    nearest = SimpleNamespace(
        run_id="model-near", case_id="CASE", case_version=3, prompt_id="prompt",
        model_id="gemini", raw_response="same response", created_at=now + timedelta(milliseconds=2),
    )
    assert _match_model_runs([agent], [older, nearest])["agent-1"] is nearest


def test_model_run_matching_fails_closed_when_source_cannot_be_resolved():
    agent = SimpleNamespace(
        run_id="agent-1", agent_id="agent", reused_from_cache=False,
        case_id="CASE", snapshot_version=3, prompt_id="prompt", model_id="gemini",
        raw_response="response", created_at=None,
    )
    with pytest.raises(AgentReportError, match="No recorded model run"):
        _match_model_runs([agent], [])
