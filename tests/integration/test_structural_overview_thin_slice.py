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


def _handoff(m3_version: str = "v1") -> dict:
    """A test handoff. M3-v2's module block has no scored findings (the rebuild's
    two typed components start ``not_attempted``)."""
    findings = {
        "M1": _finding("M1", 1, qualified=True),
        "M2": _finding("M2", 1),
        "M3": _finding("M3", 1),
        "M4": _finding("M4", 1),
    }
    if m3_version == "v2":
        del findings["M3"]
    payload = {
        "handoff_id": "bo_test001",
        "schema_version": "bo-handoff-1.3",
        "analysis_run_id": "ar_test001",
        "case_id": "TEST_CASE",
        "borrower_name": "Test Borrower, Inc.",
        "borrower_entity_id": "TEST",
        "as_of_date": "2025-12-31",
        "evidence_cutoff_timestamp": "2026-03-01T00:00:00Z",
        "evidence_snapshot_version": 3,
        "business_topic_conclusion_id": "tc_test001",
        "conclusion_status": "accepted_with_qualification",
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
    if m3_version == "v2":
        from app.services.bo.m3_rebuild.adapters import unavailable_block
        payload["module_conclusions"]["M3"] = unavailable_block(payload["case_id"])
    payload["content_hash"] = handoff_content_hash(payload)
    return payload


def _rehash(payload: dict) -> dict:
    payload["content_hash"] = handoff_content_hash(payload)
    return payload


def _governed_m3(*, borrower_status: str = "identified") -> dict:
    limitation = None if borrower_status == "identified" else (
        "Proposed borrower is unavailable; SO must confirm the facility obligor."
    )
    return {
        "schema_version": "bo-m3-corporate-assessment-1.0",
        "validator_id": "validator_7",
        "validator_version": "validator-7-1.0.0",
        "borrower_identification": {
            "status": borrower_status,
            "entity_id": "borrower" if borrower_status == "identified" else None,
            "evidence_ids": ["EV-M3-STRUCT"] if borrower_status == "identified" else [],
            "limitation": limitation,
        },
        "legal_entities": [{
            "entity_id": "parent", "legal_name": "Test Parent, Inc.",
            "jurisdiction": "US-DE", "classification": "ultimate_parent",
            "verification_status": "verified", "identity_type": "legal_entity",
            "evidence_ids": ["EV-M3-STRUCT"], "materiality_reason": "Parent of borrower.",
        }, {
            "entity_id": "borrower", "legal_name": "Test Finance LLC",
            "jurisdiction": "US-DE", "classification": "proposed_borrower",
            "verification_status": "verified", "identity_type": "legal_entity",
            "evidence_ids": ["EV-M3-STRUCT"], "materiality_reason": "Proposed facility obligor.",
        }],
        "ownership_relationships": [{
            "relationship_id": "OR-1", "parent_entity_id": "parent",
            "subsidiary_entity_id": "borrower", "ownership_percentage": 100,
            "control_basis": "direct ownership", "effective_from": None,
            "effective_to": None, "evidence_ids": ["EV-M3-STRUCT"],
            "confirmation_status": "qualified", "uncertainty": "SO legal verification required.",
        }],
        "cash_flow_locations": [],
        "cash_transfer_relationships": [],
        "structural_credit_findings": [{
            "finding_id": "SF-1", "hypothesis_id": "M3-H001",
            "credit_hypothesis": "Parent access depends on borrower distributions.",
            "causal_mechanism": "Restrictions could limit debt-service cash access.",
            "supporting_evidence_ids": ["EV-M3-STRUCT"],
            "contradicting_evidence_ids": [],
            "supporting_evidence_investigated": True,
            "contradictory_evidence_investigated": True,
            "disposition": "qualified", "admissibility": "qualified_narrative",
            "confidence": "medium", "materiality": "high",
            "downstream_verification_requirements": [
                "Verify obligor identity, restriction enforceability and creditor ranking."
            ],
            "borrower_specific": borrower_status == "identified",
            "validation_reasons": [],
        }],
        "management_assessments": [], "governance_screening": [],
        "information_gaps": ["Legal ranking is not established."],
        "downstream_dependencies": [{"module": "SO", "question": "Verify legal ranking."}],
        "validation_issues": [{
            "rule_id": "m3.legal_entity_identity", "item_id": "affiliates",
            "action": "rejected", "reason": "Collective grouping is not a legal person.",
        }],
        "rejected_items": [{
            "kind": "legal_entity",
            "item": {"entity_id": "affiliates", "identity_type": "collective_grouping"},
            "reason": "collective_grouping_is_not_a_legal_person",
        }],
        "model_proposal": {
            "legal_entities": [{"entity_id": "affiliates", "identity_type": "collective_grouping"}]
        },
    }


def test_qualified_handoff_stays_partial_and_does_not_manufacture_credit_outputs(tmp_path):
    assessment = adapt_business_overview(_handoff(), expected_case_id="TEST_CASE")
    finding = assessment.business_assessment.business_model_and_revenue_drivers[0]
    assert finding.claim == "Preserved M1 source conclusion 1."
    assert finding.disposition == "qualified"
    assert finding.admissibility == "qualified_narrative"
    assert finding.provenance.source_hypothesis_id == "M1-H001"
    assert finding.supporting_evidence_ids == ["EV-M1-1"]
    assert assessment.status == "partial"
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


def test_governed_m3_crosses_boundary_without_promoting_rejected_graph_items():
    payload = _handoff()
    payload["module_conclusions"]["M3"].update(
        m3_assessment_status="validated", m3_assessment=_governed_m3())
    assessment = adapt_business_overview(_rehash(payload))

    assert assessment.schema_version == "structural-overview-assessment-1.1"
    assert assessment.borrower.legal_name == "Test Finance LLC"
    assert assessment.borrower.entity_id == "borrower"
    assert assessment.borrower.identification_evidence_ids == ["EV-M3-STRUCT"]
    structure = assessment.corporate_structure
    assert structure.source_status == "validated"
    assert {entity.entity_id for entity in structure.legal_entities} == {"parent", "borrower"}
    assert structure.ownership_relationships[0].confirmation_status == "qualified"
    assert structure.structural_credit_findings[0].disposition.value == "qualified"
    assert structure.structural_credit_findings[0].admissibility.value == "qualified_narrative"
    assert "Verify obligor identity" in structure.structural_credit_findings[0].downstream_verification_requirements[0]
    assert all(entity.entity_id != "affiliates" for entity in structure.legal_entities)
    assert structure.audit.rejected_items[0]["item"]["entity_id"] == "affiliates"
    assert structure.audit.model_proposal["legal_entities"][0]["entity_id"] == "affiliates"
    evidence = next(
        item for item in assessment.evidence_references
        if item.evidence_id == "EV-M3-STRUCT"
    )
    assert "M3.corporate_structure" in evidence.source_sections
    assert assessment.downstream_dependencies.sections["structural_overview"].status == "partial"


def test_validated_m3_with_unknown_borrower_fails_closed_but_keeps_governed_graph():
    payload = _handoff()
    payload["module_conclusions"]["M3"].update(
        m3_assessment_status="validated",
        m3_assessment=_governed_m3(borrower_status="unavailable"),
    )
    assessment = adapt_business_overview(_rehash(payload))
    assert assessment.borrower.identification_status == "unavailable"
    assert assessment.borrower.legal_name is None
    assert assessment.borrower.entity_id is None
    assert assessment.corporate_structure.legal_entities
    assert "SO must confirm" in assessment.borrower.identification_limitation


def test_validated_m3_requires_governed_contract_and_resolved_borrower():
    payload = _handoff()
    payload["module_conclusions"]["M3"].update(
        m3_assessment_status="validated", m3_assessment={"schema_version": "bad"})
    with pytest.raises(StructuralOverviewInputError, match="Invalid governed M3 assessment"):
        adapt_business_overview(_rehash(payload))

    payload = _handoff()
    m3 = _governed_m3()
    m3["legal_entities"] = [m3["legal_entities"][0]]
    m3["ownership_relationships"] = []
    payload["module_conclusions"]["M3"].update(
        m3_assessment_status="validated", m3_assessment=m3)
    with pytest.raises(StructuralOverviewInputError, match="does not resolve"):
        adapt_business_overview(_rehash(payload))

    payload = _handoff()
    m3 = _governed_m3()
    m3["ownership_relationships"][0]["subsidiary_entity_id"] = "rejected-node"
    payload["module_conclusions"]["M3"].update(
        m3_assessment_status="validated", m3_assessment=m3)
    with pytest.raises(StructuralOverviewInputError, match="references an unadmitted entity"):
        adapt_business_overview(_rehash(payload))


def test_disputed_m3_borrower_never_populates_transaction_identity():
    payload = _handoff()
    m3 = _governed_m3(borrower_status="disputed")
    m3["borrower_identification"]["entity_id"] = "borrower"
    m3["borrower_identification"]["evidence_ids"] = ["EV-M3-STRUCT"]
    payload["module_conclusions"]["M3"].update(
        m3_assessment_status="validated", m3_assessment=m3)
    assessment = adapt_business_overview(_rehash(payload))
    assert assessment.borrower.identification_status == "disputed"
    assert assessment.borrower.entity_id is None
    assert assessment.borrower.legal_name is None
    assert assessment.borrower.identification_evidence_ids == []


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


def test_legacy_handoff_with_a_business_risk_score_still_maps():
    payload = _handoff()
    payload.update(schema_version="bo-handoff-1.2", business_risk_score_id="score_test001",
                   business_risk_score_status="provisional", business_risk_band=3,
                   score_limitations=["BO score is provisional."])
    assessment = adapt_business_overview(_rehash(payload))
    assert "business_risk_band" not in assessment.governance.model_dump()
    assert assessment.final_credit_score is None
    payload["business_risk_band"] = 1  # still covered by the content hash
    with pytest.raises(StructuralOverviewInputError, match="content hash"):
        adapt_business_overview(payload)


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
