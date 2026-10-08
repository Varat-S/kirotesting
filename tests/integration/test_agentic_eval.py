"""Milestone 20 — agentic evaluation / regression suite (Req 22).

Consolidates the remaining Req 22.2 matrix items not covered elsewhere:
two analysis runs over the same evidence snapshot stay isolated; deterministic
replay is reproducible; a provider refusal/error surfaces as a typed failure
(sibling-isolated); a partial-failure run is resumable; and NO live provider
calls occur (the fake backend drives everything).
"""

from __future__ import annotations

from datetime import datetime, timezone


from app.models.orm import AgenticAnalysisRun as RunRow
from app.models.orm import ParameterResultRow
from app.prompts.registry import PromptRegistry
from app.schemas.agentic import (
    AgentTask,
    AgenticAnalysisRun,
    ModelTier,
    Topic,
)
from app.services.agents.executor import DagExecutor, PreparedTask
from app.services.agents.registry import AgentDefinition, AgentRegistry
from app.services.agents.runtime import AgentRuntime
from app.services.llm.client import FakeLLMBackend, LLMClient, LLMRawResult, LLMRequest
from app.services.orchestration.promotion import (
    ParameterPromoter,
    ValidatedAgentOutput,
    promote_narrow_output,
)


def _registry() -> AgentRegistry:
    return AgentRegistry([
        AgentDefinition("a", Topic.BUSINESS, "extract", ModelTier.NARROW,
                        owned_parameters=("a_assessment",)),
        AgentDefinition("b", Topic.FINANCIAL, "extract", ModelTier.NARROW,
                        owned_parameters=("b_assessment",)),
    ])


def _client(db_session, backend):
    reg = PromptRegistry(db_session)
    reg.register_catalogue()
    return LLMClient(backend, prompts=reg, session=db_session)


def _seed_run(db_session, run_id):
    db_session.add(RunRow(
        analysis_run_id=run_id, case_id="C1", evidence_snapshot_version=1,
        status="created", router_version="router-1", agent_registry_version="reg-1",
        agent_registry_hash="rh", scoring_config_version=1, scoring_config_hash="sh"))
    db_session.flush()
    return AgenticAnalysisRun(
        analysis_run_id=run_id, case_id="C1", evidence_snapshot_version=1,
        started_at=datetime.now(timezone.utc), router_version="router-1",
        agent_registry_version="reg-1", agent_registry_hash="rh",
        scoring_config_version=1, scoring_config_hash="sh")


def _build(agent, context):
    return PreparedTask(
        task=AgentTask(
            agent_id=agent.agent_id, analysis_run_id=context.analysis_run_id,
            topic=agent.topic, task_type="extract",
            prompt_name="qualitative_extraction",
            response_schema_ref="qualitative_extraction",
            model_tier=agent.model_tier, case_id=context.case_id,
            snapshot_version=context.snapshot_version, packet_ref="pkt"),
        inputs={}, agent_definition_hash=agent.definition_hash(), evidence_ids=["E1"])


def test_two_runs_same_snapshot_are_isolated(db_session):
    reg = _registry()
    backend = FakeLLMBackend()
    backend.register("extract", {"facts": []})
    ex = DagExecutor(db_session, reg, AgentRuntime(_client(db_session, backend)))

    # Promote one parameter per run so each run owns its own artifacts.
    def on_result(agent, parsed, context):
        promoter = ParameterPromoter(db_session)
        pr = promote_narrow_output(ValidatedAgentOutput(
            agent=agent, parsed={"parameters": [
                {"parameter_id": f"{agent.agent_id}_assessment", "value": "x",
                 "value_type": "category", "method": "llm", "status": "ok",
                 "evidence_ids": ["E1"]}]},
            analysis_run_id=context.analysis_run_id,
            agent_run_id=context.run_ids[agent.agent_id]))
        for r in pr:
            promoter.persist(r, case_id="C1", snapshot_version=1)

    ex.run(_seed_run(db_session, "AR1"), build_task=_build, on_result=on_result)
    ex.run(_seed_run(db_session, "AR2"), build_task=_build, on_result=on_result)

    ar1 = db_session.query(ParameterResultRow).filter_by(analysis_run_id="AR1").all()
    ar2 = db_session.query(ParameterResultRow).filter_by(analysis_run_id="AR2").all()
    assert ar1 and ar2
    # No artifact leaks between runs.
    assert all(r.analysis_run_id == "AR1" for r in ar1)
    assert all(r.analysis_run_id == "AR2" for r in ar2)
    assert {r.parameter_result_id for r in ar1}.isdisjoint(
        {r.parameter_result_id for r in ar2})


def test_deterministic_replay_is_reproducible(db_session):
    # Same inputs + versions reproduce identical deterministic parameter output.
    from app.services.parameters import ParameterEngine

    e = ParameterEngine()
    a = e.compute("segment_hhi", {"shares": [40, 30, 30]}, analysis_run_id="AR1")
    b = e.compute("segment_hhi", {"shares": [40, 30, 30]}, analysis_run_id="AR2")
    assert a.result.value == b.result.value
    assert a.result.formula_version == b.result.formula_version


def test_provider_refusal_is_typed_failure(db_session):
    class RefusingBackend(FakeLLMBackend):
        def generate(self, request: LLMRequest) -> LLMRawResult:
            raise RuntimeError("provider refused")

    reg = _registry()
    ex = DagExecutor(db_session, reg,
                     AgentRuntime(_client(db_session, RefusingBackend())))
    report = ex.run(_seed_run(db_session, "AR1"), build_task=_build)
    assert set(report.failed) == {"a", "b"}  # both typed errors, neither raised


def test_resume_after_partial_failure(db_session):
    # First run: agent 'b' fails. Resume runs only the previously-failed subset.
    class FlakyBackend(FakeLLMBackend):
        fail_b = True

        def generate(self, request: LLMRequest) -> LLMRawResult:
            if request.inputs.get("who") == "b" and FlakyBackend.fail_b:
                raise RuntimeError("transient")
            return super().generate(request)

    backend = FlakyBackend()
    backend.register("extract", {"facts": []})
    client = _client(db_session, backend)
    reg = _registry()

    def build(agent, context):
        pt = _build(agent, context)
        pt.inputs = {"who": agent.agent_id}
        return pt

    ex = DagExecutor(db_session, reg, AgentRuntime(client))
    report1 = ex.run(_seed_run(db_session, "AR1"), build_task=build)
    assert "b" in report1.failed and "a" in report1.executed

    # Resume: fix the transient fault and re-run ONLY the failed node.
    FlakyBackend.fail_b = False
    report2 = ex.run(_seed_run(db_session, "AR2"), build_task=build, only={"b"})
    assert report2.executed == ["b"]


def test_no_live_calls_fake_backend_only(db_session):
    # The fake backend is deterministic + offline; a sentinel proves the model
    # id never looks like a real provider.
    reg = _registry()
    backend = FakeLLMBackend()
    backend.register("extract", {"facts": []})
    ex = DagExecutor(db_session, reg, AgentRuntime(_client(db_session, backend)))
    report = ex.run(_seed_run(db_session, "AR1"), build_task=_build)
    assert set(report.executed) == {"a", "b"}
    assert backend.MODEL_ID == "fake-deterministic-v1"
