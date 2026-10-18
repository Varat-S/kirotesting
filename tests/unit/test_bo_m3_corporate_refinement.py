"""Offline acceptance cases for the Module 3 corporate refinement."""

from __future__ import annotations

from copy import deepcopy

import jsonschema

from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA, BO_NARROW_JSON_SCHEMA
from app.schemas.bo import AcceptedBusinessOverview
from app.services.bo.m3_validation import validate_m3_assessment


EVIDENCE = {"e_parent", "e_opco", "e_owner", "e_cash", "e_restrict", "e_mgr", "e_bad"}


def entity(eid, name, classification, evidence="e_parent", status="verified"):
    return {
        "entity_id": eid, "legal_name": name, "jurisdiction": "US-DE",
        "classification": classification, "verification_status": status,
        "evidence_ids": [evidence] if evidence else [],
        "materiality_reason": "Material to cash generation or borrower access.",
    }


def finding(fid="F1", **updates):
    value = {
        "finding_id": fid,
        "credit_hypothesis": "Holding-company access depends on subsidiary distributions.",
        "causal_mechanism": "Distribution limits could delay cash reaching the debt-servicing entity.",
        "supporting_evidence_ids": ["e_restrict"],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "qualified", "admissibility": "qualified_narrative",
        "confidence": "medium", "materiality": "high",
        "downstream_verification_requirements": [
            "SO to verify obligor, restriction enforceability and creditor ranking."
        ],
        "borrower_specific": True, "validation_reasons": [],
    }
    value.update(updates)
    return value


def management(oid="MO1", **updates):
    value = {
        "observation_id": oid, "management_id": "mgr-1", "legal_name": "Alex Doe",
        "role": "Chief Executive Officer", "effective_from": "2024-01-01",
        "effective_to": None, "dimension": "competence",
        "hypothesis": "Management execution is vulnerable after repeated disruptions.",
        "credit_mechanism": "Execution failures can weaken operating stability and cash generation.",
        "supporting_evidence_ids": ["e_mgr"], "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "qualified", "admissibility": "qualified_narrative",
        "confidence": "medium", "materiality": "high", "missing_information": [],
        "validation_reasons": [],
    }
    value.update(updates)
    return value


def proposal():
    return {
        "schema_version": "bo-m3-corporate-assessment-1.0",
        "borrower_identification": {
            "status": "identified", "entity_id": "holdco",
            "evidence_ids": ["e_parent"], "limitation": None,
        },
        "legal_entities": [
            entity("parent", "Example Parent, Inc.", "ultimate_parent"),
            entity("holdco", "Example Finance LLC", "proposed_borrower"),
            entity("opco", "Example Operations LLC", "operating_subsidiary", "e_opco"),
        ],
        "ownership_relationships": [{
            "relationship_id": "OR1", "parent_entity_id": "parent",
            "subsidiary_entity_id": "holdco", "ownership_percentage": 100,
            "control_basis": "direct voting ownership", "effective_from": "2024-01-01",
            "effective_to": None, "evidence_ids": ["e_owner"],
            "confirmation_status": "verified", "uncertainty": None,
        }, {
            "relationship_id": "OR2", "parent_entity_id": "holdco",
            "subsidiary_entity_id": "opco", "ownership_percentage": 100,
            "control_basis": "direct voting ownership", "effective_from": "2024-01-01",
            "effective_to": None, "evidence_ids": ["e_owner"],
            "confirmation_status": "verified", "uncertainty": None,
        }],
        "cash_flow_locations": [{
            "cash_flow_id": "CF1", "entity_id": "opco", "amount": 25.0,
            "period": "FY2025", "currency": "USD", "units": "millions",
            "accounting_basis": "entity statutory cash from operations",
            "reporting_scope": "entity", "evidence_ids": ["e_cash"],
            "cash_flow_roles": ["operating_cash_generator", "legal_cash_controller"],
            "provenance_status": "verified", "missing_information": [],
        }],
        "cash_transfer_relationships": [{
            "transfer_id": "CT1", "source_entity_id": "opco",
            "destination_entity_id": "holdco", "mechanism": "dividend",
            "transfer_evidence_state": "capacity_only",
            "known_restrictions_or_dependencies": ["documented distribution condition"],
            "evidence_ids": ["e_restrict"], "confirmation_status": "qualified",
            "uncertainty": "SO must determine legal effect.",
        }],
        "structural_credit_findings": [finding()],
        "management_assessments": [management()],
        "governance_screening": [],
        "information_gaps": ["Definitive legal ranking is outside M3 scope."],
        "downstream_dependencies": [{"module": "SO", "question": "Verify obligor and ranking."}],
    }


def validate(value=None):
    return validate_m3_assessment(value or proposal(), allowed_evidence_ids=EVIDENCE)


