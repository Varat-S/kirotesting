"""Offline production-path controls; original live failure is immutable.

Authored responses below are regression inputs, never improved Gemini output.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import jsonschema
import pytest

from app.core.hashing import content_hash
from app.services.bo.citations import evidence_aliases
from app.models.base import create_engine_and_session
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.bo.comparison import prepare_specialist
from app.services.bo.corporate_structure import CaseCorporateStructureSource, build_corporate_structure_artifact
from app.services.bo.m3_case_evidence import attach_case_evidence, verified_scenario_source
from app.services.bo.m3_diagnostics import load_recorded_m3_run
from app.services.bo.m3_standalone import run_m3
from app.services.bo.m3_structure_context import build_structural_facts
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
from tests import bo_helpers as h


DOCUMENT = Path("data/test_inputs/hca_simulated_corporate_structure.json")
LIVE_DB = Path("data/m3_v14_hca_live_20261026_01.db")
LIVE_RUN = "23d91a53-dc5e-4a74-8f03-593f97c94840"


@pytest.fixture
def original_live():
    if not LIVE_DB.exists():
        pytest.skip("Private original live recording absent; never replaced with a provider call.")
    before = hashlib.sha256(LIVE_DB.read_bytes()).hexdigest()
    engine, sessions = create_engine_and_session(f"sqlite:///file:{LIVE_DB.resolve()}?mode=ro&uri=true")
    with sessions() as session:
        bundle = load_recorded_m3_run(session, LIVE_RUN)
    engine.dispose()
    yield bundle
    assert hashlib.sha256(LIVE_DB.read_bytes()).hexdigest() == before


def scenario_setup(session):
    doc = json.loads(DOCUMENT.read_text())
    artifact = build_corporate_structure_artifact(CaseCorporateStructureSource(
        case_id=h.CASE, registered_case_id=h.CASE, payload=doc, source_document_id="corp-onboarding-1"),
        expected_case_id=h.CASE)
    prep = prepare_specialist(session, case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(), spec=V3_SPEC_BY_CODE["M3"])
    prep.structural_facts = build_structural_facts(artifact)
    prep = attach_case_evidence(prep)
    eid = prep.structural_facts["source"]["scenario_evidence_id"]
    return prep, eid, doc


def scenario_response(prep, eid):
    cases = [
        ("non-guarantor", "guarantor_coverage", "negative", ["hca_borrower", "hca_shared"],
         ["link:hca_borrower->hca_shared"],
         "Within the hypothetical case, the non-guarantor subsidiary may offer less direct lender recourse than an assumed guarantor.",
         "The case marks the shared-services subsidiary as a non-guarantor.",
         "If subsidiary creditors have claims, they could absorb subsidiary assets before upstream equity distributions available for borrower debt service; liabilities and ranking are unknown."),
        ("guarantor", "guarantor_coverage", "positive", ["hca_borrower", "hca_hosp_ops"],
         ["link:hca_borrower->hca_hosp_ops"],
         "Within the hypothetical case, assumed guarantor coverage may improve lender recourse, subject to unverified legal terms.",
         "The case marks the hospital-operations subsidiary as an assumed guarantor.",
         "Assumed operating-company guarantees could give lenders additional recourse, but real enforceability and asset coverage remain unverified."),
        ("minority", "minority_or_jv", "neutral", ["hca_borrower", "hca_hosp_ops", "hca_ambulatory"],
         ["link:hca_borrower->hca_hosp_ops", "link:hca_hosp_ops->hca_ambulatory"],
         "Within the hypothetical case, minority economic ownership may affect the share of distributions reaching borrower lenders.",
         "The case assumes link:hca_hosp_ops->hca_ambulatory=80% ownership beneath the hospital-operations company.",
         "A minority economic interest may reduce the parent's share of distributions available for debt service; this does not establish contractual dividend restrictions or any cash amount."),
        ("cash", "cash_flow_access", "mixed", ["hca_borrower", "hca_shared"],
         ["link:hca_borrower->hca_shared"],
         "Within the hypothetical case, subsidiary cash generation may create an upstream distribution dependency for borrower lenders.",
         "The case places a shared-services operating subsidiary below the borrower.",
         "If cash is generated at the subsidiary, borrower debt service may depend on upstream transfers; ownership does not prove unrestricted access and cash amounts are unavailable."),
        ("placement", "borrower_position", "negative", ["hca_borrower", "hca_shared"],
         ["link:hca_borrower->hca_shared"],
         "Within the hypothetical case, borrower placement above an operating subsidiary may create structural subordination exposure.",
         "The case places the hypothetical borrower above the shared-services subsidiary.",
         "If subsidiary liabilities exist, subsidiary creditors could have prior claims on subsidiary assets, reducing residual distributions for borrower debt service; actual ranking and balances are unknown."),
    ]
    hypotheses, findings = [], []
    for i, (fid, dimension, direction, nodes, links, claim, observation, mechanism) in enumerate(cases, 1):
        hid = f"M3-H{i:03}"
        prefix = "Node " + nodes[-1] + ":" if dimension == "guarantor_coverage" else "Ownership " + links[-1] + ":"
        quote = next(line for line in prep.passage_text[eid].splitlines() if line.startswith(prefix))
        hypotheses.append({"hypothesis_id": hid, "claim": claim, "claim_type": "inference",
            "direction": direction, "disposition": "qualified", "confidence": "medium", "materiality": "medium",
            "supporting_evidence_ids": [eid], "contradicting_evidence_ids": [eid],
            "evidence_quotes": [{"evidence_id": eid, "quote": quote}], "credit_mechanism": mechanism})
        findings.append({"finding_id": fid, "hypothesis_id": hid, "credit_hypothesis": claim,
            "causal_mechanism": mechanism, "structural_observation": observation,
            "analytical_dimension": dimension, "referenced_node_ids": nodes, "referenced_link_ids": links,
            "onboarding_source_reference": "corp-onboarding-1", "credit_direction": direction,
            "assessment_availability": "sufficient", "supporting_evidence_ids": [eid],
            "contradicting_evidence_ids": [eid], "supporting_evidence_investigated": True,
            "contradictory_evidence_investigated": True, "disposition": "qualified",
            "admissibility": "qualified_narrative", "confidence": "medium", "materiality": "medium",
            "borrower_specific": True, "qualifications": ["Hypothetical case assumptions only; actual legal terms are unverified."],
            "missing_information": ["Subsidiary debt, cash amounts, guarantee terms and distribution restrictions."],
            "downstream_verification_requirements": ["Legal review before a real transaction."]})
    return {"parameters": [], "hypotheses": hypotheses, "m3_assessment": {
        "schema_version": "bo-m3-corporate-assessment-1.2",
        "borrower_identification": {"status": "unavailable", "entity_id": None, "evidence_ids": [],
            "limitation": "No verified actual transaction borrower; case borrower is separate."},
        "legal_entities": [], "ownership_relationships": [], "cash_flow_locations": [], "cash_transfer_relationships": [],
        "structural_credit_findings": findings, "management_assessments": [], "governance_screening": [],
        "information_gaps": ["Governance and real legal documentation are unavailable."], "downstream_dependencies": [],
        "management_stability": {"schema_version": "bo-m3-management-stability-1.0", "review_scope": "No management biography supplied in this portable structural control.",
            "as_of_date": None, "as_of_source": None, "executives": [], "limitations": ["Management unavailable."]}}}


def test_original_live_response_still_has_exactly_the_original_violation(original_live):
    raw = json.loads(original_live["raw_response"])
    errors = list(jsonschema.Draft7Validator(BO_M3_JSON_SCHEMA).iter_errors(raw))
    assert len(errors) == 1
    assert list(errors[0].absolute_path) == ["m3_assessment", "management_stability", "executives", 0, "facts", 0]
    assert original_live["canonical_validation_outcome"] == "rejected"


def test_original_malformed_answer_still_blocks_the_actual_runner(db_session, original_live):
    response = json.loads(original_live["raw_response"])
    before = deepcopy(response)
    result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(), metrics=h.metrics(),
        m3_responses={"single_pass": response}, required_assessment_version="bo-m3-corporate-assessment-1.2")
    assert result.validation_stages["canonical_schema_validation"] == "rejected"
    assert result.validation_stages["specialist_validation"] == "not_run"
    assert result.validation_stages["assessment_governance"] == "not_run"
    assert result.governed_assessment is None
    assert response == before


def test_actual_recorded_roster_and_isolated_categorical_gate(original_live):
    from app.schemas.agentic import EvidencePacket
    from app.services.agents.registry import default_registry
    from app.services.bo.indicators import IndicatorSet
    from app.services.bo.m3_roster import disclosed_rosters, roster_coverage
    from app.services.bo.validation_audit import validate_specialist_output
    inputs = original_live["recorded_request"]["inputs"]
    texts = {p["evidence_id"]: p["text"] for p in inputs["evidence"]}
    original = json.loads(original_live["raw_response"])
    coverage = roster_coverage(disclosed_rosters(texts), original["m3_assessment"]["management_stability"]["executives"])
    assert coverage["expected_count"] == 7 and coverage["proposed_count"] == 1
    assert coverage["roster_dates"] == ["2026-02-01"]
    assert any("Marks" in n for n in coverage["omitted_names"])
    with sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True) as connection:
        packet = EvidencePacket.model_validate(json.loads(connection.execute("SELECT payload FROM evidence_packets").fetchone()[0]))
    # Explicit component isolation: this is NOT a repaired model answer and does
    # not enter the standalone admission path. The original still fails above.
    component = {"parameters": deepcopy(original["parameters"]), "hypotheses": deepcopy(original["hypotheses"])}
    checked = validate_specialist_output(V3_SPEC_BY_CODE["M3"], default_registry("v3").get("m3_corporate_management"),
        packet, component, indicators=IndicatorSet(), passage_text=texts, corpus=inputs["evidence"],
        value_sets=inputs["parameter_value_sets"], analysis_run_id="offline-v14-component",
        agent_run_id="offline-v14-component:one", prompt_id="offline-component-check")
    assert checked.valid
    assert len(checked.parameters) == 5
    assert all(p.status.value == "unavailable" and p.value is None for p in checked.parameters)
    reasons = {c["parameter_id"]: c["reason"] for c in checked.parameter_changes}
    assert "dependency_grade" in reasons["key_person_dependency_assessment"]
    assert "broad_dated_review" in reasons["governance_concern_assessment"]
    assert reasons["litigation_risk_assessment"] == "no_validated_supporting_hypothesis"
    from app.services.bo.m3_grounding import received_inquiry
    assert any(received_inquiry(t) for t in texts.values())
    assert "broad_dated_review" in reasons["material_event_assessment"]


def test_scenario_findings_pass_actual_standalone_gate_and_v7_without_real_verification(db_session):
    prep, eid, doc = scenario_setup(db_session)
    response = scenario_response(prep, eid)
    jsonschema.validate(response, BO_M3_JSON_SCHEMA)
    result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(), metrics=h.metrics(),
        m3_responses={"single_pass": response}, corporate_structure_document=doc,
        required_assessment_version="bo-m3-corporate-assessment-1.2")
    assert result.specialist_validated, result.specialist_failure
    assert result.validation_stages["canonical_schema_validation"] == "valid"
    assert result.validation_stages["assessment_governance"] == "performed"
    findings = result.governed_assessment["structural_credit_findings"]
    assert {f["finding_id"] for f in findings if f["disposition"] == "qualified"} == {
        "non-guarantor", "guarantor", "minority", "cash", "placement"}
    assert result.validation_stages["admitted_analytical_findings"]["structural_credit"] == 5
    assert result.governed_assessment["borrower_identification"]["status"] == "unavailable"
    assert result.m3_analysis["scenario_assumptions"]["real_world_verification"] == "not_established"
    assert result.m3_analysis["scenario_assumptions"]["borrower"]["node_id"] == "hca_borrower"
    bundle = load_recorded_m3_run(db_session, result.validation_stages["model_run_id"])
    # The recording holds the request as the model saw it: the scenario passage
    # under its short evidence label, consistently in the structure and evidence.
    recorded = bundle["recorded_request"]
    packet_inputs = recorded["inputs"]
    alias = evidence_aliases(recorded["evidence_ids"])[eid]
    assert packet_inputs["corporate_structure"]["source"]["scenario_evidence_id"] == alias
    assert any(p["evidence_id"] == alias for p in packet_inputs["evidence"])
    assert result.model_called is False


@pytest.mark.parametrize("failure", ["unknown_node", "unknown_link", "wrong_source", "cross_topic", "unusable_hypothesis", "enforceable", "restriction", "unrestricted", "wrong_percentage", "wrong_guarantor"])
def test_scenario_support_never_authorizes_invalid_references_or_legal_claims(db_session, failure):
    prep, eid, _ = scenario_setup(db_session)
    response = scenario_response(prep, eid)
    item = response["m3_assessment"]["structural_credit_findings"][0]
    item.update(disposition="supported", admissibility="established")
    if failure == "unknown_node": item["referenced_node_ids"].append("invented")
    if failure == "unknown_link": item["referenced_link_ids"].append("invented")
    if failure == "wrong_source": item["onboarding_source_reference"] = "unrelated"
    if failure == "cross_topic": response["hypotheses"][0]["claim"] = "Management quality is unavailable."
    if failure == "enforceable": item["credit_hypothesis"] = "The guarantee is legally binding and enforceable."
    if failure == "restriction": item["credit_hypothesis"] = "Dividends are restricted by the ownership stake."
    if failure == "unrestricted": item["credit_hypothesis"] = "Subsidiary cash is freely available to the borrower."
    if failure == "wrong_percentage": item["structural_observation"] = "Case ownership is 35%."
    if failure == "wrong_guarantor": item["structural_observation"] = "hca_shared is an assumed guarantor."
    usable = {h["hypothesis_id"] for h in response["hypotheses"]}
    if failure == "unusable_hypothesis": usable.remove(item["hypothesis_id"])
    governed = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(prep.packet.evidence_ids),
        evidence_texts=prep.passage_text, structure_facts=prep.structural_facts,
        hypothesis_proposals=response["hypotheses"], usable_hypothesis_ids=usable)
    assert governed.structural_credit_findings[0].disposition.value in {"unverified", "rejected"}


def test_mixed_ownership_percentages_must_bind_to_the_right_link(db_session):
    prep, eid, _ = scenario_setup(db_session)
    response = scenario_response(prep, eid)
    item = response["m3_assessment"]["structural_credit_findings"][2]
    item["structural_observation"] = "The case assumes link:hca_borrower->hca_hosp_ops=80% ownership."
    result = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(prep.packet.evidence_ids),
        evidence_texts=prep.passage_text, structure_facts=prep.structural_facts,
        hypothesis_proposals=response["hypotheses"])
    assert result.structural_credit_findings[2].disposition.value == "rejected"
    assert any(i.rule_id == "m3.structural_percentage_binding" for i in result.validation_issues)


def test_hypothetical_entities_and_guarantors_stay_scenario_assumptions(db_session):
    prep, eid, _ = scenario_setup(db_session)
    response = scenario_response(prep, eid)
    assessment = response["m3_assessment"]
    nodes = {n["node_id"]: n for n in prep.structural_facts["nodes"]}
    for nid in ["hca_borrower", "hca_hosp_ops"]:
        assessment["legal_entities"].append({"entity_id": nid, "legal_name": nodes[nid]["display_name"],
            "classification": "proposed_borrower" if nid == "hca_borrower" else "operating_subsidiary",
            "identity_type": "legal_entity", "verification_status": "verified", "evidence_ids": [eid],
            "materiality_reason": "Hypothetical case entity only."})
    assessment["ownership_relationships"] = [{"relationship_id": "case-link", "parent_entity_id": "hca_borrower",
        "subsidiary_entity_id": "hca_hosp_ops", "ownership_percentage": 100, "evidence_ids": [eid], "confirmation_status": "verified"}]
    assessment["borrower_identification"] = {"status": "identified", "entity_id": "hca_borrower", "evidence_ids": [eid]}
    governed = validate_m3_assessment(assessment, allowed_evidence_ids=set(prep.packet.evidence_ids),
        evidence_texts=prep.passage_text, structure_facts=prep.structural_facts,
        validated_borrower_entity_ids={"hca_borrower"})
    assert all(e.verification_status == "qualified" for e in governed.legal_entities)
    assert governed.ownership_relationships[0].confirmation_status == "qualified"
    assert governed.borrower_identification.status == "unavailable"
    assert "hca_hosp_ops" in prep.structural_facts["guarantor_node_ids"]


def test_unrelated_filing_citation_cannot_verify_hypothetical_entity_or_new_case(db_session):
    prep, eid, _ = scenario_setup(db_session)
    response = scenario_response(prep, eid)
    response["m3_assessment"]["legal_entities"] = [{"entity_id": "hca_top", "legal_name": "HCA Parent Holdings, Inc.",
        "classification": "ultimate_parent", "identity_type": "legal_entity", "verification_status": "verified",
        "evidence_ids": ["real-filing"], "materiality_reason": "Parent."}]
    texts = {**prep.passage_text, "real-filing": "Exhibit 21 lists a different subsidiary."}
    governed = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(texts), evidence_texts=texts,
                                     structure_facts=prep.structural_facts)
    assert governed.legal_entities[0].verification_status == "unverified"
    changed = deepcopy(prep.structural_facts)
    changed["source"]["case_id"] = "another-case"
    assert verified_scenario_source(changed, set(texts), texts) is None


def test_new_version_requirement_fails_legacy_response_without_rewriting_history(db_session):
    from tests.integration.test_bo_m3_standalone import _single_pass
    result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(), metrics=h.metrics(),
        m3_responses={"single_pass": _single_pass()}, required_assessment_version="bo-m3-corporate-assessment-1.2")
    assert result.specialist_validated is False
    assert "version" in result.specialist_failure
    assert result.validation_stages["canonical_schema_validation"] == "valid"  # historical schema remains supported
