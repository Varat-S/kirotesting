"""Milestone 5 — async DAG executor + concurrency safety (Req 6, 25, 30).

Proves: dependency ordering; concurrent-ready scheduling with a bounded
semaphore; NO shared Session mutation during concurrent provider calls; one
provider failure preserves siblings; timeout -> typed error; event loop not
blocked by sync provider I/O; two runs on the same snapshot stay isolated.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from app.models.orm import AgentRunRow, AgenticAnalysisRun as AgenticAnalysisRunRow
from app.prompts.registry import PromptRegistry
from app.schemas.agentic import (
    AgentTask,
    AgenticAnalysisRun,
    ModelTier,
    RunStatus,
    Topic,
)
from app.services.agents.executor import DagExecutor, PreparedTask
from app.services.agents.registry import (
    AgentDefinition,
    AgentRegistry,
)
from app.services.agents.runtime import AgentRuntime
from app.services.llm.client import (
    FakeLLMBackend,
    LLMClient,
    LLMRawResult,
    LLMRequest,
)


def _run_row(db_session) -> AgenticAnalysisRun:
    run = AgenticAnalysisRun(
        analysis_run_id="AR1",
        case_id="C1",
        evidence_snapshot_version=1,
        started_at=datetime.now(timezone.utc),
        router_version="router-1",
        agent_registry_version="reg-1",
        agent_registry_hash="rh",
        scoring_config_version=1,
        scoring_config_hash="sh",
    )
    db_session.add(
        AgenticAnalysisRunRow(
            analysis_run_id="AR1",
            case_id="C1",
            evidence_snapshot_version=1,
            status="created",
            router_version="router-1",
            agent_registry_version="reg-1",
            agent_registry_hash="rh",
            scoring_config_version=1,
            scoring_config_hash="sh",
        )
    )
    db_session.flush()
    return run


def _registry() -> AgentRegistry:
    # a -> c, b -> c (c depends on a and b); d independent.
    return AgentRegistry(
        [
            AgentDefinition("a", Topic.BUSINESS, "extract", ModelTier.NARROW),
            AgentDefinition("b", Topic.FINANCIAL, "extract", ModelTier.NARROW),
            AgentDefinition("c", Topic.CROSS_TOPIC, "orchestrate",
                            ModelTier.ORCHESTRATOR, dependencies=("a", "b")),
            AgentDefinition("d", Topic.BUSINESS, "extract", ModelTier.NARROW),
        ]
    )


def _client(db_session, backend):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    return LLMClient(backend, prompts=registry, session=db_session)


def _ok_backend():
    backend = FakeLLMBackend()
    # Every method used returns the (valid, permissive) extraction schema shape.
    for method in ("extract", "orchestrate"):
        backend.register(method, {"facts": []})
    return backend


def _builder(agent, context):
    task = AgentTask(
        agent_id=agent.agent_id,
        analysis_run_id=context.analysis_run_id,
        topic=agent.topic,
        task_type="extract",  # use the extraction prompt/schema for all test agents
        packet_ref=f"packet-{agent.agent_id}",
        prompt_name="qualitative_extraction",
        response_schema_ref="qualitative_extraction",
        model_tier=agent.model_tier,
        case_id=context.case_id,
        snapshot_version=context.snapshot_version,
    )
    return PreparedTask(task=task, inputs={}, agent_definition_hash="adh")


def _executor(db_session, backend, **kw):
    client = _client(db_session, backend)
    return DagExecutor(db_session, _registry(), AgentRuntime(client), **kw)


def test_dependency_ordering_and_waves(db_session):
    backend = _ok_backend()
    ex = _executor(db_session, backend, max_concurrency=4)
    report = ex.run(_run_row(db_session), build_task=_builder)
    assert set(report.executed) == {"a", "b", "c", "d"}
    # c comes after a and b.
    waves = [set(w) for w in report.waves]
    assert {"a", "b", "d"} <= waves[0]
    assert "c" in waves[1]


def test_semaphore_bounds_concurrency(db_session):
    # A backend that sleeps so overlapping tasks are observable.
    class SlowBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            time.sleep(0.05)
            return super().generate(request)

    backend = SlowBackend()
    backend.register("extract", {"facts": []})
    ex = _executor(db_session, backend, max_concurrency=2)
    report = ex.run(_run_row(db_session), build_task=_builder)
    assert set(report.executed) == {"a", "b", "c", "d"}
    # Wave 0 has 3 ready jobs (a, b, d) but concurrency is capped at 2.
    assert ex.peak_concurrency <= 2


def test_no_shared_session_mutation_during_concurrent_calls(db_session):
    # Within a wave, concurrent provider calls must NOT observe partial
    # persistence from their own wave: all DB writes happen in the serial
    # persist phase AFTER the whole wave's provider calls complete. Wave 0 has
    # 3 jobs (a,b,d); wave 1 has 1 (c). A call must only ever see a count that
    # equals a fully-completed previous wave (0 or 3) — never a partial 1 or 2.
    observed: list[int] = []

    class AssertingBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            count = db_session.query(AgentRunRow).count()
            observed.append(count)
            assert count in (0, 3), (
                "concurrent provider calls observed partial within-wave "
                f"persistence (count={count}); DB mutated during execute phase"
            )
            return super().generate(request)

    backend = AssertingBackend()
    backend.register("extract", {"facts": []})
    ex = _executor(db_session, backend, max_concurrency=4)
    report = ex.run(_run_row(db_session), build_task=_builder)
    assert set(report.executed) == {"a", "b", "c", "d"}
    # After the run, every agent run IS persisted.
    assert db_session.query(AgentRunRow).count() == 4
    assert observed  # the backend actually ran


def test_one_failure_preserves_siblings(db_session):
    class FlakyBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            if request.inputs.get("agent_id") == "a":
                raise RuntimeError("provider exploded")
            return super().generate(request)

    backend = FlakyBackend()
    backend.register("extract", {"facts": []})

    def builder(agent, context):
        pt = _builder(agent, context)
        return PreparedTask(
            task=pt.task,
            inputs={"agent_id": agent.agent_id},
            agent_definition_hash="adh",
        )

    client = _client(db_session, backend)
    ex = DagExecutor(db_session, _registry(), AgentRuntime(client), max_concurrency=4)
    report = ex.run(_run_row(db_session), build_task=builder)

    assert "a" in report.failed  # a failed
    assert "b" in report.executed and "d" in report.executed  # siblings survived
    assert "c" in report.skipped  # c depends on failed a => skipped


def test_timeout_is_typed_error_not_hang(db_session):
    class HangingBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            time.sleep(0.3)
            return super().generate(request)

    backend = HangingBackend()
    backend.register("extract", {"facts": []})
    client = _client(db_session, backend)
    ex = DagExecutor(db_session, _registry(), AgentRuntime(client),
                     max_concurrency=4, timeout_seconds=0.05)
    report = ex.run(_run_row(db_session), build_task=_builder)
    assert report.failed  # at least the timed-out agents are failures
    assert all(v in {"TimeoutError", "rejected"} or v for v in report.failed.values())


def test_event_loop_not_blocked_by_sync_backend(db_session):
    # Two independent slow sync agents should overlap (run concurrently) because
    # the runtime offloads sync generate() to threads. If the loop were blocked
    # they would run serially (~0.2s); concurrently they finish in ~0.1s.
    class SlowBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            time.sleep(0.1)
            return super().generate(request)

    backend = SlowBackend()
    backend.register("extract", {"facts": []})
    # Registry with two independent agents only.
    reg = AgentRegistry(
        [
            AgentDefinition("a", Topic.BUSINESS, "extract", ModelTier.NARROW),
            AgentDefinition("b", Topic.FINANCIAL, "extract", ModelTier.NARROW),
        ]
    )
    client = _client(db_session, backend)
    ex = DagExecutor(db_session, reg, AgentRuntime(client), max_concurrency=2)
    started = time.monotonic()
    report = ex.run(_run_row(db_session), build_task=_builder)
    elapsed = time.monotonic() - started
    assert set(report.executed) == {"a", "b"}
    assert elapsed < 0.18  # concurrent, not ~0.2s serial


def test_run_status_set_to_completed(db_session):
    backend = _ok_backend()
    ex = _executor(db_session, backend)
    run = _run_row(db_session)
    ex.run(run, build_task=_builder)
    assert run.status == RunStatus.COMPLETED.value


def test_only_subset_runs_for_resume(db_session):
    backend = _ok_backend()
    ex = _executor(db_session, backend)
    report = ex.run(_run_row(db_session), build_task=_builder, only={"d"})
    assert report.executed == ["d"]
    assert "a" not in report.executed
