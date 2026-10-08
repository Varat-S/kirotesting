"""Milestone 12 — Business/Financial orchestrators (Req 11.2-11.3, 12.3-12.4, 14, 34, 35).

Orchestrators synthesize validated inputs into claim-level TopicConclusions and
compute nothing: they may quote exact validated numbers (checked against the
known ParameterResults) but an invented/mismatched number is rejected, and
contradictions are surfaced, not resolved.
"""

from __future__ import annotations

import pytest

from app.prompts.registry import PromptRegistry
from app.schemas.agentic import AgentTask, Topic
from app.services.agents.registry import default_registry
from app.services.agents.runtime import AgentRuntime
from app.services.agents.validation import DeterministicValidator, ValidationCheck
from app.services.llm.client import FakeLLMBackend, LLMClient
from app.services.orchestration.conclusions import (
    ConclusionStore,
    build_topic_conclusion,
)


def _client(db_session, backend):
    reg = PromptRegistry(db_session)
    reg.register_catalogue()
    return LLMClient(backend, prompts=reg, session=db_session)


def _run_orchestrator(db_session, agent_id, response):
    reg = default_registry()
    agent = reg.get(agent_id)
    backend = FakeLLMBackend()
    backend.register("orchestrate", response)
    client = _client(db_session, backend)
    runtime = AgentRuntime(client)
    task = AgentTask(
        agent_id=agent_id, analysis_run_id="AR1", topic=agent.topic,
        task_type="orchestrate", packet_ref="pkt", prompt_name=agent_id,
        response_schema_ref=agent_id, model_tier=agent.model_tier,
        case_id="C1", snapshot_version=1,
    )
    return agent, runtime.run(task, {}, agent_definition_hash=agent.definition_hash(),
                              evidence_ids=["E1"])


_GOOD_RESPONSE = {
    "overall_assessment": {
        "claim_id": "a1", "category": "assessment",
        "text": "Repayment capacity adequate but leverage-sensitive.",
        "parameter_result_ids": ["pr_lev"], "evidence_ids": ["E1"],
        "quoted_values": [{"parameter_result_id": "pr_lev", "value": 3.07}],
    },
    "material_risks": [
        {"claim_id": "r1", "category": "risk", "text": "Elevated leverage.",
         "parameter_result_ids": ["pr_lev"], "evidence_ids": ["E1"]},
    ],
    "open_questions": ["Is the revolver accessible under downside leverage?"],
}


def test_orchestrator_emits_claim_level_conclusion(db_session):
    agent, out = _run_orchestrator(db_session, "financial_orchestrator", _GOOD_RESPONSE)
    assert out.execution.status.value == "ok"
    conclusion = build_topic_conclusion(
        Topic.FINANCIAL, out.execution.parsed, analysis_run_id="AR1",
        orchestrator_run_id=out.agent_run.run_id, score_reference="S_fin:1",
    )
    assert conclusion.overall_assessment.claim_id == "a1"
    assert conclusion.overall_assessment.parameter_result_ids == ["pr_lev"]
    assert conclusion.material_risks[0].category.value == "risk"
    assert conclusion.open_questions  # surfaced, not resolved

    store = ConclusionStore(db_session)
    row = store.persist(conclusion, case_id="C1", snapshot_version=1)
    assert row.topic == "financial"
    assert row.acceptance_state == "accepted"


def test_exact_quoted_number_passes_validation(db_session):
    _, out = _run_orchestrator(db_session, "financial_orchestrator", _GOOD_RESPONSE)
    validator = DeterministicValidator(
        admitted_evidence_ids={"E1"}, known_parameters={"pr_lev": 3.07},
    )
    assert validator.validate(out.execution.parsed).valid


