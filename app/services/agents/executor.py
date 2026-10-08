"""Async DAG executor + concurrency safety (Milestone 5).

Runs the agent registry's dependency DAG with BOUNDED concurrency and SAFE
persistence. The executor is the only place provider calls run concurrently, and
it enforces the architecture's hard concurrency rules (Req 6, 30):

Per wave, the lifecycle is strictly phased:

1. BUILD (sync, single session): construct immutable ``AgentTask``s + routed
   input payloads for every ready agent. Plain data — no ORM-attached objects.
2. EXECUTE (async, NO DB): await each task via the runtime, which calls the
   provider off the event loop (``generate_async``/``to_thread``). Each child
   task catches its own provider exceptions and returns a typed
   ``AgentExecutionResult`` — a failure NEVER escapes to cancel siblings. A
   shared ``asyncio.Semaphore`` bounds concurrency to ``AGENT_MAX_CONCURRENCY``;
   each call is wrapped in ``asyncio.wait_for(AGENT_TIMEOUT_SECONDS)``.
3. GATHER: collect the typed results (failures are values, so siblings survive).
4. PERSIST (serial, single session): write ``AgentRun`` rows + audit events and
   hand parsed output to the result handler. All DB mutation is serialized here;
   NO session is ever touched during phase 2.

Resume/idempotency and descendant invalidation are supported via the registry's
dependency traversal and the cache (Milestone 6); this module provides the
concurrency-safe skeleton those build on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from sqlalchemy.orm import Session

from app.models.orm import AgentRunRow
from app.models.orm import AgenticAnalysisRun as AgenticAnalysisRunRow
from app.schemas.agentic import (
    AgentExecutionResult,
    AgentRun,
    AgentTask,
    AgenticAnalysisRun,
    ExecutionStatus,
    RunStatus,
)
from app.services.agents.registry import AgentDefinition, AgentRegistry
from app.services.agents.runtime import AgentRuntime, AgentRuntimeOutput


class TaskBuilder(Protocol):
    """Builds the immutable task + routed inputs for one agent (phase 1).

    Returns ``None`` to SKIP the agent (e.g. required evidence absent); the skip
    is recorded and the agent's descendants are handled by the caller.
    """

    def __call__(
        self, agent: AgentDefinition, context: "ExecutionContext"
    ) -> "PreparedTask | None": ...


@dataclass
class PreparedTask:
    """A built task plus everything the runtime needs — all immutable data."""

    task: AgentTask
    inputs: dict
    agent_definition_hash: str
    evidence_ids: list[str] = field(default_factory=list)
    parent_run_ids: list[str] = field(default_factory=list)


@dataclass
class ExecutionContext:
    """Shared read-only context handed to the task builder / result handler."""

    analysis_run_id: str
    case_id: str
    snapshot_version: int
    # Parsed, VALID outputs of already-completed agents, keyed by agent_id.
    completed: dict[str, dict] = field(default_factory=dict)
    # The AgentRun record id for each completed agent (for parent linkage).
    run_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionReport:
    """Summary of a DAG run."""

    analysis_run_id: str
    executed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # agent_id -> reason
    failed: dict[str, str] = field(default_factory=dict)  # agent_id -> error_type
    waves: list[list[str]] = field(default_factory=list)
    results: dict[str, AgentExecutionResult] = field(default_factory=dict)


class DagExecutor:
    """Execute the agent DAG with bounded concurrency and safe persistence."""

    def __init__(
        self,
        session: Session,
        registry: AgentRegistry,
        runtime: AgentRuntime,
        *,
        max_concurrency: int = 6,
        timeout_seconds: float = 120,
        audit=None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._runtime = runtime
        self._max_concurrency = max(1, int(max_concurrency))
        self._timeout = float(timeout_seconds)
        self._audit = audit
        # Observability hook: records the max observed concurrency (for tests).
        self.peak_concurrency = 0

    def run(
        self,
        analysis_run: AgenticAnalysisRun,
        *,
        build_task: TaskBuilder,
        on_result: Callable[[AgentDefinition, dict, ExecutionContext], None] | None = None,
        only: set[str] | None = None,
    ) -> ExecutionReport:
        """Synchronous entry point (drives the async core via ``asyncio.run``)."""
        return asyncio.run(
            self.run_async(
                analysis_run, build_task=build_task, on_result=on_result, only=only
            )
        )

    async def run_async(
        self,
        analysis_run: AgenticAnalysisRun,
        *,
        build_task: TaskBuilder,
        on_result: Callable[[AgentDefinition, dict, ExecutionContext], None] | None = None,
        only: set[str] | None = None,
    ) -> ExecutionReport:
        context = ExecutionContext(
            analysis_run_id=analysis_run.analysis_run_id,
            case_id=analysis_run.case_id,
            snapshot_version=analysis_run.evidence_snapshot_version,
        )
        report = ExecutionReport(analysis_run_id=analysis_run.analysis_run_id)
        self._set_status(analysis_run, RunStatus.RUNNING)

        semaphore = asyncio.Semaphore(self._max_concurrency)
        active = 0
        active_lock = asyncio.Lock()

        for wave_index, wave in enumerate(self._registry.topological_waves()):
            ready = [a for a in wave if only is None or a in only]
            report.waves.append(ready)
            if not ready:
                continue

            # ---- phase 1: BUILD (sync, no provider I/O) ----
            prepared: dict[str, PreparedTask] = {}
            for agent_id in ready:
                agent = self._registry.get(agent_id)
                if self._blocked_by_failure(agent, report):
                    report.skipped[agent_id] = "upstream_failed_or_skipped"
                    continue
                pt = build_task(agent, context)
                if pt is None:
                    report.skipped[agent_id] = "no_task"
                    continue
                prepared[agent_id] = pt

            if not prepared:
                continue

            # ---- phase 2: EXECUTE (async, NO DB mutation) ----
            async def _execute(agent_id: str, pt: PreparedTask) -> AgentRuntimeOutput:
                nonlocal active
                async with semaphore:
                    async with active_lock:
                        active += 1
                        self.peak_concurrency = max(self.peak_concurrency, active)
                    try:
                        return await asyncio.wait_for(
                            self._runtime.run_async(
                                pt.task,
                                pt.inputs,
                                agent_definition_hash=pt.agent_definition_hash,
                                evidence_ids=pt.evidence_ids,
                                execution_wave=wave_index,
                                parent_run_ids=pt.parent_run_ids,
                            ),
                            timeout=self._timeout,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        return self._timeout_output(agent_id, pt, wave_index)
                    finally:
                        async with active_lock:
                            active -= 1

            outputs: dict[str, AgentRuntimeOutput] = {}
            async with asyncio.TaskGroup() as tg:
                tasks = {
                    agent_id: tg.create_task(_execute(agent_id, pt))
                    for agent_id, pt in prepared.items()
                }
            for agent_id, task in tasks.items():
                outputs[agent_id] = task.result()

            # ---- phase 3+4: GATHER + PERSIST (serial) ----
            for agent_id in sorted(outputs):
                self._persist_and_record(
                    agent_id, outputs[agent_id], context, report, on_result
                )

        self._set_status(
            analysis_run,
            RunStatus.PARTIALLY_FAILED if report.failed else RunStatus.COMPLETED,
        )
        return report

    # -- helpers --------------------------------------------------------------

    def _blocked_by_failure(self, agent: AgentDefinition, report: ExecutionReport) -> bool:
        bad = set(report.failed) | set(report.skipped)
        return any(dep in bad for dep in agent.dependencies)

    def _persist_and_record(
        self,
        agent_id: str,
        output: AgentRuntimeOutput,
        context: ExecutionContext,
        report: ExecutionReport,
        on_result: Callable[[AgentDefinition, dict, ExecutionContext], None] | None,
    ) -> None:
        execution = output.execution
        report.results[agent_id] = execution

        # Log the underlying provider run serially (DB mutation happens here only).
        logged = self._session is not None
        if logged and output.generate_result is not None:
            try:
                self._runtime._client.log_run(output.generate_result)  # noqa: SLF001
            except Exception:  # pragma: no cover - logging must not crash the run
                logged = False

        self._persist_agent_run(output.agent_run)

        if execution.status is ExecutionStatus.OK and execution.parsed is not None:
            report.executed.append(agent_id)
            context.completed[agent_id] = execution.parsed
            context.run_ids[agent_id] = output.agent_run.run_id
            if on_result is not None:
                on_result(self._registry.get(agent_id), execution.parsed, context)
        elif execution.status in (ExecutionStatus.ERROR, ExecutionStatus.TIMEOUT):
            report.failed[agent_id] = execution.error_type or execution.status.value
        else:  # rejected
            report.failed[agent_id] = "rejected"

    def _persist_agent_run(self, run: AgentRun) -> None:
        if self._session is None:
            return
        self._session.add(
            AgentRunRow(
                run_id=run.run_id,
                analysis_run_id=run.analysis_run_id,
                agent_id=run.agent_id,
                agent_definition_hash=run.agent_definition_hash,
                topic=run.topic.value,
                case_id=run.case_id,
                snapshot_version=run.snapshot_version,
                prompt_id=run.prompt_id,
                prompt_version=run.prompt_version,
                prompt_hash=run.prompt_hash,
                response_schema_hash=run.response_schema_hash,
                model_id=run.model_id,
                model_config_json=dict(run.model_config_payload),
                input_hash=run.input_hash,
                cache_key=None,
                input_evidence_ids=list(run.input_evidence_ids),
                raw_response=run.raw_response,
                parsed_response=run.parsed_response,
                validation_status=run.validation_status.value,
                validation_detail=run.validation_detail,
                parent_run_ids=list(run.parent_run_ids),
                execution_wave=run.execution_wave,
                latency_ms=run.latency_ms,
                usage=run.usage,
                error_state=run.error_state,
                rerun_reason=run.rerun_reason,
                reused_from_cache=run.reused_from_cache,
            )
        )
        self._session.flush()

    def _timeout_output(
        self, agent_id: str, pt: PreparedTask, wave_index: int
    ) -> AgentRuntimeOutput:
        import uuid

        execution = AgentExecutionResult(
            agent_id=agent_id,
            analysis_run_id=pt.task.analysis_run_id,
            status=ExecutionStatus.TIMEOUT,
            error_type="TimeoutError",
        )
        from app.schemas.agentic import ValidationStatus

        agent_run = AgentRun(
            run_id=str(uuid.uuid4()),
            analysis_run_id=pt.task.analysis_run_id,
            agent_id=agent_id,
            agent_definition_hash=pt.agent_definition_hash,
            topic=pt.task.topic,
            case_id=pt.task.case_id,
            snapshot_version=pt.task.snapshot_version,
            prompt_id=pt.task.prompt_name,
            prompt_version=None,
            prompt_hash="",
            model_id="unknown",
            input_hash=pt.task.packet_ref,
            input_evidence_ids=list(pt.evidence_ids),
            validation_status=ValidationStatus.ERROR,
            validation_detail="Provider call timed out.",
            execution_wave=wave_index,
            error_state="TimeoutError",
        )
        return AgentRuntimeOutput(execution, agent_run, None)  # type: ignore[arg-type]

    def _set_status(self, run: AgenticAnalysisRun, status: RunStatus) -> None:
        run.status = status.value
        if self._session is not None:
            row = self._session.get(
                AgenticAnalysisRunRow, run.analysis_run_id
            )
            if row is not None:
                row.status = status.value
                self._session.flush()
