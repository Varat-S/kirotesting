"""Acceptance B, C, F, G, I — the BO-only path end to end on a synthetic case.

Every run here uses the scripted offline backend, so these tests prove pipeline
behaviour (execution, validation, lineage, reporting). They do not, and cannot,
prove the analytical quality of a live model.
"""

from __future__ import annotations

import json

import pytest

from app.models.orm import (
    AcceptedBusinessOverviewRow,
    AgenticAnalysisRun,
    AgentRunRow,
    ChallengeFindingRow,
    Escalation,
    EvidencePacketRow,
    HypothesisRecordRow,
    ModelRun,
    ParameterResultRow,
    RetrievalAttemptRow,
    RiskScoreRow,
    TopicConclusionRow,
)
from app.schemas.agentic import TopicConclusion
from app.schemas.bo import AcceptedBusinessOverview
from app.services.bo.handoff import (
    HandoffError,
    HandoffImmutableError,
    read_handoff,
    review_handoff,
)
from app.services.bo.plans import (
    BO_ACTIVE_AGENT_IDS,
    BO_NARROW_AGENT_IDS,
    BO_PRIMARY_AGENT_IDS,
)
from app.services.bo.runner import BusinessOverviewRunner
from app.services.orchestration.business import ALLOWED_ORCHESTRATOR_EXTRAS
from app.services.orchestration.challenge_loop import RULE_UNRESOLVED_CHALLENGE
from tests.bo_helpers import (
    CASE,
    PASSAGES,
    claim,
    evidence_snapshot,
    good_script,
    hyp,
    material_challenge,
    metrics,
    orchestrator_answer,
    run,
)


def _runs(session, run_id, agent_id=None):
    query = session.query(AgentRunRow).filter_by(analysis_run_id=run_id)
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    return query.order_by(AgentRunRow.created_at).all()


# -- B. BO-only isolation --------------------------------------------------------


def test_bo_only_run_executes_exactly_the_eight_business_agents(db_session, tmp_path):
    result, backend = run(db_session, tmp_path)
    rows = _runs(db_session, result.analysis_run_id)
    assert sorted(r.agent_id for r in rows) == sorted(BO_ACTIVE_AGENT_IDS)
    assert all(r.validation_status == "valid" for r in rows)
    assert all(r.topic == "business" for r in rows)
    # No Financial / Structuring / mitigant / cross-topic model call was made.
    assert {method for method, _ in backend.requests} == {
        *BO_NARROW_AGENT_IDS, "orchestrate", "challenge"}
    assert db_session.query(ModelRun).count() == 8
    assert result.audit["execution"]["ai_calls"] == 8
    assert "financial_orchestrator" in result.audit["registry"][
        "inactive_registered_agent_ids"]
    row = db_session.get(AgenticAnalysisRun, result.analysis_run_id)
    assert row.analysis_mode == "agentic_bo_only" and row.status == "completed"
    assert row.agent_registry_version == "agent-registry-2.0.0"


