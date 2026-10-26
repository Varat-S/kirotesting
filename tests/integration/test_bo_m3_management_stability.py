"""Actual FY2025 source/packet proof plus new-mode standalone compatibility."""

import hashlib
import html
import json
import re
from pathlib import Path

import jsonschema
import pytest

from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA, bo_v3_prompt_catalogue
from app.schemas.bo_m3 import M3CorporateAssessment
from app.services.bo.comparison import prepare_specialist
from app.services.bo.m3_diagnostics import load_recorded_m3_run
from app.services.bo.m3_standalone import run_m3
from app.services.bo.m3_management import stability_view
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
from tests.integration.test_bo_m3_phase3_replay import recorded


EXAMPLES = Path("examples/bo_m3/management_stability")


def load(name):
    return json.loads((EXAMPLES / name).read_text())


def test_new_fixture_and_legacy_responses_keep_their_distinct_versioned_contracts(recorded):
    new = load("hca_fy2025_fixture.json")["response"]
    jsonschema.validate(new, BO_M3_JSON_SCHEMA)
    M3CorporateAssessment.model_validate(new["m3_assessment"])
    legacy = recorded["raw"]
    jsonschema.validate(legacy, BO_M3_JSON_SCHEMA)
    assert M3CorporateAssessment.model_validate(legacy["m3_assessment"]).management_stability is None
    missing = {**new["m3_assessment"]}
    del missing["management_stability"]
    with pytest.raises(ValueError, match="1.2 requires"):
        M3CorporateAssessment.model_validate(missing)


def test_hca_fy2025_filing_hash_and_actual_biographies_retrieved_with_source_dates(recorded, db_session):
    fixture = load("hca_fy2025_fixture.json")
    content = Path(fixture["filing_path"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == fixture["filing_sha256"]
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", content.decode()))).strip()
    prep = prepare_specialist(db_session, case_id=recorded["evidence"].case_id,
        evidence=recorded["evidence"], metrics={}, spec=V3_SPEC_BY_CODE["M3"])
    assert len(prep.passages) <= 16
    assert sum(len(p["text"]) for p in prep.passages) <= 36_000
    assert any("Opinion on Internal Control Over Financial Reporting" in p["text"] for p in prep.passages)
    for profile in fixture["response"]["m3_assessment"]["management_stability"]["executives"]:
        for fact in profile["facts"]:
            assert fact["source"]["evidence_id"] in prep.packet.evidence_ids
            assert re.sub(r"\s+", " ", fact["source"]["quote"]) in text
            metadata = prep.management_source_metadata[fact["source"]["evidence_id"]]
            assert metadata["filing_date"] == "2026-02-10"
            assert metadata["report_date"] == "2025-12-31"
            assert metadata["sec_accession"] == "0001193125-26-044769"
    assert any(a["query_id"] == "m3.executive_experience" for a in prep.retrieval_attempts)
    assert not prep.m3_selection_summary["queries_without_selected_results"]


def test_realistic_hca_authored_example_runs_offline_and_retains_qualified_findings(recorded, db_session):
    fixture = load("hca_fy2025_fixture.json")
    evidence = recorded["evidence"]
    result = run_m3(db_session, case_id=evidence.case_id, evidence=evidence, metrics={},
        m3_responses={"single_pass": fixture["response"]},
        corporate_structure_document=json.loads(Path("data/test_inputs/hca_simulated_corporate_structure.json").read_text()))
    assert result.model_called is False
    assert result.specialist_validated is True
    assert result.validation_stages["canonical_schema_validation"] == "valid"
    assert result.validation_stages["evidence_supported_findings"]["management"] == 3
    profiles = {p["executive_id"]: p for p in result.governed_assessment["management_stability"]["executives"]}
    assert len(profiles) == 7
    assert all(f["verification_status"] == "verified" for p in profiles.values() for f in p["facts"])
    assert profiles["marks"]["transition_type"] == "internal_succession"
    assert profiles["foster"]["recent_appointment"] == "no"  # 2023-01-01 predates the 2023-02-01 window
    assert profiles["rossitto"]["recent_appointment"] == "yes"
    view = result.m3_analysis["senior_management_assessment"]
    assert view["consolidated_management_stability_conclusion"]["credit_direction"] == "positive"
    assert set(view["overall_management_stability_assessment"]["established_recent_appointments"]) == {"marks", "mcalevey", "rossitto"}
    assert view["overall_management_stability_assessment"]["history_coverage"] == "partial"
    assert all(p["value"] is None for p in fixture["response"]["parameters"])
    assert all(o["dimension"] == "leadership_continuity" for o in result.governed_assessment["management_assessments"])
    assert "Demonstrated management execution" not in json.dumps(result.m3_output["section_c_conclusion"])
    assert result.m3_output["section_a_corporate_structure"]["diagram"]["node_count"] == 14
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    assert json.loads(bundle["raw_response"])["m3_assessment"] == fixture["response"]["m3_assessment"]
    assert bundle["recorded_request"]["inputs"]["management_review_policy"]["recent_change_window_years"] == 3
    assert view["key_executive_details"][0]["source_references"][0]["filing_date"] == "2026-02-10"


def test_fictional_negative_fixture_is_admitted_with_both_external_expertise_and_transition_uncertainty():
    fixture = load("fictional_negative_fixture.json")
    jsonschema.validate(fixture["response"], BO_M3_JSON_SCHEMA)
    governed = validate_m3_assessment(fixture["response"]["m3_assessment"],
        allowed_evidence_ids=set(fixture["evidence"]), evidence_texts=fixture["evidence"],
        management_company_aliases=tuple(fixture["company_aliases"]))
    data = governed.model_dump(mode="json")
    view = stability_view(data["management_stability"], data["management_assessments"])
    assert len(view["credit_findings"]) == 1
    assert view["consolidated_management_stability_conclusion"]["credit_direction"] == "negative"
    assert all(p["external_roles"] and p["prior_company_experience"] == "explicitly_absent" for p in view["key_executive_details"])
    # The existing typed SO adapter consumes new qualified observations without
    # a new score, a legacy-quality mapping, or structural code changes.
    from app.services.structural_overview.adapter import _corporate_structure
    mapped, *_ = _corporate_structure({"m3_assessment_status": "validated", "m3_assessment": data})
    assert mapped.management_assessments[0].dimension == "transition_risk"
    assert mapped.management_assessments[0].credit_direction.value == "negative"


def test_new_prompt_retires_broad_quality_objectives_and_keeps_independent_governance():
    prompt = next(p for p in bo_v3_prompt_catalogue() if p["name"] == "m3_corporate_management")["template"]
    assert "Management competence: examine" not in prompt
    assert "Credibility and integrity: compare" not in prompt
    assert "management_quality_assessment and key_person_dependency_assessment" in prompt
    assert "Keep independent governance screening" in prompt
    assert "One external hire does not automatically" in prompt


def test_new_continuity_finding_cannot_silently_default_a_missing_direction_to_neutral():
    fixture = load("fictional_negative_fixture.json")
    del fixture["response"]["m3_assessment"]["management_assessments"][0]["credit_direction"]
    with pytest.raises(ValueError, match="explicit credit_direction"):
        M3CorporateAssessment.model_validate(fixture["response"]["m3_assessment"])
