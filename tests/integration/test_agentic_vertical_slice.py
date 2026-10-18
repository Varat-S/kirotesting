"""Actual-specialist BO + synthetic FO + governed SO vertical-slice checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.core.hashing import content_hash
from app.services.structural_overview.agentic_vertical_slice import (
    AgenticVerticalSliceError,
    build_agentic_vertical_slice,
    validate_agentic_vertical_slice,
    write_agentic_vertical_slice_artifacts,
)
from tests.integration.test_structural_overview_thin_slice import _governed_m3


FO_FIXTURE = Path("examples/credit_memo/synthetic_fo_v1.json")
R5_RUN = Path(
    "output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/"
    "ar_fb0bba6709e94a14/specialists_run.json"
)


def _hypothesis() -> dict:
    return {
        "hypothesis_id": "M3-H001",
        "analysis_run_id": "ar_test_vertical",
        "agent_id": "m3_corporate_management",
        "agent_run_id": "agent_run_m3",
        "section": "M3",
        "claim": "The reporting parent is an admitted legal entity.",
        "claim_type": "fact",
        "direction": "neutral",
        "supporting_evidence_ids": ["EV-M3-STRUCT"],
        "evidence_quotes": [{
            "evidence_id": "EV-M3-STRUCT",
            "quote": "Test Parent, Inc. is the reporting parent.",
        }],
        "disposition": "supported",
        "admissibility": "established",
        "confidence": "high",
        "materiality": "high",
        "display_priority": "display",
        "evidence_basis": "company_specific_disclosure",
        "validator_id": "validator_1",
        "validator_version": "test-1",
    }


def _specialists_run() -> dict:
    proposal = _governed_m3(borrower_status="unavailable")
    proposal.pop("validator_id")
    proposal.pop("validator_version")
    proposal.pop("validation_issues")
    proposal.pop("rejected_items")
    proposal.pop("model_proposal")
    return {
        "document": "business_overview_specialists_run",
        "version": "bo-specialists-run-1.0",
        "meta": {
            "analysis_run_id": "ar_test_vertical",
            "case_id": "TEST_VERTICAL",
            "borrower": "Test Parent, Inc.",
            "as_of_date": "2025-12-31",
            "evidence_cutoff": "2026-03-31 00:00:00+00:00",
            "execution_mode": "live_model",
            "models": ["test-live-model"],
            "documents": [{
                "filename": "test-10k.htm", "role": "primary_inline_xbrl",
                "form": "10-K", "report_date": "2025-12-31",
                "accession": "TEST-ACCESSION", "sha256": "a" * 64,
            }],
        },
        "metrics": {
            "agents_validated": 1, "model_calls": 1,
            "hypotheses_recorded": 1, "hypotheses_usable": 1,
            "by_admissibility": {"established": 1},
            "coverage_addressed": 0, "coverage_checks": 0,
            "citation_verification_failures": 0, "citations_recovered": 0,
        },
        "usage": {"total_tokens": 100, "input_tokens": 80, "output_tokens": 20},
        "stages": [{"stage": "primary_specialists", "executed": ["m3_corporate_management"]}],
        "agents": [{
            "agent_id": "m3_corporate_management", "code": "M3",
            "title": "Corporate Structure & Senior Management",
            "status": "validated", "detail": None,
            "agent_run_ids": ["agent_run_m3"],
            "owned_parameters": [], "parameters": [],
            "parameter_adjustments": [], "hypotheses": [_hypothesis()],
            "coverage": [], "validation_issues": [],
            "validator_adjustments": [], "retrieval_attempts": [],
            "calls": [{
                "prompt_id": "m3_test", "prompt_hash": "b" * 64,
                "effective_instructions": "Test M3 instructions.",
                "model_id": "test-live-model", "usage": {"total_tokens": 100},
                "latency_ms": 12, "error": None,
                "inputs": {
                    "evidence": [{
                        "evidence_id": "EV-M3-STRUCT",
                        "text": "Test Parent, Inc. is the reporting parent.",
                    }],
                    "indicators": [], "checklist": [], "retrieval": {},
                },
                "output": {"m3_assessment": proposal, "hypotheses": []},
            }],
        }],
        "fo_handoffs": [], "shared_rules": "Test shared rules.",
        "passages": {"EV-M3-STRUCT": {"section": "Item 1", "location": 1}},
    }


def _fo() -> dict:
    return json.loads(FO_FIXTURE.read_text(encoding="utf-8"))


def test_specialist_slice_is_fail_closed_and_reproducible(tmp_path):
    source = _specialists_run()
    report = build_agentic_vertical_slice(source, _fo())
    assert report == build_agentic_vertical_slice(source, _fo())
    assert report["business_overview"]["source_type"] == \
        "live_specialists_only_no_accepted_handoff"
    assert report["financial_overview"]["packet"]["provenance"] == "synthetic_fixture"
    assert report["financial_overview"]["fixture_binding"]["rebound"] is True
    assert report["case"]["transaction_borrower_status"] == "unavailable"
    assert report["case"]["transaction_borrower_entity_id"] is None
    assert report["completion"]["bo_topic_conclusion"] == "not_executed"
    assert report["completion"]["final_credit_synthesis"] == "blocked"
    assert report["final_credit_score"] is report["facility_approval"] is None
    assert report["structural_overview"]["replay"]["model_called"] is False

    json_path, pdf_path = write_agentic_vertical_slice_artifacts(report, tmp_path)
    assert validate_agentic_vertical_slice(
        json.loads(json_path.read_text(encoding="utf-8"))) == report
    with pdfium.PdfDocument(pdf_path) as pdf:
        texts = [page.get_textpage().get_text_range() for page in pdf]
    full = "\n".join(texts)
    assert all("INCOMPLETE" in text for text in texts)
    for expected in (
        "BO / FO / SO Thin Vertical Slice", "Financial Overview - simulated fixture",
        "Structural Overview - governed M3", "BO agent 1: M3",
        "Sources, lineage, and integrity",
    ):
        assert expected in full


def test_frozen_slice_rejects_tampering_and_final_outputs():
    report = build_agentic_vertical_slice(_specialists_run(), _fo())
    tampered = copy.deepcopy(report)
    tampered["financial_overview"]["calculations"][0]["value"] = 999
    with pytest.raises(AgenticVerticalSliceError, match="content hash"):
        validate_agentic_vertical_slice(tampered)
    tampered["report_content_hash"] = content_hash({
        key: value for key, value in tampered.items() if key != "report_content_hash"
    })
    with pytest.raises(AgenticVerticalSliceError, match="calculations"):
        validate_agentic_vertical_slice(tampered)

    final = copy.deepcopy(report)
    final["final_credit_score"] = 3
    final["report_content_hash"] = content_hash({
        key: value for key, value in final.items() if key != "report_content_hash"
    })
    with pytest.raises(AgenticVerticalSliceError, match="final outputs"):
        validate_agentic_vertical_slice(final)


def test_non_synthetic_fo_cannot_be_rebound():
    fo = _fo()
    fo["provenance"] = "validated_fo"
    for period in fo["periods"]:
        for fact in period["facts"].values():
            fact.update(provenance="validated_fo", evidence_ids=["test-evidence"])
    for finding in fo["findings"]:
        finding["provenance"] = "validated_fo"
    with pytest.raises(AgenticVerticalSliceError, match="cannot be rebound"):
        build_agentic_vertical_slice(_specialists_run(), fo)


@pytest.mark.skipif(not R5_RUN.exists(), reason="local R5 live specialist artifact is absent")
def test_actual_r5_live_agents_to_simulated_fo_and_governed_so_pdf(tmp_path):
    source = json.loads(R5_RUN.read_text(encoding="utf-8"))
    report = build_agentic_vertical_slice(source, _fo())
    assert report["business_overview"]["meta"]["execution_mode"] == "live_model"
    assert report["business_overview"]["metrics"]["agents_validated"] == 9
    assert report["business_overview"]["metrics"]["hypotheses_recorded"] == 28
    assert len(report["business_overview"]["agents"]) == 9
    assert sum(len(agent["calls"]) for agent in report["business_overview"]["agents"]) == 13
    assert report["business_overview"]["usage"]["total_tokens"] == 256359
    assert report["case"]["transaction_borrower_status"] == "unavailable"
    structure = report["structural_overview"]["governed_assessment"]
    assert {item["entity_id"] for item in structure["legal_entities"]} == {
        "HCA_HEALTHCARE_INC"
    }
    assert structure["ownership_relationships"] == []
    assert structure["cash_flow_locations"] == []
    assert len(structure["validation_issues"]) == 11
    assert len(structure["rejected_items"]) == 4
    assert report["cross_domain_findings"]

    _, pdf_path = write_agentic_vertical_slice_artifacts(report, tmp_path)
    with pdfium.PdfDocument(pdf_path) as pdf:
        texts = [page.get_textpage().get_text_range() for page in pdf]
    full = "\n".join(texts)
    assert len(texts) >= 20
    assert all("INCOMPLETE" in text for text in texts)
    for code in ("M1S1", "M1S2", "M1S3", "M1S4", "M1S5", "M1A6", "M2", "M3", "M4"):
        assert f": {code} -" in full
    assert "SIMULATED FINANCIAL INFORMATION" in full
    assert "Proposed borrower is unavailable" in full