def test_provider_schema_describes_nested_m3_objects_instead_of_empty_shells():
    value = proposal()
    for record in [*value["structural_credit_findings"],
                   *value["governance_screening"]]:
        record.setdefault("hypothesis_id", None)
        record.pop("validation_reasons", None)
    for record in value["management_assessments"]:
        record.pop("validation_reasons", None)
    jsonschema.validate(
        {"parameters": [], "hypotheses": [], "m3_assessment": value},
        BO_M3_JSON_SCHEMA)
    assert "m3_assessment" not in BO_NARROW_JSON_SCHEMA["properties"]
    # M1A6-only branches would push the combined M3 response over Vertex's
    # structured-schema complexity limit and have no meaning for this module.
    m3_hypothesis = BO_M3_JSON_SCHEMA["properties"]["hypotheses"]["items"]
    assert "component_hypothesis_ids" not in m3_hypothesis["properties"]
    assert "retrieval_requests" in BO_M3_JSON_SCHEMA["properties"]
    entity = BO_M3_JSON_SCHEMA["properties"]["m3_assessment"]["properties"][
        "legal_entities"]["items"]
    assert {"entity_id", "legal_name", "classification"} <= set(entity["properties"])


def test_case_a_complex_group_principal_graph_is_material_and_bounded():
    result = validate()
    assert {e.entity_id for e in result.legal_entities} == {"parent", "holdco", "opco"}
    assert all(e.materiality_reason for e in result.legal_entities)


def test_case_b_holdco_distribution_dependency_is_qualified_not_legal_conclusion():
    result = validate()
    assert result.cash_transfer_relationships[0].confirmation_status == "qualified"
    structural = result.structural_credit_findings[0]
    assert structural.disposition.value == "qualified"
    assert "SO to verify" in structural.downstream_verification_requirements[0]


def test_case_c_unknown_borrower_keeps_borrower_specific_finding_unavailable():
    value = proposal()
    value["borrower_identification"] = {
        "status": "unavailable", "entity_id": None, "evidence_ids": [],
        "limitation": "No proposed borrowing entity was specified.",
    }
    result = validate(value)
    assert result.structural_credit_findings[0].disposition.value == "unverified"
    assert result.structural_credit_findings[0].admissibility.value == "audit_only"


def test_case_d_consolidated_cash_cannot_be_allocated_to_subsidiary():
    value = proposal()
    value["cash_flow_locations"][0]["reporting_scope"] = "consolidated"
    result = validate(value)
    assert result.cash_flow_locations == []
    assert result.rejected_items[0]["reason"] == \
        "unsupported_subsidiary_allocation_of_consolidated_cash_flow"


def test_case_e_tenure_alone_does_not_establish_management_competence():
    value = proposal()
    value["management_assessments"] = [management(
        hypothesis="Long tenure establishes strong competence.",
        credit_mechanism="Executive tenure and qualifications support competence.",
        disposition="supported", admissibility="established")]
    result = validate(value)
    observation = result.management_assessments[0]
    assert observation.disposition.value == "qualified"
    assert observation.admissibility.value == "qualified_narrative"


def test_case_f_limited_management_evidence_preserves_inability_to_conclude():
    value = proposal()
    value["management_assessments"] = [management(
        supporting_evidence_ids=[], disposition="unverified", admissibility="audit_only",
        missing_information=["No evidence links management actions to outcomes."])]
    result = validate(value)
    assert result.management_assessments[0].disposition.value == "unverified"
    assert result.management_assessments[0].missing_information


def test_case_g_material_related_party_concern_is_carried_forward():
    value = proposal()
    value["governance_screening"] = [finding(
        "G1", credit_hypothesis="A material related-party transfer may divert value.",
        causal_mechanism="Cash transferred to an affiliate may reduce creditor-accessible resources.")]
    result = validate(value)
    assert [x.finding_id for x in result.governance_screening] == ["G1"]
    assert result.governance_screening[0].materiality.value == "high"


def test_case_h_contradictory_ownership_disclosures_are_rejected_and_audited():
    value = proposal()
    conflict = deepcopy(value["ownership_relationships"][0])
    conflict.update(relationship_id="OR3", ownership_percentage=80)
    value["ownership_relationships"].append(conflict)
    result = validate(value)
    assert len(result.ownership_relationships) == 2
    assert any(i.reason == "contradictory_ownership_or_control"
               for i in result.validation_issues)
    assert result.model_proposal["ownership_relationships"][-1]["ownership_percentage"] == 80


def test_case_i_resolving_citation_without_recorded_investigation_is_not_established():
    value = proposal()
    value["structural_credit_findings"][0].update(
        disposition="supported", admissibility="established",
        contradictory_evidence_investigated=False)
    result = validate(value)
    finding_result = result.structural_credit_findings[0]
    assert finding_result.disposition.value == "unverified"
    assert finding_result.admissibility.value == "audit_only"


def test_case_j_legacy_handoff_remains_interpretable_with_qualification_and_hash():
    legacy = AcceptedBusinessOverview(
        handoff_id="bo_old", schema_version="bo-handoff-1.1", analysis_run_id="run_old",
        case_id="case_old", evidence_snapshot_version=1,
        business_topic_conclusion_id=None, conclusion_status="unavailable",
        business_risk_score_id=None, business_risk_score_status="unavailable",
        evidence_limitations=["borrower unavailable"], canonical_references={"hash": "old"},
        agent_registry_version="2", agent_registry_hash="registry", scoring_config_version=1,
        scoring_config_hash="score",
    )
    assert legacy.schema_version == "bo-handoff-1.1"
    assert legacy.evidence_limitations == ["borrower unavailable"]
    assert legacy.module_conclusions == {}
