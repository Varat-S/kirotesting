"""Offline M3 evidence gates, private diagnostics and reproducible local runs.

Scripted responses exercise the real validators, not model compliance with new
instructions. No live provider, external network or credential is used.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import jsonschema
import pytest
from sqlalchemy import select

from app.core.hashing import content_hash
from app.models.base import create_engine_and_session, init_db
from app.models.orm import AgenticAnalysisRun, ModelRun, StageArtifact
from app.services.bo.m3_demo import write_m3_demo
from app.services.bo.m3_diagnostics import load_recorded_m3_run
from app.services.bo.m3_standalone import run_m3
from app.services.llm.client import LLMBackend, LLMRawResult
from app.services.llm.providers import ProviderCallError
from app.services.pipeline.artifacts import ArtifactIntegrityError, verified_artifacts
from tests import bo_helpers as h
from tests.integration.test_bo_m3_live_readiness import _live_valid_response
from tests.integration.test_bo_m3_standalone import _single_pass
from tests.unit.test_bo_comparison_failure_detail import _vertex_backend


SECRET = "fake-credential-must-not-be-recorded"
DOC = Path("data/test_inputs/hca_simulated_corporate_structure.json")


def _run(session, response=None, *, backend=None, document=None):
    return run_m3(session, case_id=h.CASE, evidence=h.evidence_snapshot(),
                  metrics=h.metrics(), m3_responses={"single_pass": response or _single_pass()},
                  live_backend=backend, corporate_structure_document=document)


class _RawFake(LLMBackend):
    model_id = "fake-private-diagnostic"

    def __init__(self, raw, error=None):
        self.raw, self.error, self.calls = raw, error, 0

    def generate(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return LLMRawResult(self.raw, self.model_id, {
            "backend": "fake", "input_tokens": 123, "output_tokens": 45,
            "api_key": SECRET, "headers": {"Authorization": SECRET},
        })


@pytest.mark.parametrize("raw,parsing,canonical", [
    ("  not-json\n", "invalid", "not_run"),
    (' {"parameters": "wrong-type", "hypotheses": []} \n', "valid", "rejected"),
    (json.dumps(_live_valid_response(), ensure_ascii=False, indent=2) + "\n\n", "valid", "valid"),
])
def test_exact_response_and_parse_stages_survive_close_reopen_and_offline_replay(
        tmp_path, raw, parsing, canonical):
    database = tmp_path / "private run.db"
    engine, factory = create_engine_and_session("sqlite:///" + str(database))
    init_db(engine)
    backend = _RawFake(raw)
    with factory() as session:
        result = _run(session, backend=backend)
        run_id = result.validation_stages["model_run_id"]
        session.commit()
    with factory() as session:
        bundle = load_recorded_m3_run(session, run_id)
        assert bundle["raw_response"] == raw
        assert bundle["recorded_request"]["raw_response"] == raw
        assert "M3 evidence and consistency requirements" in bundle["recorded_request"]["prompt_template"]
        assert SECRET not in json.dumps(bundle)
        assert bundle["provider_metadata"]["input_tokens"] == 123
        assert bundle["provider_metadata"]["output_tokens"] == 45
        schema = bundle["recorded_request"]["response_schema"]
        if canonical == "valid":
            jsonschema.validate(json.loads(bundle["raw_response"]), schema)
        assert verified_artifacts(session, h.CASE, 1)
    stages = result.validation_stages
    assert stages["model_execution"] == "response_received"
    assert stages["json_parsing"] == parsing
    assert stages["canonical_schema_validation"] == canonical
    assert backend.calls == 1
    assert database.stat().st_mode & 0o777 == 0o600
    engine.dispose()


@pytest.mark.parametrize("error", [ProviderCallError("Safe HTTP rejection."), ValueError(SECRET)])
def test_provider_failure_recorded_without_secrets_or_retries(db_session, error):
    backend = _RawFake("", error=error)
    result = _run(db_session, backend=backend)
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    assert bundle["raw_response"] == ""
    assert SECRET not in json.dumps(bundle)
    assert result.validation_stages["model_execution"] == "failed"
    assert result.validation_stages["canonical_schema_validation"] == "not_run"
    assert result.validation_stages["specialist_validation"] == "not_run"
    assert result.validation_stages["module_completion"] == "failed_model_execution"
    assert backend.calls == 1


def test_actual_vertex_wire_contract_and_hashes_retained_privately(db_session, tmp_path):
    backend, calls = _vertex_backend(raw=json.dumps(_live_valid_response()))
    result = _run(db_session, backend=backend, document=json.loads(DOC.read_text()))
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    meta = bundle["provider_metadata"]
    actual = meta["provider_request"]
    assert actual["config"] == calls[0]["config"]
    assert actual["contents"] == calls[0]["contents"]
    assert meta["request_schema_sha256"] == content_hash(actual["config"]["response_json_schema"])
    assert meta["system_instruction_sha256"] == content_hash(actual["config"]["system_instruction"])
    request = bundle["recorded_request"]
    assert actual["inputs_sha256"] == content_hash(request["inputs"])
    facts = request["inputs"]["corporate_structure"]
    assert (len(facts["nodes"]), len(facts["links"]), len(facts["guarantor_node_ids"])) == (14, 13, 5)
    assert facts["source"]["is_simulated"] is True
    paths = write_m3_demo(result, tmp_path, simplified_document=json.loads(DOC.read_text()))
    assert len(paths) == 6
    manifest = json.loads(paths["run_manifest"].read_text())
    assert "provider_request" not in json.dumps(manifest)
    scorecard = json.loads(paths["evaluation_scorecard"].read_text())
    assert scorecard["corporate_structure_accuracy"]["count_accuracy"] == {"numerator": 3, "denominator": 3}


def _cited_parameter_response():
    response = _single_pass()
    response["hypotheses"].append(h.hyp(
        "M3-H002", "The company is party to litigation and government investigations.",
        "fact", "negative", "supported", support=["party to litigation"],
        quotes=[("party to litigation", "We are party to litigation and government investigations.")],
        evidence_basis="company_specific_disclosure"))
    response["parameters"] = [{
        "parameter_id": "litigation_risk_assessment", "value_type": "categorical",
        "status": "ok", "value": "existing_proceedings_not_quantified",
        "evidence_ids": [{"$evidence": "party to litigation"}], "hypothesis_ids": ["M3-H002"],
    }]
    return response


def test_resolving_parameter_citations_and_supported_hypotheses_pass_existing_gate(db_session):
    result = _run(db_session, _cited_parameter_response())
    assert result.specialist_validated is True
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    assert json.loads(bundle["raw_response"])["parameters"][0]["evidence_ids"] == ["ev_reg"]


@pytest.mark.parametrize("status", ["ok", "provisional", "requires_review"])
def test_substantive_parameters_without_citations_still_fail(db_session, status):
    response = _cited_parameter_response()
    response["parameters"][0].update(status=status, evidence_ids=[])
    result = _run(db_session, response)
    assert result.specialist_validated is False
    assert "Specialist evidence validation rejected" in result.specialist_failure
    stages = result.validation_stages
    assert stages["canonical_schema_validation"] == "valid"
    assert stages["specialist_validation"] == "rejected"
    assert stages["module_completion"] == "failed_specialist_validation"
    raw = json.loads(load_recorded_m3_run(db_session, stages["model_run_id"])["raw_response"])
    assert raw["parameters"][0]["evidence_ids"] == []
    stage = db_session.get(StageArtifact, (h.CASE, 1, stages["validation_stage"]))
    assert "carries no evidence" in stage.payload_json
    assert db_session.get(AgenticAnalysisRun, result.analysis_run_id).status == "partially_failed"


def test_unknown_evidence_is_removed_and_noted_but_the_raw_answer_is_kept(db_session):
    response = _cited_parameter_response()
    response["parameters"][0]["evidence_ids"] = ["unknown-citation"]
    result = _run(db_session, response)
    # The unknown citation is removed; the parameter keeps the admitted evidence
    # of its own supported hypothesis, and the change is noted.
    assert result.specialist_validated
    notes = [a["note"] for a in result.specialist_validation.adjustments
             if a["hypothesis_id"] == "litigation_risk_assessment"]
    assert any("not admitted" in n for n in notes)
    assert any("evidence of its own hypotheses" in n for n in notes)
    raw = json.loads(load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])["raw_response"])
    assert raw["parameters"][0]["evidence_ids"] == ["unknown-citation"]


def test_honest_unavailable_parameters_pass_without_manufactured_citations(db_session):
    response = _cited_parameter_response()
    response["parameters"][0].update(status="unavailable", value=None, evidence_ids=[],
                                     notes="No evidence establishes the classification.")
    result = _run(db_session, response)
    assert result.specialist_validated is True
    assert result.validation_stages["module_completion"] == "partial_artifacts"


def test_retrieval_audit_contains_only_performed_searches_and_citable_packet_ids(db_session):
    result = _run(db_session)
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    inputs = bundle["recorded_request"]["inputs"]
    review = inputs["evidence_review"]
    assert review["retrieval_attempts"]
    assert any(a["purpose"] == "contradict" for a in review["retrieval_attempts"])
    for attempt in review["retrieval_attempts"]:
        assert attempt["status"] in {"performed", "no_results"}
        assert set(attempt["result_ids_in_packet"]) <= set(review["admitted_evidence_ids"])
    assert inputs["transaction_context"]["validated_borrower_entity_ids"] == []


@pytest.mark.parametrize("date_value,expected_issue", [
    (None, False), ("2024-02-03", False), ("2019", True), ("2019-02", True),
])
def test_unknown_complete_and_partial_management_dates_keep_existing_rules(
        db_session, date_value, expected_issue):
    response = _single_pass()
    observation = response["m3_assessment"]["management_assessments"][0]
    observation["effective_from"] = date_value
    result = _run(db_session, response)
    rules = {i["rule_id"] for i in result.validation_issues}
    assert ("m3.management_effective_date" in rules) is expected_issue
    assert result.governed_assessment["management_assessments"][0]["effective_from"] == date_value


def test_no_counterevidence_investigation_is_never_fabricated(db_session):
    response = _single_pass()
    response["m3_assessment"]["management_assessments"][0]["contradictory_evidence_investigated"] = False
    result = _run(db_session, response)
    observation = result.governed_assessment["management_assessments"][0]
    assert observation["contradictory_evidence_investigated"] is False
    assert observation["disposition"] == "unverified"
    assert observation["admissibility"] == "audit_only"


def test_partial_assessment_fails_canonical_without_inserting_required_section(db_session):
    response = _single_pass()
    del response["m3_assessment"]["management_assessments"]
    result = _run(db_session, response)
    assert result.governed_assessment is None
    assert result.validation_stages["canonical_schema_validation"] == "rejected"
    raw = json.loads(load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])["raw_response"])
    assert "management_assessments" not in raw["m3_assessment"]


def test_schema_valid_but_typed_contract_invalid_response_remains_replayable(db_session):
    response = _single_pass()
    response["m3_assessment"]["cash_flow_locations"] = [{
        "cash_flow_id": "test-cash", "entity_id": "test-entity", "amount": None,
        "period": None, "currency": None, "units": None, "accounting_basis": None,
        "measure_type": "unclassified", "reporting_scope": "consolidated",
        "cash_flow_roles": [], "evidence_ids": [], "provenance_status": "unverified",
        "missing_information": ["Cash-flow role is unknown."],
    }]
    result = _run(db_session, response)
    assert result.validation_stages["canonical_schema_validation"] == "valid"
    assert result.validation_stages["assessment_governance"] == "rejected"
    assert result.validation_stages["module_completion"] == "failed_assessment_governance"
    assert result.governed_assessment is None
    row = db_session.get(AgenticAnalysisRun, result.analysis_run_id)
    assert row.status == "partially_failed"
    assert "assessment contract rejected" in row.failure_state
    db_session.commit()
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    assert json.loads(bundle["raw_response"])["m3_assessment"]["cash_flow_locations"][0]["cash_flow_roles"] == []


def test_recording_rejects_unignored_database_suffix_before_provider_call(tmp_path):
    engine, factory = create_engine_and_session("sqlite:///" + str(tmp_path / "not-ignored.txt"))
    init_db(engine)
    backend = _RawFake(json.dumps(_live_valid_response()))
    with factory() as session:
        with pytest.raises(ValueError, match="git-ignored .db or .sqlite3"):
            _run(session, backend=backend)
        assert backend.calls == 0
    engine.dispose()


def test_private_sqlite_files_and_companions_are_git_ignored():
    names = ["phase2-private" + extension + suffix
             for extension in (".db", ".sqlite3") for suffix in ("", "-journal", "-wal", "-shm")]
    result = subprocess.run(["git", "check-ignore", "--stdin"],
                            input="\n".join(names) + "\n", text=True, capture_output=True,
                            cwd=Path(__file__).resolve().parents[2], check=True)
    assert set(result.stdout.splitlines()) == set(names)


def test_legacy_complete_with_unsupported_observation_does_not_claim_analytical_success(db_session):
    result = _run(db_session, backend=_RawFake(json.dumps(_live_valid_response())),
                  document=json.loads(DOC.read_text()))
    assert result.completion_status == "complete"  # backward-compatible availability field
    assert result.validation_stages["analytical_status"] == "no_evidence_supported_findings"
    assert result.validation_stages["module_completion"] == "completed_with_evidence_gaps"


def test_replay_rejects_response_tampering(db_session):
    result = _run(db_session)
    run_id = result.validation_stages["model_run_id"]
    row = db_session.get(ModelRun, run_id)
    row.raw_response = "tampered response"
    db_session.flush()
    with pytest.raises(ArtifactIntegrityError, match="differs from its immutable stage"):
        load_recorded_m3_run(db_session, run_id)


def test_replay_rejects_metadata_tampering(db_session):
    result = _run(db_session)
    run_id = result.validation_stages["model_run_id"]
    row = db_session.get(ModelRun, run_id)
    row.model_config_json = {**row.model_config_json, "input_tokens": 999999}
    db_session.flush()
    with pytest.raises(ArtifactIntegrityError, match="metadata differs"):
        load_recorded_m3_run(db_session, run_id)


def test_reproduce_command_quotes_paths_and_omits_credentials(db_session, tmp_path):
    result = _run(db_session)
    argv = ["python", "-m", "app.cli", "--database", "data/private run.db", "--output",
            "output/with spaces", "run-m3", h.CASE, "--package", "source with spaces.json", "--live"]
    paths = write_m3_demo(result, tmp_path, reproduce_argv=argv, reproduce_env={
        "LLM_PROVIDER": "vertex", "GOOGLE_CLOUD_PROJECT": "test-project", "API_KEY": SECRET})
    manifest = json.loads(paths["run_manifest"].read_text())
    assert manifest["reproduction"]["ready"] is True
    command = shlex.split(manifest["reproduce_command"])
    assert command[command.index("python"):] == argv
    assert SECRET not in manifest["reproduce_command"]


def test_legacy_reproduction_fragment_is_explicitly_not_ready(db_session, tmp_path):
    paths = write_m3_demo(_run(db_session), tmp_path)
    manifest = json.loads(paths["run_manifest"].read_text())
    assert manifest["reproduction"]["ready"] is False
    assert "fragment" in manifest["reproduction"]["note"]


@pytest.mark.parametrize("valid", [True, False])
def test_relationship_and_transfer_endpoints_require_existing_legal_entities(db_session, valid):
    response = _single_pass()
    assessment = response["m3_assessment"]
    assessment["legal_entities"] = [{
        "entity_id": eid, "legal_name": name, "jurisdiction": None,
        "classification": classification, "identity_type": "legal_entity",
        "verification_status": "unverified", "evidence_ids": [],
        "materiality_reason": "Fictional test identity; actual legal identity is unresolved.",
    } for eid, name, classification in (
        ("test-parent", "Fictional parent", "ultimate_parent"),
        ("test-child", "Fictional child", "operating_subsidiary"))]
    endpoint = "test-parent" if valid else "unknown-endpoint"
    assessment["ownership_relationships"] = [{
        "relationship_id": "test-ownership", "parent_entity_id": endpoint,
        "subsidiary_entity_id": "test-child", "ownership_percentage": None,
        "control_basis": None, "effective_from": None, "effective_to": None,
        "evidence_ids": [], "confirmation_status": "unverified", "uncertainty": "Not established.",
    }]
    assessment["cash_transfer_relationships"] = [{
        "transfer_id": "test-transfer", "source_entity_id": endpoint,
        "destination_entity_id": "test-child", "mechanism": "other",
        "transfer_evidence_state": "unknown", "known_restrictions_or_dependencies": [],
        "evidence_ids": [], "confirmation_status": "unverified", "uncertainty": "Not established.",
    }]
    result = _run(db_session, response)
    reasons = {item["reason"] for item in result.rejected_items}
    assert ("relationship_references_unknown_entity" in reasons) is not valid
    assert ("unknown_entity" in reasons) is not valid
    assert {e["entity_id"] for e in result.governed_assessment["legal_entities"]} == {
        "test-parent", "test-child"}


def test_supported_synthetic_structure_and_management_survive_existing_verification(db_session):
    from tests.integration.test_bo_m3_structural_credit import _run_fictional

    result = _run_fictional(db_session, "fictional_positive")
    supported = result.validation_stages["evidence_supported_findings"]
    assert supported["structural_credit"] >= 1
    assert supported["management"] >= 1
    assert result.validation_stages["analytical_status"] == "evidence_supported_findings_for_review"
    finding = result.governed_assessment["structural_credit_findings"][0]
    assert finding["referenced_node_ids"]
    assert finding["analytical_dimension"] == "guarantor_coverage"
    assert any("simulat" in q for q in finding["qualifications"])


def test_cli_records_runnable_source_and_backend_flags_and_can_replay_offline(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    from app import cli

    # Only source preparation is replaced; real standalone execution, storage,
    # validation, writer and CLI argument parsing run against an offline script.
    monkeypatch.setattr(cli.SourcePackage, "load", lambda path: object())
    monkeypatch.setattr(cli.CreditMemoPipeline, "prepare_case", lambda *a, **k: SimpleNamespace(
        evidence_snapshot=h.evidence_snapshot(), metrics=h.metrics()))
    responses = tmp_path / "script with spaces.json"
    responses.write_text(json.dumps({"single_pass": _single_pass()}))
    database = tmp_path / "original run.db"
    out = tmp_path / "output with spaces"
    argv = ["--database", str(database), "--output", str(out), "run-m3", h.CASE,
            "--package", "source with spaces.json", "--responses", str(responses)]
    cli.main(argv)
    summary = json.loads(capsys.readouterr().out)
    assert "validation_stages" in summary
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["reproduction"]["ready"] is True
    command = shlex.split(manifest["reproduce_command"])
    assert "--package" in command and "--responses" in command and "--database" in command
    assert "--live" not in command
    replay_args = command[command.index("app.cli") + 1:]
    replay_database = tmp_path / "offline replay.db"
    replay_output = tmp_path / "offline replay output"
    replay_args[replay_args.index("--database") + 1] = str(replay_database)
    replay_args[replay_args.index("--output") + 1] = str(replay_output)
    cli.main(replay_args)
    capsys.readouterr()
    for db in (database, replay_database):
        engine, factory = create_engine_and_session("sqlite:///" + str(db))
        with factory() as session:
            row = session.scalars(select(ModelRun)).one()
            bundle = load_recorded_m3_run(session, row.run_id)
            assert bundle["canonical_validation_outcome"] == "valid"
        engine.dispose()
