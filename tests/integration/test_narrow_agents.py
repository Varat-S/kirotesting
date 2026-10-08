"""Milestones 10-11 — Business + Financial narrow agents (Req 11.1, 12.1-12.2).

Each of the twelve narrow agents is driven offline by the FakeLLMBackend through
the DagExecutor; outputs pass deterministic validation and are promoted to typed
ParameterResults that carry only owned parameters, cite evidence, and (for
covenant extraction) route numeric terms as hybrid for later validation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.orm import AgenticAnalysisRun as RunRow
from app.models.orm import ParameterResultRow
from app.prompts.registry import PromptRegistry
from app.schemas.agentic import (
    AgentTask,
    AgenticAnalysisRun,
    Method,
    ParameterStatus,
)
from app.services.agents.executor import DagExecutor, PreparedTask
from app.services.agents.registry import default_registry
from app.services.agents.runtime import AgentRuntime
from app.services.agents.validation import DeterministicValidator
from app.services.llm.client import FakeLLMBackend, LLMClient
from app.services.orchestration.promotion import (
    ParameterPromoter,
    PromotionRejected,
    ValidatedAgentOutput,
    promote_narrow_output,
)


def _seed_run(db_session, run_id="AR1"):
    db_session.add(
        RunRow(
            analysis_run_id=run_id, case_id="C1", evidence_snapshot_version=1,
            status="created", router_version="router-1",
            agent_registry_version="reg-1", agent_registry_hash="rh",
            scoring_config_version=1, scoring_config_hash="sh",
        )
    )
    db_session.flush()
    return AgenticAnalysisRun(
        analysis_run_id=run_id, case_id="C1", evidence_snapshot_version=1,
        started_at=datetime.now(timezone.utc), router_version="router-1",
        agent_registry_version="reg-1", agent_registry_hash="rh",
        scoring_config_version=1, scoring_config_hash="sh",
    )


def _client(db_session, backend):
    reg = PromptRegistry(db_session)
    reg.register_catalogue()
    return LLMClient(backend, prompts=reg, session=db_session)


def _validate(agent, parsed, evidence_ids=("E1",)):
    from app.schemas.agentic import EvidencePacket

    packet = EvidencePacket(
        case_id="C1", snapshot_version=1, router_version="r",
        analysis_run_id="AR1", agent_id=agent.agent_id,
        evidence_ids=list(evidence_ids), packet_hash="pkt",
    )
    return DeterministicValidator.for_agent(packet, agent).validate(parsed)


def test_valid_owned_output_promotes_through_the_gate(db_session):
    reg = default_registry()
    agent = reg.get("business_model")
    backend = FakeLLMBackend()
    backend.register(
        "business_model",
        {
            "parameters": [
                {"parameter_id": "business_model_classification",
                 "value": "network-carrier", "value_type": "category",
                 "method": "llm", "status": "ok", "evidence_ids": ["E1"]},
            ]
        },
    )
    client = _client(db_session, backend)
    runtime = AgentRuntime(client)
    task = AgentTask(
        agent_id="business_model", analysis_run_id="AR1", topic=agent.topic,
        task_type="business_model", packet_ref="pkt",
        prompt_name="business_model", response_schema_ref="business_model",
        model_tier=agent.model_tier, case_id="C1", snapshot_version=1,
    )
    out = runtime.run(task, {}, agent_definition_hash=agent.definition_hash(),
                      evidence_ids=["E1"])
    outcome = _validate(agent, out.execution.parsed)
    assert outcome.valid

    validated = ValidatedAgentOutput.gate(
        agent, out.execution.parsed, outcome, analysis_run_id="AR1",
        agent_run_id=out.agent_run.run_id,
    )
    results = promote_narrow_output(validated)
    pids = {r.parameter_id for r in results}
    assert "business_model_classification" in pids
    assert all(r.method is Method.LLM for r in results)


def test_llm_emitting_deterministic_param_fails_validation_and_cannot_promote(db_session):
    reg = default_registry()
    agent = reg.get("business_model")
    # The LLM tries to emit a deterministic id + a non-owned id => validation
    # fails, and the promotion GATE refuses to construct a ValidatedAgentOutput.
    parsed = {
        "parameters": [
            {"parameter_id": "business_model_classification", "value": "x",
             "value_type": "category", "method": "llm", "status": "ok",
             "evidence_ids": ["E1"]},
            {"parameter_id": "segment_hhi", "value": 0.3, "value_type": "index",
             "method": "llm", "status": "ok", "evidence_ids": ["E1"]},
        ]
    }
    outcome = _validate(agent, parsed)
    assert not outcome.valid  # deterministic id rejected
    with pytest.raises(PromotionRejected):
        ValidatedAgentOutput.gate(agent, parsed, outcome, analysis_run_id="AR1",
                                  agent_run_id="run-1")


def test_covenant_extraction_emits_hybrid_numeric_term(db_session):
    reg = default_registry()
    agent = reg.get("covenant_extraction")
    backend = FakeLLMBackend()
    backend.register(
        "covenant_extraction",
        {
            "parameters": [
                {
                    "parameter_id": "max_net_leverage_covenant_threshold",
                    "value_type": "number", "method": "hybrid", "status": "ok",
                    "evidence_ids": ["E_cov"],
                    "extracted_term": {"name": "max_net_leverage",
                                       "numeric_value": 3.5, "unit": "x"},
                }
            ]
        },
    )
    client = _client(db_session, backend)
    runtime = AgentRuntime(client)
    task = AgentTask(
        agent_id="covenant_extraction", analysis_run_id="AR1", topic=agent.topic,
        task_type="covenant_extraction", packet_ref="pkt",
        prompt_name="covenant_extraction",
        response_schema_ref="covenant_extraction",
        model_tier=agent.model_tier, case_id="C1", snapshot_version=1,
    )
    out = runtime.run(task, {}, agent_definition_hash=agent.definition_hash(),
                      evidence_ids=["E_cov"])
    outcome = _validate(agent, out.execution.parsed, evidence_ids=["E_cov"])
    assert outcome.valid  # the hybrid threshold IS allowed for this agent
    validated = ValidatedAgentOutput.gate(
        agent, out.execution.parsed, outcome, analysis_run_id="AR1",
        agent_run_id=out.agent_run.run_id,
    )
    results = promote_narrow_output(validated)
    term = next(r for r in results
                if r.parameter_id == "max_net_leverage_covenant_threshold")
    assert term.method is Method.HYBRID  # extracted numeric term => hybrid
    assert term.value == 3.5


def test_narrow_agents_run_concurrently_in_wave0(db_session):
    # All 15 narrow / early-extraction agents are ready in Wave 0 and promote
    # validated parameters through the gate. Each emits only its owned/hybrid
    # params, with status + evidence, so validation passes.
    reg = default_registry()
    narrow = [a for a in reg.all()
              if a.task_type not in {"orchestrate", "challenge", "mitigant"}]
    backend = FakeLLMBackend()
    for agent in narrow:
        params = []
        for p in agent.owned_parameters:
            params.append({"parameter_id": p, "value": "x", "value_type": "category",
                           "method": "llm", "status": "ok", "evidence_ids": ["E1"]})
        for p in agent.hybrid_parameters:
            params.append({"parameter_id": p, "value_type": "number",
                           "method": "hybrid", "status": "ok", "evidence_ids": ["E1"],
                           "extracted_term": {"name": p, "numeric_value": 3.5,
                                              "unit": "x"}})
        backend.register(agent.task_type, {"parameters": params})
    client = _client(db_session, backend)
    promoter = ParameterPromoter(db_session)

    def build(agent, context):
        if agent not in narrow:
            return None  # only run narrow/extraction agents in this test
        return PreparedTask(
            task=AgentTask(
                agent_id=agent.agent_id, analysis_run_id=context.analysis_run_id,
                topic=agent.topic, task_type=agent.task_type, packet_ref="pkt",
                prompt_name=agent.agent_id, response_schema_ref=agent.agent_id,
                model_tier=agent.model_tier, case_id=context.case_id,
                snapshot_version=context.snapshot_version,
            ),
            inputs={}, agent_definition_hash=agent.definition_hash(),
            evidence_ids=["E1"],
        )

    def on_result(agent, parsed, context):
        outcome = _validate(agent, parsed)
        validated = ValidatedAgentOutput.gate(
            agent, parsed, outcome, analysis_run_id=context.analysis_run_id,
            agent_run_id=context.run_ids[agent.agent_id],
        )
        for r in promote_narrow_output(validated):
            promoter.persist(r, case_id="C1", snapshot_version=1)

    ex = DagExecutor(db_session, reg, AgentRuntime(client), max_concurrency=6)
    report = ex.run(_seed_run(db_session), build_task=build, on_result=on_result)

    assert len(report.executed) == 15  # the 15 narrow/early-extraction agents
    rows = db_session.query(ParameterResultRow).all()
    assert len(rows) > 0
    assert all(r.acceptance_state == "accepted" for r in rows)
    assert "business_orchestrator" not in report.executed