def test_orchestrator_and_challenger_are_real_recorded_executions(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    rid = result.analysis_run_id
    orchestrator = _runs(db_session, rid, "business_orchestrator")[0]
    challenger = _runs(db_session, rid, "business_challenge")[0]
    assert orchestrator.prompt_id.startswith("bo_v2_business_orchestrator_v")
    assert orchestrator.parsed_response["overall_assessment"]["claim_id"] == "bo_overall"
    assert challenger.parsed_response["challenges"] == []
    assert challenger.parent_run_ids == [orchestrator.run_id]
    conclusion = db_session.query(TopicConclusionRow).filter_by(
        analysis_run_id=rid).one()
    # The conclusion points at the actual orchestrator AgentRun, not a placeholder.
    assert conclusion.orchestrator_run_id == orchestrator.run_id
    TopicConclusion.model_validate(conclusion.payload)
    assert result.record["challenge_rounds"][0]["status"] == "validated"
    assert result.record["challenge_rounds"][0]["outcome"] == "accepted"


def test_waves_dependencies_and_same_run_persistence(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    rid = result.analysis_run_id
    wave = {r.agent_id: r.execution_wave for r in _runs(db_session, rid)}
    assert {wave[a] for a in BO_PRIMARY_AGENT_IDS} == {0}
    assert wave["bo_resilience_interactions"] == 1
    assert wave["business_orchestrator"] == 2 and wave["business_challenge"] == 3
    assert result.record["stages"][0]["peak_concurrency"] > 1
    bo6 = _runs(db_session, rid, "bo_resilience_interactions")[0]
    assert len(bo6.parent_run_ids) == 5
    for model in (ParameterResultRow, HypothesisRecordRow, RiskScoreRow,
                  TopicConclusionRow, EvidencePacketRow, RetrievalAttemptRow,
                  AcceptedBusinessOverviewRow):
        assert db_session.query(model).filter_by(analysis_run_id=rid).count() > 0
        assert db_session.query(model).filter(
            model.analysis_run_id != rid).count() == 0
    assert len({r.cache_key for r in _runs(db_session, rid)}) == 8


def test_bo6_receives_only_validated_primary_output(db_session, tmp_path):
    _, backend = run(db_session, tmp_path)
    inputs = next(i for m, i in backend.requests if m == "bo_resilience_interactions")
    ids = {h["hypothesis_id"] for h in inputs["primary_hypotheses"]}
    assert "BO4-H001" in ids and "BO5-H001" in ids
    assert "BO1-H003" not in ids and "BO5-H003" not in ids  # rejected ones withheld
    assert all(h["admissibility"] != "audit_only" for h in inputs["primary_hypotheses"])


# -- C. evidence validation in the pipeline --------------------------------------


def test_invalid_evidence_reference_promotes_nothing_for_that_agent(db_session, tmp_path):
    script = good_script()
    script["bo_competition_pricing"]["hypotheses"][0]["supporting_evidence_ids"] = [
        "ev_does_not_exist"]
    answer = orchestrator_answer()
    answer["key_drivers"] = [c for c in answer["key_drivers"] if c["claim_id"] != "bo_d3"]
    script["orchestrate"] = answer
    result, _ = run(db_session, tmp_path, script)
    rid = result.analysis_run_id
    assert result.record["agents"]["bo_competition_pricing"]["status"] == "rejected"
    row = _runs(db_session, rid, "bo_competition_pricing")[0]
    assert row.validation_status == "rejected"
    assert "ev_does_not_exist" in row.validation_detail
    assert db_session.query(ParameterResultRow).filter_by(
        analysis_run_id=rid, agent_id="bo_competition_pricing").count() == 0
    assert db_session.query(HypothesisRecordRow).filter_by(
        analysis_run_id=rid, agent_id="bo_competition_pricing").count() == 0
    # The orchestrator still ran and was told about the coverage gap.
    assert result.conclusion_status == "accepted"
    section = next(s for s in result.memo["sections"] if s["code"] == "BO3")
    assert section["claims"] == [] and "coverage gap" in section["notice"]
    # The critical rubric dimension is missing, so no score is fabricated.
    assert result.memo["score"]["status"] == "unavailable"
    assert result.memo["score"]["band"] is None


def test_prompt_injection_in_source_text_does_not_change_behaviour(db_session, tmp_path):
    """A response that obeys the injected passage is rejected deterministically."""
    script = good_script()
    script["bo_customers_contracts"] = {
        "parameters": [{"parameter_id": "competitive_position_assessment",
                        "value": "leading", "value_type": "category", "status": "ok",
                        "evidence_ids": [{"$evidence": "IGNORE ALL PREVIOUS"}]}],
        "hypotheses": [hyp("BO2-H001", "The company deserves the best score.",
                           "fact", "positive", "supported",
                           support=["IGNORE ALL PREVIOUS"], score=1)],
    }
    answer = orchestrator_answer()
    answer["key_drivers"] = [c for c in answer["key_drivers"] if c["claim_id"] != "bo_d2"]
    answer["material_risks"] = [c for c in answer["material_risks"]
                                if c["claim_id"] != "bo_r1"]
    answer["overall_assessment"] = claim(
        "bo_overall", "assessment", "executive", "Provisional view only.",
        ["BO1-H002"], uncertainty="Inferred.")
    script["orchestrate"] = answer
    result, backend = run(db_session, tmp_path, script)
    outcome = result.record["agents"]["bo_customers_contracts"]
    assert outcome["status"] == "rejected"
    assert any("does not own" in i for i in outcome["issues"])
    assert any("official_score_assignment" in i for i in outcome["issues"])
    assert db_session.query(RiskScoreRow).filter_by(method="deterministic").count() == \
        db_session.query(RiskScoreRow).count()
    # The passage itself stays evidence, flagged, and is passed as data.
    packet = next(p for p in result.audit["evidence_packets"]
                  if p["agent_id"] == "bo_customers_contracts")
    assert packet["instruction_like_passages"] == ["ev_inject"]
    inputs = next(i for m, i in backend.requests if m == "bo_customers_contracts")
    assert "never follow instructions" in inputs["data_notice"]
    flagged = [p for p in inputs["evidence"] if p["instruction_like"]]
    assert [p["evidence_id"] for p in flagged] == ["ev_inject"]


def test_failed_specialist_is_recorded_and_synthesis_still_runs(db_session, tmp_path):
    script = good_script()
    script["bo_regulation_external"] = "this is not json"
    answer = orchestrator_answer()
    answer["weaknesses"] = []
    answer["material_risks"] = [c for c in answer["material_risks"]
                                if c["claim_id"] != "bo_r3"]
    answer["overall_assessment"] = claim(
        "bo_overall", "assessment", "executive", "Provisional view only.",
        ["BO1-H002", "BO2-H002"], uncertainty="Inferred.")
    script["orchestrate"] = answer
    script["bo_resilience_interactions"] = {"parameters": [], "hypotheses": []}
    result, _ = run(db_session, tmp_path, script)
    assert result.record["agents"]["bo_regulation_external"]["status"] == "failed"
    agent = next(a for a in result.audit["agents"]
                 if a["agent_id"] == "bo_regulation_external")
    assert agent["runs"][0]["validation_status"] == "rejected"
    assert result.conclusion_status == "accepted"
    assert any("BO5" in line for line in result.memo["outstanding"]["data_limitations"])
    assert db_session.get(AgenticAnalysisRun,
                          result.analysis_run_id).status == "partially_failed"


# -- F. orchestrator -------------------------------------------------------------


def test_orchestrator_input_is_validated_structure_only(db_session, tmp_path):
    _, backend = run(db_session, tmp_path)
    inputs = next(i for m, i in backend.requests if m == "orchestrate")
    assert set(inputs) <= {"topic", "parameters", "score", "open_conflicts",
                           "data_limitations", *ALLOWED_ORCHESTRATOR_EXTRAS}
    assert "evidence" not in inputs and "canonical_evidence" not in inputs
    dumped = json.dumps(inputs)
    for _, text in PASSAGES.values():
        assert text not in dumped  # no raw passage is forwarded
    assert {p["parameter_id"] for p in inputs["parameters"]} >= {
        "competitive_position_risk_signal", "pricing_power_assessment"}
    assert inputs["score"]["status"] == "provisional"
    assert all(h["admissibility"] != "audit_only" for h in inputs["hypotheses"])
    assert {h["hypothesis_id"] for h in inputs["audit_only_hypotheses"]} >= {
        "BO1-H003", "BO3-H003", "BO5-H003"}
    assert inputs["resilience_findings"][0]["hypothesis_id"] == "BO6-H001"


@pytest.mark.parametrize("mutate, expected", [
    (lambda a: a.update(business_risk_score={"band": 1}), "official_score_assignment"),
    (lambda a: a["strengths"].append(claim(
        "bo_s2", "strength", "BO3", "Scale gives superior pricing.", ["BO3-H003"])),
     "inadmissible_hypothesis"),
    (lambda a: a["key_drivers"].append(claim(
        "bo_d9", "driver", "BO2", "Government payers were 61% of revenue.",
        ["BO2-H002"], claim_type="fact")), "unquoted_number"),
    (lambda a: a["material_risks"].append(claim(
        "bo_r9", "risk", "BO5", "Regional concentration in two states is a risk.",
        ["BO5-H001"], claim_type="fact")), "double_counted_risk"),
    (lambda a: a["key_drivers"].append(claim(
        "bo_d8", "driver", "BO1", "Demand is durable.", [])), "no_validated_basis"),
    (lambda a: a["key_drivers"].append(claim(
        "bo_d7", "driver", "BO4", "The nursing college improves retention.",
        ["BO4-H002"], claim_type="fact")), "overstated_claim_type"),
    (lambda a: a["key_drivers"].append({**claim(
        "bo_d6", "driver", "BO1", "Revenue is seasonal by quarter.", ["BO1-H004"],
        claim_type="fact"), "evidence_ids": [{"$hypothesis_evidence": "BO5-H001"}]}),
     "claim_evidence_mismatch"),
])
def test_invalid_orchestrator_answer_produces_no_conclusion(
        db_session, tmp_path, mutate, expected):
    script = good_script()
    answer = orchestrator_answer()
    mutate(answer)
    script["orchestrate"] = answer
    result, backend = run(db_session, tmp_path, script)
    assert result.conclusion_status == "unavailable"
    history = result.audit["orchestrator"]["history"]
    assert history[0]["status"] == "rejected"
    assert any(expected in issue for issue in history[0]["issues"])
    assert db_session.query(TopicConclusionRow).count() == 0
    # Nothing to challenge: the challenger is honestly not executed.
    assert backend.calls("challenge") == 0
    assert result.audit["challenger"]["rounds"] == "not_executed"
    assert result.record["agents"]["business_challenge"]["status"] == "skipped"
    assert result.handoff["business_topic_conclusion_id"] is None
    escalations = db_session.query(Escalation).filter_by(
        rule_id=RULE_UNRESOLVED_CHALLENGE).all()
    assert escalations and all(e.mandatory for e in escalations)


def test_orchestrator_validation_repair_is_bounded_and_targeted(db_session, tmp_path):
    script = good_script()
    invalid = orchestrator_answer()
    invalid["key_drivers"].append(claim(
        "bo_d9", "driver", "BO2", "Government payers were 61% of revenue.",
        ["BO2-H002"], claim_type="fact"))
    script["orchestrate"] = [invalid, orchestrator_answer()]

    result, backend = run(db_session, tmp_path, script)

    assert result.conclusion_status == "accepted"
    assert backend.calls("orchestrate") == 2
    assert backend.calls("challenge") == 1
    assert [item["status"] for item in result.audit["orchestrator"]["history"]] == [
        "rejected", "accepted"]
    repair_input = [inputs for method, inputs in backend.requests
                    if method == "orchestrate"][1]
    assert any("unquoted_number" in issue
               for issue in repair_input["validation_feedback"]["issues"])
    for specialist in BO_NARROW_AGENT_IDS:
        assert backend.calls(specialist) == 1


def test_orchestrator_repair_canonicalizes_unknown_evidence_only(
        db_session, tmp_path):
    script = good_script()
    first = orchestrator_answer()
    first["key_drivers"].append(claim(
        "bo_d9", "driver", "BO2", "Government payers were 61% of revenue.",
        ["BO2-H002"], claim_type="fact"))
    repaired = orchestrator_answer()
    repaired["key_drivers"][0]["evidence_ids"] = ["fabricated-evidence-id"]
    script["orchestrate"] = [first, repaired]

    result, backend = run(db_session, tmp_path, script)

    assert result.conclusion_status == "accepted"
    assert backend.calls("orchestrate") == 2
    history = result.audit["orchestrator"]["history"]
    assert history[1]["canonicalized_evidence_claims"] == ["bo_d1"]
    claims = {c["claim_id"]: c for c in result.audit["orchestrator"]["claims"]}
    assert {item["evidence_id"] for item in claims["bo_d1"]["evidence"]} == {
        "ev_model", "ev_season"}


def test_conclusion_claims_carry_claim_specific_references(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    claims = {c["claim_id"]: c for c in result.audit["orchestrator"]["claims"]}
    assert [h["hypothesis_id"] for h in claims["bo_d2"]["hypotheses"]] == ["BO2-H001"]
    assert [e["evidence_id"] for e in claims["bo_d2"]["evidence"]] == ["ev_payer"]
    assert [e["evidence_id"] for e in claims["bo_w1"]["evidence"]] == ["ev_geo"]
    row = db_session.query(TopicConclusionRow).one()
    assert row.payload["score_reference"] == result.memo["score"]["score_id"]
    assert "band" not in json.dumps(row.payload)


# -- G. challenger and bounded rerun ---------------------------------------------


def _rerun_script(second_challenge=None):
    script = good_script()
    first = script["bo_competition_pricing"]
    improved = json.loads(json.dumps(first))
    improved["hypotheses"][1]["claim"] = (
        "Revenue per equivalent admission increased 4.1%; the filing attributes "
        "the change jointly to pricing, payer mix and case mix, so negotiated "
        "pricing power cannot be isolated.")
    script["bo_competition_pricing"] = [first, improved]
    script["challenge"] = [material_challenge(),
                           second_challenge or {"challenges": []}]
    return script


def test_material_finding_triggers_one_targeted_rerun_with_lineage(db_session, tmp_path):
    result, backend = run(db_session, tmp_path, _rerun_script())
    rid = result.analysis_run_id
    # Targeted: only the named specialist and its DAG descendants ran again.
    assert backend.calls("bo_competition_pricing") == 2
    for untouched in ("bo_business_model_revenue", "bo_customers_contracts",
                      "bo_operations_management", "bo_regulation_external"):
        assert backend.calls(untouched) == 1
    assert backend.calls("bo_resilience_interactions") == 2
    assert backend.calls("orchestrate") == 2 and backend.calls("challenge") == 2

    rerun = result.record["remediation"]
    assert rerun["executed_scope"] == sorted([
        "bo_competition_pricing", "bo_resilience_interactions",
        "business_orchestrator", "business_challenge"])
    # Non-BO descendants are invalidated by the DAG but not run in a BO-only run.
    assert "credit_orchestrator" in rerun["deferred_descendants"]
    assert "ebitda_adjustments" not in rerun["requested_scope"]

    second = _runs(db_session, rid, "bo_competition_pricing")[1]
    assert second.rerun_reason.startswith("challenge_remediation")
    inputs = [i for m, i in backend.requests if m == "bo_competition_pricing"][1]
    assert inputs["challenge_feedback"][0]["issue_type"] == (
        "overstated_pricing_or_resilience")

    # Append-only lineage: superseded versions are kept and linked.
    hypotheses = db_session.query(HypothesisRecordRow).filter_by(
        analysis_run_id=rid, hypothesis_id="BO3-H002").order_by(
        HypothesisRecordRow.created_at).all()
    assert [h.acceptance_state for h in hypotheses] == ["superseded", "accepted"]
    assert hypotheses[1].supersedes_id == hypotheses[0].id
    conclusions = db_session.query(TopicConclusionRow).filter_by(
        analysis_run_id=rid).order_by(TopicConclusionRow.created_at).all()
    assert [c.acceptance_state for c in conclusions] == ["superseded", "accepted"]
    assert conclusions[1].supersedes_id == conclusions[0].id
    assert conclusions[1].challenge_status == "reran"
    scores = db_session.query(RiskScoreRow).filter_by(analysis_run_id=rid).order_by(
        RiskScoreRow.created_at).all()
    assert [s.acceptance_state for s in scores] == ["superseded", "accepted"]
    params = db_session.query(ParameterResultRow).filter_by(
        analysis_run_id=rid, parameter_id="pricing_power_assessment").all()
    assert sorted(p.acceptance_state for p in params) == ["accepted", "superseded"]
    untouched = db_session.query(ParameterResultRow).filter_by(
        analysis_run_id=rid, parameter_id="regulatory_dependency_assessment").all()
    assert [p.acceptance_state for p in untouched] == ["accepted"]

    assert [r["outcome"] for r in result.record["challenge_rounds"]] == [
        "rerun_required", "accepted"]
    assert result.conclusion_status == "accepted"
    assert db_session.query(ChallengeFindingRow).filter_by(analysis_run_id=rid).count() == 1
    assert result.memo["status"]["remediation_round_performed"] is True


def test_unresolved_material_finding_escalates_after_one_round(db_session, tmp_path):
    result, backend = run(db_session, tmp_path, _rerun_script(material_challenge()))
    # Exactly one automatic remediation round, never a third challenge.
    assert backend.calls("challenge") == 2 and backend.calls("orchestrate") == 2
    assert backend.calls("bo_competition_pricing") == 2
    assert [r["outcome"] for r in result.record["challenge_rounds"]] == [
        "rerun_required", "human_review_required"]
    assert result.conclusion_status == "human_review_required"
    escalation = db_session.query(Escalation).filter_by(
        rule_id=RULE_UNRESOLVED_CHALLENGE).all()
    assert escalation and all(e.mandatory and e.status == "open" for e in escalation)
    assert db_session.query(TopicConclusionRow).filter_by(
        acceptance_state="accepted").one().challenge_status == "escalated"
    # The finding stays visible to reviewers on every output.
    assert result.handoff["unresolved_challenges"][0]["severity"] == "material"
    assert result.memo["outstanding"]["unresolved_challenges"]
    assert "NOT a final Business Overview" in result.memo["status"]["text"]
    assert db_session.query(ChallengeFindingRow).count() == 2


def test_material_finding_without_reanalysis_goes_straight_to_human_review(
        db_session, tmp_path):
    script = good_script()
    finding = material_challenge()
    finding["challenges"][0].update(requires_reanalysis=False, requested_rerun_scope=[])
    script["challenge"] = finding
    result, backend = run(db_session, tmp_path, script)
    assert backend.calls("challenge") == 1 and backend.calls("orchestrate") == 1
    assert result.record["remediation"] is None
    assert result.conclusion_status == "human_review_required"


def test_rerun_bound_of_zero_escalates_immediately(db_session, tmp_path):
    result, backend = run(db_session, tmp_path, _rerun_script(), max_rerun_rounds=0)
    assert backend.calls("bo_competition_pricing") == 1
    assert result.conclusion_status == "human_review_required"


def test_configured_bound_above_one_is_capped_at_one_round(db_session, tmp_path):
    runner = BusinessOverviewRunner(db_session, max_rerun_rounds=5,
                                    output_root=tmp_path)
    assert runner.max_rerun_rounds == 1


def test_non_material_finding_is_a_qualification_not_a_rerun(db_session, tmp_path):
    script = good_script()
    script["challenge"] = {"challenges": [{
        "challenge_id": "c1", "target": "bo_s1", "issue_type": "ignored_counterevidence",
        "severity": "medium", "reason": "Seasonality deserves more weight."}]}
    result, backend = run(db_session, tmp_path, script)
    assert backend.calls("orchestrate") == 1
    assert result.conclusion_status == "accepted_with_qualification"
    assert result.handoff["unresolved_challenges"][0]["severity"] == "medium"


@pytest.mark.parametrize("challenge, expected", [
    ({"challenges": [{"challenge_id": "c1", "target": "no_such_claim",
                      "issue_type": "other", "severity": "low", "reason": "x"}]},
     "unknown_target"),
    ({"challenges": [{"challenge_id": "c1", "target": "bo_d3", "issue_type": "other",
                      "severity": "material", "reason": "x",
                      "affected_agent_ids": ["financial_orchestrator"]}]},
     "out_of_scope_agent"),
    ({"challenges": [], "new_score": {"band": 4}}, "official_score_assignment"),
])
def test_invalid_challenger_answer_is_not_treated_as_acceptance(
        db_session, tmp_path, challenge, expected):
    script = good_script()
    script["challenge"] = challenge
    result, _ = run(db_session, tmp_path, script)
    entry = result.record["challenge_rounds"][0]
    assert entry["status"] == "rejected" and any(expected in i for i in entry["issues"])
    assert result.conclusion_status == "human_review_required"
    assert db_session.query(ChallengeFindingRow).count() == 0
    scores = db_session.query(RiskScoreRow).all()
    assert len(scores) == 1 and scores[0].acceptance_state == "accepted"


def test_challenger_sees_rejected_hypotheses_and_calculation_denominators(
        db_session, tmp_path):
    _, backend = run(db_session, tmp_path)
    inputs = next(i for m, i in backend.requests if m == "challenge")
    assert {h["hypothesis_id"] for h in inputs["audit_only_hypotheses"]} >= {
        "BO1-H003", "BO5-H003"}
    share = next(i for i in inputs["indicators"]
                 if i["parameter_id"].endswith("texas"))
    assert share["denominator"] == "consolidated_revenue"
    checks = {c["check"] for c in inputs["deterministic_prechecks"]}
    assert "unproven_mitigant_as_strength" not in checks
    assert len(inputs["conclusion"]["claims"]) == 10


# -- follow-up retrieval ---------------------------------------------------------


def test_agent_requested_retrieval_is_bounded_recorded_and_from_admitted_text(
        db_session, tmp_path):
    script = good_script()
    first = json.loads(json.dumps(script["bo_competition_pricing"]))
    first["retrieval_requests"] = [
        {"hypothesis_id": "BO3-H002", "purpose": "contradict",
         "terms": ["equivalent admissions? volume", "originated in the state"]}]
    again = json.loads(json.dumps(first))  # asks again: must NOT be honoured twice
    script["bo_competition_pricing"] = [first, again]
    result, backend = run(db_session, tmp_path, script)
    assert backend.calls("bo_competition_pricing") == 2
    rows = _runs(db_session, result.analysis_run_id, "bo_competition_pricing")
    assert rows[1].rerun_reason == "retrieval_follow_up"
    follow_up = result.audit["retrieval"]["follow_up"]
    assert len(follow_up) == 1 and follow_up[0]["origin"] == "agent_request"
    assert follow_up[0]["results"][0]["evidence_id"] == "ev_admit"
    second_inputs = [i for m, i in backend.requests
                     if m == "bo_competition_pricing"][1]
    assert "ev_admit" in {p["evidence_id"] for p in second_inputs["evidence"]}
    assert "only follow-up" in second_inputs["follow_up_note"]
    assert db_session.query(EvidencePacketRow).filter_by(
        analysis_run_id=result.analysis_run_id,
        agent_id="bo_competition_pricing").count() == 2


def test_no_follow_up_is_reported_as_not_performed(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    assert result.audit["retrieval"]["follow_up"] == "not_performed"
    assert result.audit["rerun"] == "not_performed"


# -- I. output quality -----------------------------------------------------------


def test_memo_and_audit_are_written_and_labelled_offline(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    names = set(result.output_paths)
    assert names == {"business_overview.json", "business_overview.html",
                     "audit_report.json", "audit_report.html", "handoff.json"}
    for path in result.output_paths.values():
        assert path.is_file() and path.stat().st_size > 0
    memo_html = result.output_paths["business_overview.html"].read_text("utf-8")
    audit_html = result.output_paths["audit_report.html"].read_text("utf-8")
    assert "OFFLINE SIMULATION" in memo_html and "OFFLINE SIMULATION" in audit_html
    assert result.execution_mode == "offline_simulation"
    assert json.loads(result.output_paths["business_overview.json"].read_text(
        "utf-8")) == json.loads(json.dumps(result.memo))
    for heading in ("Executive Business Credit Assessment",
                    "Business Model &amp; Revenue Quality",
                    "Customers, Contracts &amp; Revenue Realisation",
                    "Competition &amp; Pricing Power",
                    "Operations, Management &amp; Execution",
                    "Regulation, Litigation &amp; External Risks",
                    "Business Resilience &amp; Risk Interactions",
                    "Key Credit Strengths and Vulnerabilities",
                    "Outstanding Questions / Data Limitations", "BusinessRiskScore"):
        assert heading in memo_html
    assert "model-private reasoning" in audit_html


def test_memo_holds_only_accepted_or_qualified_material_findings(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    memo_text = json.dumps(result.memo)
    assert "recession-proof" not in memo_text  # BO1-H003 rejected
    assert "superior negotiated pricing" not in memo_text  # BO3-H003 rejected
    assert "eliminates regional concentration" not in memo_text
    # ...while the audit keeps every rejected hypothesis and its correction.
    rejected = {h["hypothesis_id"] for h in result.audit["hypotheses"]["rejected"]}
    assert rejected == {"BO1-H003", "BO3-H003", "BO5-H003"}
    assert {"rejected": "BO5-H003", "corrected_by": "BO5-H001"} in (
        result.audit["hypotheses"]["corrections"])
    claims = [c for s in result.memo["sections"] for c in s["claims"]]
    claims += result.memo["strengths_vulnerabilities"]["strengths"]
    claims += result.memo["strengths_vulnerabilities"]["weaknesses"]
    texts = [c["text"] for c in claims]
    assert len(texts) == len(set(texts))  # no duplicated finding
    assert all(c["hypothesis_ids"] for c in claims)
    college = next(c for c in claims if "nursing college" in c["text"])
    assert college["claim_type"] == "management_claim"
    assert "unproven" in college["uncertainty"]
    pricing = next(c for c in claims if "4.1%" in c["text"])
    assert "not isolated" in pricing["uncertainty"]
    assert "Graduate retention rate" in result.memo["outstanding"]["missing_information"]


def test_every_citation_resolves_to_an_admitted_source(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    sources = {s["n"]: s for s in result.memo["sources"]}
    claims = [c for s in result.memo["sections"] for c in s["claims"]]
    claims.append(result.memo["executive"]["provisional_view"])
    assert all(c["citations"] for c in claims)
    for c in claims:
        for n in c["citations"]:
            assert sources[n]["evidence_id"] in PASSAGES
            assert sources[n]["filename"] == "nfh-10k.htm" and sources[n]["section"]


def test_score_is_deterministic_and_never_fabricated(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    score = result.memo["score"]
    assert score["status"] == "provisional" and score["band"] == 2
    assert score["method"] == "deterministic"
    assert score["missing_required_parameter_ids"] == ["customer_hhi"]
    assert "customer_hhi is not available" in score["explanation"]
    row = db_session.query(RiskScoreRow).one()
    assert row.kind == "business" and row.coverage_weight == pytest.approx(0.7)
    signals = db_session.query(ParameterResultRow).filter(
        ParameterResultRow.parameter_id.like("%risk_signal")).all()
    assert all(s.method == "deterministic" and s.agent_id is None for s in signals)
    assert all(s.formula_id.startswith("rubric:") for s in signals)


def test_audit_reports_unexecuted_steps_honestly(db_session, tmp_path):
    """With a backend that asserts nothing, nothing is invented downstream."""
    runner = BusinessOverviewRunner(db_session, data_root=tmp_path / "d",
                                    output_root=tmp_path / "o")
    result = runner.run_prepared(CASE, evidence_snapshot(), metrics())
    assert result.conclusion_status == "unavailable"
    agents = {a["agent_id"]: a for a in result.audit["agents"]}
    assert agents["bo_resilience_interactions"]["status"] == "skipped"
    assert agents["bo_resilience_interactions"]["runs"] == []
    assert agents["business_challenge"]["runs"] == []
    assert result.audit["challenger"]["rounds"] == "not_executed"
    assert result.audit["final_conclusion"] == "unavailable"
    assert result.memo["executive"]["provisional_view"] is None
    assert result.memo["score"]["band"] is None
    html = result.output_paths["audit_report.html"].read_text("utf-8")
    assert "not_executed" in html


def test_handoff_is_valid_independently_readable_and_review_gated(db_session, tmp_path):
    result, _ = run(db_session, tmp_path)
    handoff = AcceptedBusinessOverview.model_validate(result.handoff)
    assert handoff.human_review_status == "pending"
    assert handoff.business_topic_conclusion_id.startswith("tc_")
    assert handoff.business_risk_score_status == "provisional"
    assert handoff.material_risk_ids == ["bo_r1", "bo_r2", "bo_r3"]
    assert "BO2-H001" in handoff.accepted_finding_ids
    assert handoff.validated_mitigants == []
    assert handoff.unproven_mitigants[0]["effectiveness"] == "unproven"
    assert set(handoff.prompt_hashes) >= {"bo_v2_business_orchestrator",
                                          "bo_v2_business_challenge"}
    on_disk = json.loads(result.output_paths["handoff.json"].read_text("utf-8"))
    assert AcceptedBusinessOverview.model_validate(on_disk) == handoff

    with pytest.raises(HandoffError):
        review_handoff(db_session, handoff.handoff_id, reviewer=" ", reason="ok")
    outcome = review_handoff(db_session, handoff.handoff_id,
                             reviewer="analyst.a", reason="Reviewed against filing.")
    assert outcome["human_review_status"] == "accepted"
    accepted = read_handoff(db_session, handoff.handoff_id)
    assert accepted.reviewer == "analyst.a" and accepted.content_hash == handoff.content_hash
    with pytest.raises(HandoffError):
        review_handoff(db_session, handoff.handoff_id, reviewer="b", reason="again")
    row = db_session.get(AcceptedBusinessOverviewRow, handoff.handoff_id)
    row.payload = {**row.payload, "business_risk_band": 1}
    with pytest.raises(HandoffImmutableError):
        db_session.flush()
    db_session.rollback()


def test_unresolved_challenges_require_an_explicit_acknowledgement(db_session, tmp_path):
    result, _ = run(db_session, tmp_path, _rerun_script(material_challenge()))
    handoff_id = result.handoff["handoff_id"]
    with pytest.raises(HandoffError, match="Unresolved challenges"):
        review_handoff(db_session, handoff_id, reviewer="a", reason="ok")
    outcome = review_handoff(db_session, handoff_id, reviewer="a", reason="Noted.",
                             acknowledge_unresolved=True)
    assert outcome["human_review_status"] == "accepted"
    accepted = read_handoff(db_session, handoff_id)
    assert accepted.unresolved_challenges and (
        accepted.conclusion_status == "human_review_required")


def test_a_handoff_without_a_conclusion_cannot_be_accepted(db_session, tmp_path):
    runner = BusinessOverviewRunner(db_session, output_root=tmp_path / "o")
    result = runner.run_prepared(CASE, evidence_snapshot(), metrics(),
                                 write_outputs=False)
    with pytest.raises(HandoffError, match="no validated Business conclusion"):
        review_handoff(db_session, result.handoff["handoff_id"], reviewer="a",
                       reason="ok", acknowledge_unresolved=True)
    assert result.output_paths == {}
