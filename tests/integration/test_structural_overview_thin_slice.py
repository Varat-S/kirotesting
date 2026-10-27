"""Targeted BO-to-Structural-Overview integration tests; no live model calls."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.bo.reporting import handoff_content_hash
from app.services.structural_overview import (
    StructuralOverviewInputError,
    adapt_business_overview,
    assessment_content_hash,
    render_assessment_html,
    write_artifacts,
)


REAL_HCA_HANDOFF = Path(
    "output/bo_hca_live/bo/HCA_2025_BO_LIVE/"
    "ar_36d4a0589d224737/handoff_v1_2.json"
)


def _finding(module: str, index: int, *, qualified: bool = False) -> dict:
    hypothesis_id = f"{module}-H{index:03d}"
    return {
        "hypothesis_id": hypothesis_id,
        "section": module,
        "claim": f"Preserved {module} source conclusion {index}.",
        "claim_type": "inference" if qualified else "fact",
        "direction": "negative" if qualified else "positive",
        "supporting_evidence_ids": [f"EV-{module}-{index}"],
        "contradicting_evidence_ids": [],
        "disposition": "qualified" if qualified else "supported",
        "admissibility": "qualified_narrative" if qualified else "established",
        "confidence": "medium" if qualified else "high",
        "materiality": "high",
        "missing_information": ["Independent confirmation"] if qualified else [],
        "validation_notes": ["Preserve qualification"] if qualified else [],
    }


def _handoff() -> dict:
    findings = {
        "M1": _finding("M1", 1, qualified=True),
        "M2": _finding("M2", 1),
        "M4": _finding("M4", 1),
    }
    payload = {
        "handoff_id": "bo_test001",
        "schema_version": "bo-handoff-1.2",
        "analysis_run_id": "ar_test001",
        "case_id": "TEST_CASE",
        "borrower_name": "Test Borrower, Inc.",
        "borrower_entity_id": "TEST",
        "as_of_date": "2025-12-31",
        "evidence_cutoff_timestamp": "2026-03-01T00:00:00Z",
        "evidence_snapshot_version": 3,
        "business_topic_conclusion_id": "tc_test001",
        "conclusion_status": "accepted_with_qualification",
        "business_risk_score_id": "score_test001",
        "business_risk_score_status": "provisional",
        "business_risk_band": 3,
        "accepted_finding_ids": [x["hypothesis_id"] for x in findings.values()],
        "material_risk_ids": ["R-001"],
        "business_strengths": [{
            "claim_id": "S-001", "title": "Strength", "text": "Preserved strength.",
            "section": "M4", "materiality": "high", "hypothesis_ids": ["M4-H001"],
            "evidence_ids": ["EV-M4-1"],
        }],
        "operational_vulnerabilities": [],
        "validated_mitigants": [],
        "unproven_mitigants": [],
        "unresolved_challenges": [{
            "challenge_id": "ar_test001:r0:CH-001", "issue_type": "qualification",
            "reason": "The claim requires qualification.", "severity": "medium",
            "target": "M1-H001",
        }],
        "open_questions": ["What is normalized cash flow?"],
        "module_conclusions": {
            module: {"module": module, "status": "complete", "findings": [finding]}
            for module, finding in findings.items()
        },
        "compound_business_risks": [{
            "claim_id": "R-001", "title": "Interaction risk",
            "text": "The preserved M1 interaction compounds risk.", "section": "M1",
            "materiality": "high", "hypothesis_ids": ["M1-H001"],
            "evidence_ids": ["EV-M1-1"],
        }],
        "fo_handoffs": [],
        "score_limitations": ["BO score is provisional."],
        "evidence_limitations": ["Independent confirmation is unavailable."],
        "escalations": [],
        "canonical_references": {"analysis_run_id": "ar_test001", "audit_stage": "bo_audit:ar_test001"},
        "human_review_status": "pending",
        "reviewer": None,
        "reviewed_at": None,
        "execution_mode": "offline_simulation",
        "agent_registry_version": "test-v1",
        "agent_registry_hash": "registryhash",
        "prompt_hashes": {},
        "scoring_config_version": 1,
        "scoring_config_hash": "scoringhash",
        "config_versions": {},
        "content_hash": None,
    }
    from app.services.bo.m3_rebuild.adapters import unavailable_block
    payload["module_conclusions"]["M3"] = unavailable_block(payload["case_id"])
    payload["content_hash"] = handoff_content_hash(payload)
    return payload


def _rehash(payload: dict) -> dict:
    payload["content_hash"] = handoff_content_hash(payload)
    return payload




def test_qualified_handoff_stays_partial_and_does_not_manufacture_credit_outputs(tmp_path):
    assessment = adapt_business_overview(_handoff(), expected_case_id="TEST_CASE")
    finding = assessment.business_assessment.business_model_and_revenue_drivers[0]
    assert finding.claim == "Preserved M1 source conclusion 1."
    assert finding.disposition == "qualified"
    assert finding.admissibility == "qualified_narrative"
    assert finding.provenance.source_hypothesis_id == "M1-H001"
    assert finding.supporting_evidence_ids == ["EV-M1-1"]
    assert assessment.status == "partial"
    assert assessment.governance.provisional_business_risk_band == 3
    assert assessment.governance.business_risk_score_status == "provisional"
    assert assessment.final_credit_score is None
    assert assessment.facility_approval is None
    assert assessment.downstream_dependencies.bo_to_fo_handoffs == []
    assert assessment.downstream_dependencies.sections["financial_overview"].status == "unavailable"
    assert assessment.borrower.reporting_entity_name == "Test Borrower, Inc."
    assert assessment.borrower.identification_status == "unavailable"
    assert assessment.borrower.entity_id is None
    assert assessment.corporate_structure.source_status == "unavailable"
    assert assessment.downstream_dependencies.sections["structural_overview"].status == "unavailable"
    assert assessment.downstream_dependencies.sections["final_credit_synthesis"].status == "blocked"

    json_path, html_path = write_artifacts(assessment, tmp_path)
    frozen = json.loads(json_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")
    assert frozen["content_hash"] == assessment.content_hash
    assert "The claim requires qualification." in html
    assert "EV-M1-1" in html
    assert "final_credit_synthesis" in html










def test_compound_risk_preserves_component_and_evidence_links():
    assessment = adapt_business_overview(_handoff())
    risk = assessment.business_assessment.compound_risks[0]
    assert risk.component_hypothesis_ids == ["M1-H001"]
    assert risk.evidence_ids == ["EV-M1-1"]
    finding = assessment.business_assessment.business_model_and_revenue_drivers[0]
    assert finding.provenance.source_claim_ids == ["R-001"]
    evidence = next(x for x in assessment.evidence_references if x.evidence_id == "EV-M1-1")
    assert evidence.source_hypothesis_ids == ["M1-H001"]
    assert evidence.source_claim_ids == ["R-001"]


def test_transformation_and_html_are_deterministic():
    first = adapt_business_overview(_handoff())
    second = adapt_business_overview(_handoff())
    assert first.content_hash == second.content_hash
    assert assessment_content_hash(first) == first.content_hash
    assert render_assessment_html(first) == render_assessment_html(second)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda x: x.update(schema_version="bo-handoff-9.9"), "Unsupported BO handoff schema"),
        (lambda x: x.update(analysis_run_id="bad id"), "Malformed analysis run ID"),
        (lambda x: x["canonical_references"].update(analysis_run_id="ar_other"), "Canonical analysis-run reference"),
        (lambda x: x["compound_business_risks"][0].update(hypothesis_ids=["M1-H999"]), "broken hypothesis links"),
        (lambda x: x["compound_business_risks"][0].update(evidence_ids=["EV-MISSING"]), "broken evidence links"),
    ],
)
def test_invalid_inputs_are_rejected(mutation, message):
    payload = copy.deepcopy(_handoff())
    mutation(payload)
    _rehash(payload)
    with pytest.raises(StructuralOverviewInputError, match=message):
        adapt_business_overview(payload)


def test_incompatible_case_identity_is_rejected():
    with pytest.raises(StructuralOverviewInputError, match="Case identity mismatch"):
        adapt_business_overview(_handoff(), expected_case_id="OTHER_CASE")


def test_tampered_source_handoff_is_rejected():
    payload = _handoff()
    payload["module_conclusions"]["M1"]["findings"][0]["claim"] = "Tampered conclusion."
    with pytest.raises(StructuralOverviewInputError, match="content hash"):
        adapt_business_overview(payload)


def test_missing_bo_score_remains_unavailable():
    payload = _handoff()
    payload.update(business_risk_score_id=None, business_risk_score_status="unavailable", business_risk_band=None)
    assessment = adapt_business_overview(_rehash(payload))
    assert assessment.governance.provisional_business_risk_band is None
    assert assessment.governance.business_risk_score_status == "unavailable"
    assert assessment.final_credit_score is None


@pytest.mark.skipif(not REAL_HCA_HANDOFF.exists(), reason="local genuine HCA BO artifact is not present")
def test_genuine_hca_handoff_maps_with_lineage_and_challenge_visible():
    source = json.loads(REAL_HCA_HANDOFF.read_text(encoding="utf-8"))
    assessment = adapt_business_overview(source, expected_case_id="HCA_2025_BO_LIVE")
    all_findings = [
        *assessment.business_assessment.business_model_and_revenue_drivers,
        *assessment.business_assessment.industry_and_competitive_risks,
        *assessment.business_assessment.management_and_operational_findings,
        *assessment.business_assessment.strategy_and_capital_allocation_findings,
    ]
    assert assessment.borrower.reporting_entity_name == "HCA Healthcare, Inc."
    assert assessment.borrower.identification_status == "unavailable"
    assert assessment.borrower.legal_name is None
    assert assessment.source_bo_run_id == "ar_36d4a0589d224737"
    assert assessment.borrower.as_of_date == "2025-12-31"
    assert len(all_findings) >= 3
    assert all(x.provenance.source_run_id == assessment.source_bo_run_id for x in all_findings)
    assert any(x.supporting_evidence_ids for x in all_findings)
    compound = next(x for x in assessment.business_assessment.compound_risks if x.claim_id == "R-001")
    assert "M1A6-H001" in compound.component_hypothesis_ids
    assert compound.evidence_ids
    assert assessment.governance.conclusion_status == "accepted_with_qualification"
    assert assessment.governance.human_review_status == "pending"
    assert assessment.governance.unresolved_challenges
    challenge = assessment.governance.unresolved_challenges[0]
    assert challenge["reason"] in render_assessment_html(assessment)
