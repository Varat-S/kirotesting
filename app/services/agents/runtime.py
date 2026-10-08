"""Agent runtime — execute a single :class:`AgentTask` (Milestone 2.2).

The runtime is the thin, DB-free unit the concurrent executor (Milestone 5)
schedules: given one ``AgentTask`` and its routed input payload, it drives the
single :class:`~app.services.llm.client.LLMClient` choke point to produce a RAW
provider result, validates the JSON against the task's response schema, and
assembles:

* an :class:`~app.schemas.agentic.AgentExecutionResult` — the typed outcome the
  executor gathers (provider failures are captured here, never raised, so a
  failing task cannot cancel its concurrent siblings, Req 30); and
* an UNPERSISTED :class:`~app.schemas.agentic.AgentRun` — the full provenance
  record the executor persists LATER, serially, off the concurrent path
  (Req 6.5 / 19.2).

No database mutation happens here. Persistence is the executor's serial phase.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.schemas.agentic import (
    AgentExecutionResult,
    AgentRun,
    AgentTask,
    ExecutionStatus,
    ValidationStatus,
)
from app.services.llm.client import GenerateOnlyResult, LLMClient


@dataclass(frozen=True)
class AgentRuntimeOutput:
    """What the runtime returns for one task: the typed result + unpersisted run.

    ``generate_result`` is retained so the executor's serial persist phase can
    log the underlying provider run via :meth:`LLMClient.log_run` and reconcile
    the resulting ``run_id`` onto the ``AgentRun`` record.
    """

    execution: AgentExecutionResult
    agent_run: AgentRun
    generate_result: GenerateOnlyResult


_OUTCOME_TO_EXECUTION = {
    "valid": ExecutionStatus.OK,
    "rejected": ExecutionStatus.REJECTED,
}
_OUTCOME_TO_VALIDATION = {
    "valid": ValidationStatus.VALID,
    "rejected": ValidationStatus.REJECTED,
}


class AgentRuntime:
    """Execute one :class:`AgentTask` through the LLMClient (no persistence)."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    # -- synchronous ----------------------------------------------------------

    def run(
        self,
        task: AgentTask,
        inputs: dict,
        *,
        agent_definition_hash: str,
        evidence_ids: list[str] | None = None,
        execution_wave: int = 0,
        parent_run_ids: list[str] | None = None,
    ) -> AgentRuntimeOutput:
        """Execute ``task`` synchronously; returns typed result + unpersisted run."""
        request = self._build_request(task, inputs, evidence_ids)
        started = time.monotonic()
        result = self._client.generate_only(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._assemble(
            task,
            result,
            agent_definition_hash=agent_definition_hash,
            evidence_ids=evidence_ids,
            execution_wave=execution_wave,
            parent_run_ids=parent_run_ids,
            latency_ms=latency_ms,
        )

    # -- asynchronous (used by the concurrent executor, Req 30) ---------------

    async def run_async(
        self,
        task: AgentTask,
        inputs: dict,
        *,
        agent_definition_hash: str,
        evidence_ids: list[str] | None = None,
        execution_wave: int = 0,
        parent_run_ids: list[str] | None = None,
    ) -> AgentRuntimeOutput:
        """Execute ``task`` off the event loop; captures provider failures.

        Provider exceptions are turned into a rejected result inside the
        LLMClient's DB-free path, so this coroutine never raises a provider
        error into the surrounding ``TaskGroup`` (Req 30).
        """
        request = self._build_request(task, inputs, evidence_ids)
        started = time.monotonic()
        result = await self._client.generate_only_async(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        return self._assemble(
            task,
            result,
            agent_definition_hash=agent_definition_hash,
            evidence_ids=evidence_ids,
            execution_wave=execution_wave,
            parent_run_ids=parent_run_ids,
            latency_ms=latency_ms,
        )

    # -- internals ------------------------------------------------------------

    def _build_request(
        self, task: AgentTask, inputs: dict, evidence_ids: list[str] | None
    ):
        return self._client.build_request(
            task.task_type,
            task.prompt_name,
            inputs,
            evidence_ids=evidence_ids,
            case_id=task.case_id,
            case_version=task.snapshot_version,
        )

    def _assemble(
        self,
        task: AgentTask,
        result: GenerateOnlyResult,
        *,
        agent_definition_hash: str,
        evidence_ids: list[str] | None,
        execution_wave: int,
        parent_run_ids: list[str] | None,
        latency_ms: int,
    ) -> AgentRuntimeOutput:
        raw = result.raw
        usage = _extract_usage(raw.model_config)
        outcome = result.validation_outcome
        error_type = raw.model_config.get("error_type")
        if error_type:
            exec_status = (
                ExecutionStatus.TIMEOUT
                if error_type.lower().endswith("timeout")
                else ExecutionStatus.ERROR
            )
        else:
            exec_status = _OUTCOME_TO_EXECUTION.get(outcome, ExecutionStatus.ERROR)

        execution = AgentExecutionResult(
            agent_id=task.agent_id,
            analysis_run_id=task.analysis_run_id,
            status=exec_status,
            parsed=result.parsed,
            raw_response=raw.raw_response or None,
            error_type=error_type,
            usage=usage,
            latency_ms=latency_ms,
        )

        agent_run = AgentRun(
            run_id=str(uuid.uuid4()),
            analysis_run_id=task.analysis_run_id,
            agent_id=task.agent_id,
            agent_definition_hash=agent_definition_hash,
            topic=task.topic,
            case_id=task.case_id,
            snapshot_version=task.snapshot_version,
            prompt_id=result.request.prompt.prompt_id,
            prompt_version=result.request.prompt.version,
            prompt_hash=result.request.prompt.content_hash,
            response_schema_hash=None,
            model_id=raw.model_id,
            model_config_payload=dict(raw.model_config),
            input_hash=task.packet_ref,
            input_evidence_ids=list(evidence_ids or []),
            raw_response=raw.raw_response or None,
            parsed_response=result.parsed,
            validation_status=(
                ValidationStatus.ERROR
                if error_type
                else _OUTCOME_TO_VALIDATION.get(outcome, ValidationStatus.ERROR)
            ),
            validation_detail=result.validation_detail,
            parent_run_ids=list(parent_run_ids or []),
            execution_wave=execution_wave,
            latency_ms=latency_ms,
            usage=usage,
            error_state=error_type,
            rerun_reason=task.rerun_reason,
            reused_from_cache=False,
        )
        return AgentRuntimeOutput(execution, agent_run, result)


def _extract_usage(model_config: dict) -> dict | None:
    """Pull token-usage fields out of a backend's ``model_config`` if present."""
    keys = ("input_tokens", "output_tokens", "total_tokens")
    usage = {k: model_config[k] for k in keys if k in model_config}
    return usage or None
