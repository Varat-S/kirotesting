"""Milestone 1.1 — typed agentic contracts (Req 2, 3, 10, 27, 31, 32, 35, 36).

These tests pin the invariants the whole agentic architecture relies on:
honest method classification, risk/evidence-quality separation, run identity on
every analytical contract, score status/band coupling, claim-level provenance,
and the absence of a mutable candidate-selection flag.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.agentic import (
    AcceptanceState,
    AgenticAnalysisRun,
    AgentRun,
    CandidateStructure,
    ChallengeFinding,
    ChallengeSeverity,
    ConclusionClaim,
    ClaimCategory,
    EvidencePacket,
    EvidenceQuality,
    Method,
    ParameterResult,
    ParameterStatus,
    RiskScore,
    RunStatus,
    ScoreKind,
    ScoreStatus,
    Topic,
    TopicConclusion,
    ValidationStatus,
)


def _deterministic_param(**overrides) -> dict:
    base = dict(
        parameter_result_id="PR1",
        analysis_run_id="AR1",
        parameter_id="net_leverage",
        topic=Topic.FINANCIAL,
        value=3.07,
        value_type="ratio",
        method=Method.DETERMINISTIC,
        status=ParameterStatus.OK,
        formula_id="net_debt_to_ebitda",
        formula_version="1",
    )
    base.update(overrides)
    return base


# --- ParameterResult method/origin invariants (Req 2.2) --------------------


def test_deterministic_parameter_requires_formula():
    pr = ParameterResult(**_deterministic_param())
    assert pr.method is Method.DETERMINISTIC
    assert pr.formula_id and pr.formula_version


def test_deterministic_parameter_missing_formula_rejected():
    with pytest.raises(ValidationError):
        ParameterResult(**_deterministic_param(formula_id=None))


def test_deterministic_parameter_with_agent_run_id_rejected():
    with pytest.raises(ValidationError):
        ParameterResult(**_deterministic_param(agent_run_id="run-1"))


def test_llm_parameter_requires_agent_identity():
    with pytest.raises(ValidationError):
        ParameterResult(
            parameter_result_id="PR2",
            analysis_run_id="AR1",
            parameter_id="management_quality",
            topic=Topic.BUSINESS,
            value="experienced",
            value_type="category",
            method=Method.LLM,
            status=ParameterStatus.OK,
        )


def test_hybrid_parameter_with_agent_identity_ok():
    pr = ParameterResult(
        parameter_result_id="PR3",
        analysis_run_id="AR1",
        parameter_id="max_net_leverage_covenant",
        topic=Topic.FINANCIAL,
        value=3.5,
        value_type="number",
        method=Method.HYBRID,
        status=ParameterStatus.OK,
        agent_id="covenant_extraction",
        agent_run_id="run-9",
        evidence_ids=["E1"],
    )
    assert pr.method is Method.HYBRID


# --- risk vs evidence quality are independent (Req 10) ---------------------


def test_risk_signal_and_evidence_quality_independent():
    pr = ParameterResult(
        **_deterministic_param(
            risk_signal=4, evidence_quality=EvidenceQuality.LOW, confidence=0.2
        )
    )
    # Low evidence quality does NOT change the (high) risk band.
    assert pr.risk_signal == 4
    assert pr.evidence_quality is EvidenceQuality.LOW


def test_parameter_status_enum_enforced():
    with pytest.raises(ValidationError):
        ParameterResult(**_deterministic_param(status="totally-made-up"))


# --- analysis_run_id required on every analytical contract (Req 27.3) ------


def test_analysis_run_id_required_on_parameter_result():
    payload = _deterministic_param()
    del payload["analysis_run_id"]
    with pytest.raises(ValidationError):
        ParameterResult(**payload)


def test_analysis_run_id_required_on_agent_run():
    with pytest.raises(ValidationError):
        AgentRun(
            run_id="run-1",
            agent_id="business_model",
            agent_definition_hash="abc",
            topic=Topic.BUSINESS,
            case_id="C1",
            snapshot_version=1,
            prompt_id="business_model_v1.0",
            prompt_version=1,
            prompt_hash="h",
            model_id="fake-deterministic-v1",
            input_hash="ih",
            validation_status=ValidationStatus.VALID,
            execution_wave=0,
        )  # missing analysis_run_id


def test_analysis_run_lifecycle_fields():
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
    assert run.status is RunStatus.CREATED
    assert run.analysis_mode == "agentic"


# --- RiskScore status/band coupling (Remediation 3) ------------------------


def test_risk_score_final_requires_band():
    with pytest.raises(ValidationError):
        RiskScore(
            score_id="S1",
            analysis_run_id="AR1",
            kind=ScoreKind.FINANCIAL,
            status=ScoreStatus.FINAL,
            band=None,
            scoring_config_version=1,
            scoring_config_hash="sh",
        )


def test_risk_score_unavailable_forbids_band():
    with pytest.raises(ValidationError):
        RiskScore(
            score_id="S2",
            analysis_run_id="AR1",
            kind=ScoreKind.FACILITY,
            status=ScoreStatus.UNAVAILABLE,
            band=2,
            scoring_config_version=1,
            scoring_config_hash="sh",
        )


def test_risk_score_unavailable_without_band_ok():
    score = RiskScore(
        score_id="S3",
        analysis_run_id="AR1",
        kind=ScoreKind.FACILITY,
        status=ScoreStatus.UNAVAILABLE,
        scoring_config_version=1,
        scoring_config_hash="sh",
        missing_required_parameter_ids=["facility_terms"],
    )
    assert score.band is None
    assert score.method == "deterministic"


# --- claim-level provenance (Remediation 4 / Req 35) -----------------------


def test_conclusion_claim_carries_parameter_and_evidence_ids():
    claim = ConclusionClaim(
        claim_id="CL1",
        category=ClaimCategory.RISK,
        text="Net leverage is elevated at 3.07x.",
        parameter_result_ids=["PR1"],
        evidence_ids=["E1"],
        materiality="high",
    )
    assert claim.parameter_result_ids == ["PR1"]
    assert claim.evidence_ids == ["E1"]


def test_topic_conclusion_uses_typed_claims():
    assessment = ConclusionClaim(
        claim_id="CL0",
        category=ClaimCategory.ASSESSMENT,
        text="Repayment capacity is adequate but leverage-sensitive.",
        parameter_result_ids=["PR1"],
        evidence_ids=["E1"],
    )
    tc = TopicConclusion(
        topic=Topic.FINANCIAL,
        analysis_run_id="AR1",
        overall_assessment=assessment,
        score_reference="S1:1",
        orchestrator_run_id="run-fin-orch",
    )
    assert tc.overall_assessment.claim_id == "CL0"
    assert tc.acceptance_state is AcceptanceState.ACCEPTED


# --- immutable CandidateStructure (Remediation 11 / Req 36) -----------------


def test_candidate_structure_has_no_selected_field():
    assert "selected" not in CandidateStructure.model_fields
    with pytest.raises(ValidationError):
        CandidateStructure(
            candidate_id="CS1",
            analysis_run_id="AR1",
            selected=True,  # extra=forbid rejects this
        )


# --- ChallengeFinding targets a claim and never mutates --------------------


def test_challenge_finding_targets_claim_id():
    cf = ChallengeFinding(
        challenge_id="CH1",
        analysis_run_id="AR1",
        target="CL1",
        issue_type="unsupported_claim",
        severity=ChallengeSeverity.MATERIAL,
        reason="Claim relies on a parameter under review.",
        affected_parameter_ids=["PR1"],
        requested_rerun_scope=["financial_orchestrator"],
        requires_reanalysis=True,
    )
    assert cf.target == "CL1"
    assert cf.severity is ChallengeSeverity.MATERIAL


# --- EvidencePacket carries routing identity (Req 4) -----------------------


def test_evidence_packet_minimal():
    packet = EvidencePacket(
        case_id="C1",
        snapshot_version=1,
        router_version="router-1",
        analysis_run_id="AR1",
        agent_id="business_model",
        evidence_ids=["E1", "E2"],
    )
    assert packet.packet_hash is None  # filled by the router
    assert packet.agent_id == "business_model"
