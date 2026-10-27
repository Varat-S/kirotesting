"""Saved-output viewing must never execute a model or publish private stores."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.m3_preview import create_m3_preview_app


def saved_case(tmp_path):
    case = "OFFLINE_PREVIEW_TEST"
    source = json.loads(Path("data/test_inputs/hca_simulated_corporate_structure.json").read_text())
    from app.services.bo.corporate_structure import CaseCorporateStructureSource, build_corporate_structure_artifact
    from app.services.bo.m3_artifacts import build_m3_structure_sidecar
    structure = build_corporate_structure_artifact(CaseCorporateStructureSource(
        case_id=case, registered_case_id=case, payload=source), expected_case_id=case)
    sidecar = build_m3_structure_sidecar(structure)
    files = {
        "run_manifest.json": {"case_id": case, "analysis_run_id": "test-run"},
        "m3_corporate_structure.json": source,
        "m3_structure_provenance.json": sidecar,
        "validation_report.json": {"case_id": case, "validation_issues": [], "rejected_items": [],
            "validation_stages": {"model_execution": "response_received", "json_parsing": "valid",
                "canonical_schema_validation": "valid", "assessment_governance": "qualified"}},
        "m3_analysis.json": {"corporate_structure_reference": {"artifact_id": sidecar["artifact_id"]},
            "structural_observations": [], "structural_limitations": [],
            "ai_structural_credit_findings": [], "admitted_structural_credit_finding_ids": [],
            "senior_management_assessment": {}, "information_gaps": ["Review source coverage."]},
        "m3_browser_review.json": {"case_id": case, "assessment_version": "bo-m3-corporate-assessment-1.2",
            "proposals": [{"id": "rejected-claim", "kind": "management", "status": "rejected",
                "evidence": "<script>secretScript()</script>", "inference": "Unsupported inference",
                "credit_implication": "Unsupported credit claim", "proposed_disposition": "supported",
                "validator_disposition": "rejected", "credit_direction": "positive", "citations": [],
                "validation_reasons": ["Evidence does not support conclusion."], "qualifications": []}]},
    }
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data))
    return case


def test_read_only_refresh_chart_and_rejected_status(tmp_path, monkeypatch):
    case = saved_case(tmp_path)
    from app.services.llm.providers_vertex import VertexProviderBackend
    monkeypatch.setattr(VertexProviderBackend, "generate", lambda *_: (_ for _ in ()).throw(
        AssertionError("Preview must never call a provider")))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with TestClient(create_m3_preview_app(tmp_path)) as client:
        for _ in range(2):
            response = client.get(f"/m3/{case}")
            assert response.status_code == 200
            html = response.text
            assert case in html and "Simulated structure" in html
            assert "rejected-claim" in html and "Validator disposition: rejected" in html
            assert "&lt;script&gt;" in html and "<script>secretScript" not in html
            assert "No management-stability credit finding was admitted" in html
            assert "form-action 'none'" in response.headers["content-security-policy"]
        svg = client.get(f"/m3/{case}/structure.svg")
        assert svg.status_code == 200 and "<svg" in svg.text
        assert "HCA" in svg.text and "data-renderer-version" in svg.text
        assert client.post(f"/m3/{case}").status_code == 405
        for url in [f"/m3/{case}/artifacts/private.db", "/cases/anything/model-runs", "/docs"]:
            assert client.get(url).status_code == 404
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_provider_failure_still_shows_honest_structure(tmp_path):
    case = saved_case(tmp_path)
    path = tmp_path / "validation_report.json"
    validation = json.loads(path.read_text())
    validation["validation_stages"].update(model_execution="provider_rejected",
        module_completion="failed_model_execution", canonical_schema_validation="not_run")
    validation["specialist_failure"] = "Vertex rejected the request."
    path.write_text(json.dumps(validation))
    (tmp_path / "m3_browser_review.json").unlink()
    with TestClient(create_m3_preview_app(tmp_path)) as client:
        page = client.get(f"/m3/{case}")
        assert page.status_code == 200
        assert "Analysis did not complete successfully" in page.text
        assert "No original AI proposals" in page.text
        assert "Vertex rejected the request" in page.text
        assert client.get(f"/m3/{case}/structure.svg").status_code == 200


def test_cannot_mix_cases_or_serve_symlinked_files(tmp_path):
    case = saved_case(tmp_path)
    with TestClient(create_m3_preview_app(tmp_path)) as client:
        assert client.get("/m3/another-case").status_code == 404
        scorecard = tmp_path / "evaluation_scorecard.json"
        scorecard.symlink_to(tmp_path / "run_manifest.json")
        assert client.get(f"/m3/{case}/artifacts/evaluation_scorecard.json").status_code == 404
        review = tmp_path / "m3_browser_review.json"
        review.write_text(json.dumps({"case_id": "wrong-case"}))
        assert client.get(f"/m3/{case}").status_code == 409


def test_verified_executive_and_qualified_finding_are_readable(tmp_path):
    case = saved_case(tmp_path)
    path = tmp_path / "m3_analysis.json"
    analysis = json.loads(path.read_text())
    source = {"evidence_id": "test-source", "quote": "Officer A has served since May 2024.",
              "filename": "test-filing.htm", "section": "Executive officers"}
    analysis["senior_management_assessment"] = {
        "overall_management_stability_assessment": {"as_of_date": "2026-02-01", "recent_change_window_years": 3},
        "consolidated_management_stability_conclusion": {"classification": "insufficient_information",
            "summary": "Specific experience is supported; overall direction remains unestablished.", "limitations": []},
        "key_executive_details": [{"name": "Officer A", "current_role": "CFO", "profile_status": "verified",
            "transition_type": "internal_succession", "appointment_date": "2024-05",
            "current_role_tenure": {"min_completed_months": 20, "max_completed_months": 21, "as_of_date": "2026-02-01"},
            "prior_company_experience": "established", "recent_appointment": "yes", "previous_company_roles": ["Finance director"],
            "external_roles": [], "limitations": [], "source_references": [source]}],
        "credit_findings": [{"finding_id": "qualified-finding", "disposition": "qualified", "credit_direction": "neutral",
            "evidence": [{"kind": "appointment", "value": "CFO", "date": "2024-05", "source": source}],
            "qualified_inference": "Internal service may support familiarity.", "credit_implication": "Transition risk remains uncertain.",
            "uncertainties": ["No complete change log."], "mitigating_considerations": []}],
    }
    path.write_text(json.dumps(analysis))
    with TestClient(create_m3_preview_app(tmp_path)) as client:
        response = client.get(f"/m3/{case}")
        assert response.status_code == 200
        for phrase in ["Officer A", "20–21 completed months", "qualified-finding", source["quote"],
                       "Previous internal positions", "Transition risk remains uncertain."]:
            assert phrase in response.text
