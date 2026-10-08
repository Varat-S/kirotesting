"""Milestone 2.2 — agent runtime executes one AgentTask with no DB mutation.

The runtime drives the single LLMClient choke point, validates output, and
assembles a typed AgentExecutionResult + an UNPERSISTED AgentRun. Provider
failures surface as a typed error result, never a raised exception (Req 30).
"""

from __future__ import annotations

import asyncio

from app.models.orm import ModelRun
from app.prompts.registry import PromptRegistry
from app.schemas.agentic import (
    AgentTask,
    ExecutionStatus,
    ModelTier,
    Topic,
    ValidationStatus,
)
from app.services.agents.runtime import AgentRuntime
from app.services.llm.client import FakeLLMBackend, LLMClient, LLMRequest, LLMRawResult


def _client(db_session, backend=None):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = backend or FakeLLMBackend()
    return LLMClient(backend, prompts=registry, session=db_session), backend


def _task(**overrides) -> AgentTask:
    base = dict(
        agent_id="qualitative_extractor",
        analysis_run_id="AR1",
        topic=Topic.BUSINESS,
        task_type="extract",
        packet_ref="packet-hash-1",
        prompt_name="qualitative_extraction",
        response_schema_ref="qualitative_extraction",
        model_tier=ModelTier.NARROW,
        case_id="C1",
        snapshot_version=1,
    )
    base.update(overrides)
    return AgentTask(**base)


def _valid_extraction():
    return {
        "facts": [
            {
                "fact_id": "f1",
                "name": "CEO tenure",
                "topic": "management",
                "statement": "CEO since 2016.",
                "status": "unverified",
                "confidence": 0.8,
                "source_refs": [{"document_id": "d1", "page": 4}],
            }
        ]
    }


def test_runtime_executes_task_without_db_mutation(db_session):
    client, backend = _client(db_session)
    backend.register("extract", _valid_extraction())
    runtime = AgentRuntime(client)

    out = runtime.run(_task(), {}, agent_definition_hash="adh-1", evidence_ids=["E1"])
    db_session.flush()

    assert db_session.query(ModelRun).count() == 0  # DB-free
    assert out.execution.status is ExecutionStatus.OK
    assert out.execution.parsed is not None
    assert out.agent_run.validation_status is ValidationStatus.VALID
    assert out.agent_run.agent_definition_hash == "adh-1"
    assert out.agent_run.input_hash == "packet-hash-1"
    assert out.agent_run.input_evidence_ids == ["E1"]
    assert out.agent_run.analysis_run_id == "AR1"
    assert out.agent_run.run_id  # assigned, but not persisted


def test_runtime_marks_schema_invalid_output_rejected(db_session):
    client, backend = _client(db_session)
    backend.register("extract", "not-json")
    runtime = AgentRuntime(client)

    out = runtime.run(_task(), {}, agent_definition_hash="adh-1")

    assert out.execution.status is ExecutionStatus.REJECTED
    assert out.agent_run.validation_status is ValidationStatus.REJECTED
    assert out.agent_run.validation_detail


def test_runtime_captures_provider_error_as_typed_result(db_session):
    class ExplodingBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            raise RuntimeError("boom with secret token")

    client, backend = _client(db_session, backend=ExplodingBackend())
    runtime = AgentRuntime(client)

    # No exception escapes; a typed error result is returned instead (Req 30).
    out = runtime.run(_task(), {}, agent_definition_hash="adh-1")
    assert out.execution.status is ExecutionStatus.ERROR
    assert out.execution.error_type == "RuntimeError"
    assert out.agent_run.validation_status is ValidationStatus.ERROR
    # Secret must not leak into the stored raw response.
    assert "secret" not in (out.agent_run.raw_response or "")


def test_runtime_async_matches_sync(db_session):
    client, backend = _client(db_session)
    backend.register("extract", _valid_extraction())
    runtime = AgentRuntime(client)

    out = asyncio.run(
        runtime.run_async(_task(), {}, agent_definition_hash="adh-1")
    )
    db_session.flush()
    assert db_session.query(ModelRun).count() == 0
    assert out.execution.status is ExecutionStatus.OK
    assert out.agent_run.validation_status is ValidationStatus.VALID


def test_runtime_output_can_be_logged_serially(db_session):
    client, backend = _client(db_session)
    backend.register("extract", _valid_extraction())
    runtime = AgentRuntime(client)

    out = runtime.run(_task(), {}, agent_definition_hash="adh-1")
    # The executor's serial persist phase logs the underlying provider run.
    run = client.log_run(out.generate_result)
    db_session.flush()
    assert db_session.query(ModelRun).count() == 1
    assert run.validation_outcome == "valid"