def test_invented_number_is_rejected(db_session):
    response = {
        "overall_assessment": {
            "claim_id": "a1", "category": "assessment",
            "text": "Leverage should fall to 2.6x next year.",
            "evidence_ids": ["E1"],
            # No accepted ParameterResult pr_fcast exists => rejected.
            "quoted_values": [{"parameter_result_id": "pr_fcast", "value": 2.6}],
        }
    }
    _, out = _run_orchestrator(db_session, "financial_orchestrator", response)
    validator = DeterministicValidator(
        admitted_evidence_ids={"E1"}, known_parameters={"pr_lev": 3.07},
    )
    outcome = validator.validate(out.execution.parsed)
    assert not outcome.valid
    assert any(i.check is ValidationCheck.NUMERIC_CORRESPONDENCE
               for i in outcome.issues)


def test_financial_orchestrator_input_contains_only_contracted_inputs():
    from app.schemas.agentic import (
        AcceptanceState,
        Method,
        ParameterResult,
        ParameterStatus,
        RiskScore,
        ScoreKind,
        ScoreStatus,
        Topic,
    )
    from app.services.orchestration import build_financial_orchestrator_input

    def _p(pid, run="AR1", accepted=True, topic=Topic.FINANCIAL):
        return ParameterResult(
            parameter_result_id=f"pr_{pid}", analysis_run_id=run,
            parameter_id=pid, topic=topic, value=1.0, value_type="ratio",
            method=Method.DETERMINISTIC, status=ParameterStatus.OK,
            formula_id="f", formula_version="1",
            acceptance_state=(AcceptanceState.ACCEPTED if accepted
                              else AcceptanceState.SUPERSEDED),
        )

    score = RiskScore(score_id="s_fin", analysis_run_id="AR1",
                      kind=ScoreKind.FINANCIAL, status=ScoreStatus.FINAL, band=2,
                      scoring_config_version=1, scoring_config_hash="h")
    inp = build_financial_orchestrator_input(
        analysis_run_id="AR1",
        parameters=[
            _p("net_leverage"),
            _p("net_leverage_super", accepted=False),       # superseded -> excluded
            _p("mgmt_quality", topic=Topic.BUSINESS),        # wrong topic -> excluded
        ],
        financial_score=score,
        stress_results=[{"scenario": "downside", "dscr": 1.1}],
        covenant_results=[{"covenant": "max_net_leverage", "headroom": 0.1}],
    )
    payload = inp.as_prompt_inputs()
    pids = {p["parameter_id"] for p in payload["parameters"]}
    assert pids == {"net_leverage"}  # only accepted Financial params
    assert payload["score"]["kind"] == "financial"
    assert payload["stress_results"] and payload["covenant_results"]
    # The contract has no slot for raw documents.
    assert "documents" not in payload and "raw_sources" not in payload


def test_orchestrator_input_rejects_wrong_score_kind():
    from app.schemas.agentic import RiskScore, ScoreKind, ScoreStatus
    from app.services.orchestration import (
        OrchestratorInputError,
        build_business_orchestrator_input,
    )

    fin = RiskScore(score_id="s", analysis_run_id="AR1", kind=ScoreKind.FINANCIAL,
                    status=ScoreStatus.FINAL, band=2, scoring_config_version=1,
                    scoring_config_hash="h")
    with pytest.raises(OrchestratorInputError):
        build_business_orchestrator_input(
            analysis_run_id="AR1", parameters=[], business_score=fin)


def test_business_orchestrator_conclusion(db_session):
    response = {
        "overall_assessment": {
            "claim_id": "b1", "category": "assessment",
            "text": "Durable franchise with moderate concentration.",
            "parameter_result_ids": ["PR_HHI"], "evidence_ids": ["E1"],
        },
        "strengths": [
            {"claim_id": "s1", "category": "strength", "text": "Network scale.",
             "parameter_result_ids": ["PR_HHI"], "evidence_ids": ["E1"]},
        ],
    }
    _, out = _run_orchestrator(db_session, "business_orchestrator", response)
    conclusion = build_topic_conclusion(
        Topic.BUSINESS, out.execution.parsed, analysis_run_id="AR1",
        orchestrator_run_id=out.agent_run.run_id, score_reference="S_bus:1",
    )
    assert conclusion.topic is Topic.BUSINESS
    assert conclusion.strengths[0].claim_id == "s1"
