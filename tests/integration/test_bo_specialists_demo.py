"""End-to-end: six specialists + validators + demonstration reports.

Model responses are scripted (CI-safe). One separately marked test runs the
live provider and is skipped unless ``BO_LIVE_TEST=1`` and the filing is present.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.orm import AgentRunRow, AgenticAnalysisRun, TopicConclusionRow
from app.services.bo.demo import run_demo
from app.services.bo.demo_report import audit_rows, compute_metrics
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.plans import BO_NARROW_AGENT_IDS
from app.services.bo.sectors import HEALTHCARE, CoverageCheck
from tests.bo_helpers import CASE, evidence_snapshot, good_script, metrics

ROOT = Path(__file__).resolve().parents[2]
ENTITY_CHECK = CoverageCheck(
    "case.nursing_college", ("BO4",),
    "What does the filing say about the nursing college?", ("College of Nursing",),
    ("College of Nursing|nursing college",), 2, "case")


def _prepared():
    return SimpleNamespace(evidence_snapshot=evidence_snapshot(), metrics=metrics(),
                           escalations=[], rejected_sources=[])


def _script():
    script = {k: v for k, v in good_script().items()
              if k not in ("orchestrate", "challenge")}
    script["bo_operations_management"]["checklist_responses"] = [
        {"check_id": "case.nursing_college", "status": "addressed",
         "hypothesis_ids": ["BO4-H002"]},
        {"check_id": "hc.workforce", "status": "addressed", "hypothesis_ids": ["BO4-H001"]},
        {"check_id": "hc.operating_entities", "status": "not_supported_by_evidence",
         "hypothesis_ids": []}]
    script["bo_competition_pricing"]["checklist_responses"] = [
        {"check_id": "hc.unit_revenue_vs_pricing", "status": "addressed",
         "hypothesis_ids": ["BO3-H002"]}]
    script["bo_customers_contracts"]["checklist_responses"] = [
        {"check_id": "hc.contract_rates_vs_volumes", "status": "addressed",
         "hypothesis_ids": ["BO2-H001"]}]
    # The first-run failure modes, as regression fixtures:
    script["bo_resilience_interactions"]["hypotheses"].append({
        "hypothesis_id": "BO6-H002",
        "claim": "Labour cost pressure limits the ability to service debt and could "
                 "trigger a liquidity crisis.",
        "claim_type": "inference", "direction": "negative", "disposition": "supported",
        "supporting_evidence_ids": [{"$hypothesis_evidence": "BO4-H001"}],
        "component_hypothesis_ids": ["BO4-H001", "BO2-H002"],
        "interaction_kind": "compound_risk",
        "transmission_mechanism": "Higher wages reduce cash available after operations."})
    script["bo_regulation_external"]["parameters"][0]["value"] = (
        "High dependency on government programmes and state licensure.")
    return script


def _demo(db_session, tmp_path, script=None, **kwargs):
    backend = ScriptedBoBackend(copy.deepcopy(script or _script()))
    result = run_demo(
        db_session, CASE, package=None, prepared=_prepared(), backend=backend,
        data_root=tmp_path / "data", output_root=tmp_path / "out",
        sector=HEALTHCARE, coverage_checks=[ENTITY_CHECK], **kwargs)
    return result, backend


def test_all_six_specialists_execute_and_validate_without_any_other_agent(
        db_session, tmp_path):
    result, backend = _demo(db_session, tmp_path)
    payload = result["payload"]
    assert [a["status"] for a in payload["agents"]] == ["validated"] * 6
    assert {m for m, _ in backend.requests} == set(BO_NARROW_AGENT_IDS)
    rows = db_session.query(AgentRunRow).all()
    assert {r.agent_id for r in rows} == set(BO_NARROW_AGENT_IDS)
    # At most one coverage follow-up per specialist; never an open-ended loop.
    assert max(backend.calls(a) for a in BO_NARROW_AGENT_IDS) <= 2
    follow_up = [s for s in payload["stages"] if s["stage"] == "coverage_follow_up"]
    assert len(follow_up) == 1
    assert {r.rerun_reason for r in rows if r.rerun_reason} == {"coverage_follow_up"}
    assert db_session.query(TopicConclusionRow).count() == 0  # no memo, no conclusion
    run = db_session.get(AgenticAnalysisRun, payload["meta"]["analysis_run_id"])
    assert run.status == "completed"
    assert run.model_configuration["scope"] == "business_overview_specialists_only"
    assert payload["meta"]["sector_profile"] == "healthcare_provider"


def test_runtime_prompts_carry_each_specialists_own_parameters_only(db_session, tmp_path):
    result, _ = _demo(db_session, tmp_path)
    every = {p for a in result["payload"]["agents"] for p in a["owned_parameters"]}
    for agent in result["payload"]["agents"]:
        sent = agent["calls"][0]["effective_instructions"]  # logged as actually sent
        assert all(p in sent for p in agent["owned_parameters"])
        assert not [p for p in every - set(agent["owned_parameters"]) if p in sent]
        assert sent.endswith(result["payload"]["shared_rules"])
        assert set(agent["calls"][0]["inputs"]["parameter_value_sets"]) <= set(
            agent["owned_parameters"])


def test_bo6_receives_only_the_validated_bo1_to_bo5_packet(db_session, tmp_path):
    result, backend = _demo(db_session, tmp_path)
    inputs = next(i for m, i in backend.requests if m == "bo_resilience_interactions")
    passed = {h["hypothesis_id"]: h for h in inputs["primary_hypotheses"]}
    recorded = {h["hypothesis_id"]: h for a in result["payload"]["agents"][:5]
                for h in a["hypotheses"]}
    assert set(passed) == {k for k, h in recorded.items()
                           if h["admissibility"] != "audit_only"}
    assert "BO1-H003" not in passed and "BO5-H003" not in passed  # rejected
    # Qualifications and attribution travel with the hypothesis.
    assert passed["BO1-H002"]["disposition"] == "qualified"
    assert passed["BO4-H002"]["admissibility"] in ("attributed", "qualified_narrative")
    assert all(not h["hypothesis_id"].startswith("BO6") for h in passed.values())


def test_known_first_run_failures_are_caught_end_to_end(db_session, tmp_path):
    result, _ = _demo(db_session, tmp_path)
    payload = result["payload"]
    by_id = {h["hypothesis_id"]: h for a in payload["agents"] for h in a["hypotheses"]}
    debt = by_id["BO6-H002"]
    assert debt["admissibility"] == "audit_only"
    assert "operating_pressure_to_debt_capacity" in [f["rule_id"] for f in debt["review_flags"]]
    assert payload["fo_handoffs"][0]["variable"] == "debt_service_capacity"
    assert payload["metrics"]["bo_fo_boundary_violations"] == 1
    held = payload["agents"][4]["parameter_adjustments"]
    assert held[0]["parameter_id"] == "regulatory_dependency_assessment"
    assert held[0]["reason"] == "value_not_permitted"
    assert not [p for p in payload["agents"][4]["parameters"]
                if p["parameter_id"] == "regulatory_dependency_assessment"
                and p["status"] == "ok"]
    assert by_id["BO4-H002"]["mitigant"]["effectiveness"] == "unproven"


def test_coverage_is_judged_by_validated_answers_not_by_the_agents_say_so(
        db_session, tmp_path):
    script = _script()
    script["bo_regulation_external"]["checklist_responses"] = [
        {"check_id": "hc.geography", "status": "addressed", "hypothesis_ids": ["BO5-H003"]}]
    result, _ = _demo(db_session, tmp_path, script)
    coverage = {(c["agent_id"], c["check_id"]): c
                for a in result["payload"]["agents"] for c in a["coverage"]}
    assert coverage[("bo_operations_management", "case.nursing_college")]["status"] == "addressed"
    assert coverage[("bo_competition_pricing", "hc.unit_revenue_vs_pricing")][
        "addressed_by_validated_hypotheses"] == ["BO3-H002"]
    assert coverage[("bo_operations_management", "hc.operating_entities")]["status"] == (
        "agent_reported_not_supported")
    # Cited a REJECTED hypothesis: not counted as addressed.
    geography = coverage[("bo_regulation_external", "hc.geography")]
    assert geography["hypothesis_ids_cited"] == ["BO5-H003"]
    assert geography["status"] != "addressed"
    assert geography["addressed_by_validated_hypotheses"] == []
    assert coverage[("bo_customers_contracts", "hc.payer_mix")]["agent_response"] == "no_response"
    call = result["payload"]["agents"][3]["calls"][0]
    sent = call["inputs"]
    assert {c["check_id"] for c in sent["checklist"]} == {
        "hc.operating_entities", "hc.workforce", "case.nursing_college"}
    # The model sees short evidence labels; the transcript maps them back.
    assert "ev_labor" in {call["evidence_aliases"][p["evidence_id"]] for p in sent["evidence"]}


def test_answer_to_an_unasked_coverage_question_is_ignored_and_noted(
        db_session, tmp_path):
    script = _script()
    script["bo_customers_contracts"]["checklist_responses"] = [
        {"check_id": "hc.made_up", "status": "addressed", "hypothesis_ids": ["BO2-H001"]}]
    result, _ = _demo(db_session, tmp_path, script)
    agent = result["payload"]["agents"][1]
    assert agent["status"] == "validated" and agent["hypotheses"]
    assert any("hc.made_up" in a["note"] for a in agent["validator_adjustments"])
    assert not [c for c in agent["coverage"] if c["check_id"] == "hc.made_up"]
    assert all(c["status"] != "addressed" for c in agent["coverage"])


def test_coverage_follow_up_asks_once_and_carries_the_previous_answer(
        db_session, tmp_path):
    script = _script()
    first = script["bo_competition_pricing"]
    first["checklist_responses"] = [
        {"check_id": "hc.unit_revenue_vs_pricing", "status": "addressed",
         "hypothesis_ids": [], "note": "Revenue per admission rose with mix."}]
    second = copy.deepcopy(first)
    second["checklist_responses"][0]["hypothesis_ids"] = ["BO3-H002"]
    script["bo_competition_pricing"] = [first, second, second]
    result, backend = _demo(db_session, tmp_path, script)
    assert backend.calls("bo_competition_pricing") == 2
    agent = result["payload"]["agents"][2]
    asked = agent["calls"][1]["inputs"]["coverage_follow_up"]
    assert [q["check_id"] for q in asked["questions"]] == [
        "hc.unit_revenue_vs_pricing", "hc.local_competition"]
    assert asked["questions"][0]["your_previous_note"] == "Revenue per admission rose with mix."
    assert asked["previous_answer"]["hypotheses"]
    outcome = next(c for c in agent["coverage"]
                   if c["check_id"] == "hc.unit_revenue_vs_pricing")
    assert outcome["status"] == "addressed"
    # Usage of both calls is counted, and the superseded version is kept.
    assert result["payload"]["usage"]["calls"] > 6


def test_reports_are_written_and_faithful_to_the_structured_output(db_session, tmp_path):
    result, _ = _demo(db_session, tmp_path)
    payload, paths = result["payload"], result["paths"]
    assert {"specialists_run.json", "executive_summary.html",
            "specialists_comparison.html", "validation_audit.html",
            "m3_governed_assessment.json", "m3_governed_assessment.html"} <= set(paths)
    stored = json.loads(paths["specialists_run.json"].read_text("utf-8"))
    assert stored["metrics"] == json.loads(json.dumps(payload["metrics"]))
    assert compute_metrics(stored)["hypotheses_usable"] == stored["metrics"]["hypotheses_usable"]

    summary = paths["executive_summary.html"].read_text("utf-8")
    comparison = paths["specialists_comparison.html"].read_text("utf-8")
    audit = paths["validation_audit.html"].read_text("utf-8")
    m3_export = json.loads(paths["m3_governed_assessment.json"].read_text("utf-8"))
    assert m3_export["assessment_status"] == "unavailable"
    assert "unavailable" in paths["m3_governed_assessment.html"].read_text("utf-8")
    hypotheses = [h for a in payload["agents"] for h in a["hypotheses"]]
    for h in hypotheses:
        assert h["hypothesis_id"] in comparison
        shown = f"[{h['hypothesis_id']}]" in summary.split("Unresolved evidence gaps")[0]
        assert shown == (h["admissibility"] != "audit_only")
    assert "OFFLINE SIMULATION" in summary and "no orchestrator" in summary
    assert "debt-capacity" in summary and "rating" in summary
    assert "Management states" in summary and "Qualified" in summary
    assert "liquidity crisis" in summary.split("Unresolved evidence gaps")[1]
    for heading in ("How the company generates revenue", "Sources of operating resilience",
                    "Competitive and payer dynamics", "Management and execution",
                    "External and regulatory exposures",
                    "Cross-specialist risk interactions", "Unresolved evidence gaps"):
        assert heading in summary
    for label in ("Role and exact effective prompt", "Deterministic indicators provided",
                  "Evidence provided", "Parameter assessments", "Validator verdict",
                  "Coverage questions: outcome", "Retrieval requests and retry outcomes",
                  "Rules shared by all six specialists"):
        assert label in comparison
    rows = audit_rows(payload)
    assert {r["hypothesis_id"] for r in rows} >= {"BO6-H002", "BO1-H003", "BO5-H003"}
    assert all(r["hypothesis_id"] in audit for r in rows)
    debt = next(r for r in rows if r["hypothesis_id"] == "BO6-H002")
    assert debt["category"] == "unresolved / held back"
    assert debt["can_reach_memo_as_fact"] is False
    assert "Can reach the memo as a fact?" in audit and "heuristic" in audit
    # Counts are reported as they are, not inflated.
    assert payload["metrics"]["hypotheses_usable"] < payload["metrics"]["hypotheses_recorded"]
    if "specialists_comparison.pdf" in paths:
        assert paths["specialists_comparison.pdf"].read_bytes()[:4] == b"%PDF"


def test_no_secret_or_environment_value_reaches_a_report(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("VERTEX_API_KEY", "SECRET-KEY-VALUE-123")
    result, _ = _demo(db_session, tmp_path)
    for path in result["paths"].values():
        if path.suffix in (".html", ".json"):
            assert "SECRET-KEY-VALUE-123" not in path.read_text("utf-8")


def test_benchmark_replays_a_baseline_through_the_current_validators(db_session, tmp_path):
    first, backend = _demo(db_session, tmp_path)
    baseline = tmp_path / "baseline_calls.json"
    baseline.write_text(json.dumps([
        {"task_type": a["agent_id"], "raw_response": json.dumps(a["calls"][0]["output"]),
         "model_config": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
         "inputs": a["calls"][0]["inputs"]}
        for a in first["payload"]["agents"]]), encoding="utf-8")
    improved = _script()
    improved["bo_resilience_interactions"]["hypotheses"].pop()  # no debt claim now
    result, _ = _demo(db_session, tmp_path, improved, baseline_calls=baseline,
                      baseline_label=first["payload"]["meta"]["analysis_run_id"])
    bench = result["payload"]["benchmark"]
    titles = [c["title"] for c in bench["columns"]]
    assert titles[1:] == ["Baseline, re-validated", "This run"]
    recorded, revalidated, new = (c["metrics"] for c in bench["columns"])
    assert recorded["tokens"]["total_tokens"] == 660
    assert recorded["hypotheses_usable"] == first["payload"]["metrics"]["hypotheses_usable"]
    assert revalidated["bo_fo_boundary_violations"] == 1
    assert new["bo_fo_boundary_violations"] == 0
    debt = bench["known_problem_checks"][0]
    assert debt["resolved"] and "BO6-H002" in debt["before"]
    html = result["paths"]["benchmark.html"].read_text("utf-8")
    assert "not treated as an improvement" in html and "run-to-run variation" in html
    assert result["paths"]["baseline_revalidated.json"].is_file()


@pytest.mark.skipif(
    os.environ.get("BO_LIVE_TEST") != "1"
    or not (ROOT / "examples/healthcare/hca-2025/filing/bundle.json").is_file(),
    reason="Live-model integration test; set BO_LIVE_TEST=1 (incurs provider cost).")
def test_live_hca_specialists_run(db_session, tmp_path):  # pragma: no cover - live
    from app.core.config import get_settings
    from app.services.bo.sectors import load_case_checks
    from app.services.llm.providers_vertex import VertexProviderBackend
    from app.services.pipeline.package import SourcePackage

    backend = VertexProviderBackend(get_settings())
    backend.check_configuration()
    hca = ROOT / "examples/healthcare/hca-2025"
    result = run_demo(
        db_session, "HCA_LIVE_TEST", package=SourcePackage.load(hca / "package.json"),
        backend=backend, data_root=tmp_path / "data", output_root=tmp_path / "out",
        coverage_checks=load_case_checks(hca / "coverage_checks.json"))
    payload = result["payload"]
    assert payload["meta"]["execution_mode"] == "live_model"
    assert payload["metrics"]["model_calls"] >= 6
    usable = [h for a in payload["agents"] for h in a["hypotheses"]
              if h["admissibility"] != "audit_only"]
    # Structural guarantees that must hold whatever the model says:
    assert all(not any(f["rule_id"] == "operating_pressure_to_debt_capacity"
                       for f in h["review_flags"]) for h in usable)
    assert all(h["evidence_quotes"] for h in usable
               if h["claim_type"] in ("fact", "management_claim"))
