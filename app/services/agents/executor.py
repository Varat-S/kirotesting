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
    ValidationStatus,
)
from app.services.agents.cache import AgentResultCache, CacheIdentity
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
    # Cache-identity inputs (Milestone 6 wiring). ``packet_hash`` defaults to the
    # task's ``packet_ref``; router_version/response_schema_hash are optional and
    # fall back to safe defaults when the builder does not supply them.
    router_version: str = "router-unknown"
    response_schema_hash: str | None = None
    packet_hash: str | None = None

    def resolved_packet_hash(self) -> str:
        return self.packet_hash or self.task.packet_ref


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
        self._cache = AgentResultCache(session) if session is not None else None
        # Observability hooks (for tests): max observed concurrency + cache stats.
        self.peak_concurrency = 0
        self.cache_hits = 0

    def _cache_identity(self, pt: PreparedTask) -> CacheIdentity | None:
        """Compose the cache identity for a prepared task (Milestone 6 / Req 18)."""
        try:
            prompt = self._runtime._client.resolve_prompt(pt.task.prompt_name)  # noqa: SLF001
        except Exception:
            return None
        backend = getattr(self._runtime._client, "_backend", None)  # noqa: SLF001
        model_id = getattr(backend, "model_id", getattr(backend, "MODEL_ID", "unknown"))
        return CacheIdentity(
            case_id=pt.task.case_id,
            evidence_snapshot_version=pt.task.snapshot_version,
            agent_id=pt.task.agent_id,
            agent_definition_hash=pt.agent_definition_hash,
            prompt_hash=prompt.content_hash,
            response_schema_hash=pt.response_schema_hash,
            model_id=model_id,
            model_config={"temperature": 0.0},
            router_version=pt.router_version,
            packet_hash=pt.resolved_packet_hash(),
        )

    def run(
        self,
        analysis_run: AgenticAnalysisRun,
        *,
        build_task: TaskBuilder,
        on_result: Callable[[AgentDefinition, dict, ExecutionContext], None] | None = None,
        only: set[str] | None = None,
        force_regenerate: bool = False,
    ) -> ExecutionReport:
        """Synchronous entry point (drives the async core via ``asyncio.run``)."""
        return asyncio.run(
            self.run_async(
                analysis_run, build_task=build_task, on_result=on_result,
                only=only, force_regenerate=force_regenerate,
            )
        )

    async def run_async(
        self,
        analysis_run: AgenticAnalysisRun,
        *,
        build_task: TaskBuilder,
        on_result: Callable[[AgentDefinition, dict, ExecutionContext], None] | None = None,
        only: set[str] | None = None,
        force_regenerate: bool = False,
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

            # ---- phase 1: BUILD + CACHE LOOKUP (sync, no provider I/O) ----
            prepared: dict[str, PreparedTask] = {}
            cache_keys: dict[str, str | None] = {}
            for agent_id in ready:
                agent = self._registry.get(agent_id)
                if self._blocked_by_failure(agent, report):
                    report.skipped[agent_id] = "upstream_failed_or_skipped"
                    continue
                pt = build_task(agent, context)
                if pt is None:
                    report.skipped[agent_id] = "no_task"
                    continue

                # Idempotent reuse: a valid prior run with the same identity is a
                # hit (Milestone 6 / Req 18). force_regenerate bypasses.
                identity = None if self._cache is None else self._cache_identity(pt)
                cache_keys[agent_id] = identity.key() if identity is not None else None
                if identity is not None:
                    hit = self._cache.lookup(identity, force_regenerate=force_regenerate)
                    if hit is not None:
                        self._record_cache_hit(
                            agent, pt, hit, identity.key(), wave_index, context,
                            report, on_result,
                        )
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
                    agent_id, outputs[agent_id], context, report, on_result,
                    cache_key=cache_keys.get(agent_id),
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
        *,
        cache_key: str | None = None,
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

        self._persist_agent_run(output.agent_run, cache_key=cache_key)

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

    def _record_cache_hit(
        self,
        agent: AgentDefinition,
        pt: PreparedTask,
        hit,
        cache_key: str,
        wave_index: int,
        context: ExecutionContext,
        report: ExecutionReport,
        on_result: Callable[[AgentDefinition, dict, ExecutionContext], None] | None,
    ) -> None:
        """Reuse a valid prior run WITHOUT calling the provider (Req 18.4)."""
        import uuid

        self.cache_hits += 1
        run = AgentRun(
            run_id=str(uuid.uuid4()),
            analysis_run_id=pt.task.analysis_run_id,
            agent_id=agent.agent_id,
            agent_definition_hash=pt.agent_definition_hash,
            topic=agent.topic,
            case_id=pt.task.case_id,
            snapshot_version=pt.task.snapshot_version,
            prompt_id=pt.task.prompt_name,
            prompt_hash="",
            model_id="cache",
            input_hash=pt.resolved_packet_hash(),
            input_evidence_ids=list(pt.evidence_ids),
            parsed_response=hit.parsed,
            validation_status=ValidationStatus.VALID,
            validation_detail=f"Reused from cache (run {hit.run_id}).",
            execution_wave=wave_index,
            reused_from_cache=True,
        )
        self._persist_agent_run(run, cache_key=cache_key)
        report.results[agent.agent_id] = AgentExecutionResult(
            agent_id=agent.agent_id,
            analysis_run_id=pt.task.analysis_run_id,
            status=ExecutionStatus.OK,
            parsed=hit.parsed,
        )
        report.executed.append(agent.agent_id)
        if hit.parsed is not None:
            context.completed[agent.agent_id] = hit.parsed
        context.run_ids[agent.agent_id] = run.run_id
        if on_result is not None and hit.parsed is not None:
            on_result(agent, hit.parsed, context)

    def _persist_agent_run(self, run: AgentRun, *, cache_key: str | None = None) -> None:
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
                cache_key=cache_key,
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
