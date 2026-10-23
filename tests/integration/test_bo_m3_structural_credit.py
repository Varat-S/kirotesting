"""Offline coverage of Module 3 Layer 2: evidence-grounded structural credit.

Runs :func:`app.services.bo.m3_standalone.run_m3` with SCRIPTED offline
specialist responses (no model call) and asserts the ordering fix plus the
Layer-2 reasoning / Validator-7 behaviour across the T1-T12 scenarios and the
ordering-proof test. Everything is offline; conftest forbids live SEC I/O and
disables .env.

The scripted responses and the fictional corporate-structure document live in
``examples/bo_m3/structural_credit/`` and carry developer-authored expected
outcomes (``_scripted_offline``/``_label``). A passing scripted set is NOT proof
of real-world analytical reliability; live capability is UNVERIFIED.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.models.base import create_engine_and_session, init_db
from app.schemas.bo_m3_structure import NO_JSON_MESSAGE
from app.services.bo import comparison, m3_demo
from app.services.bo.completion import admit_corporate_structure_source
from app.services.bo.m3_standalone import run_m3
from tests import bo_helpers as h


FIXTURES = Path("examples/bo_m3/structural_credit")
HCA_SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")
FICTIONAL_STRUCTURE = FIXTURES / "fictional_synthetic_structure.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture(name: str) -> dict:
    return _load(FIXTURES / f"{name}_single_pass.json")


def _fresh_session():
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return factory()


# Synthetic admitted evidence for the FICTIONAL cases: a guaranty agreement and
# an intercompany-distribution restriction (developer-authored, offline). Phrase
# anchors in the fixtures resolve against these passages.
FICTIONAL_PASSAGES = {
    "ev_board": ("Item 10", "The board of directors oversees governance."),
    "ev_guar": ("Item 15",
                "Pursuant to the guaranty agreement, Larkspur Freight Operations "
                "LLC guarantees the obligations under the proposed credit facility."),
    "ev_restrict": ("Item 15",
                    "Under the indenture, intercompany distributions are restricted "
                    "from the warehousing subsidiary to the borrower."),
}


def _run_hca(session, responses=None):
    return run_m3(
        session, case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(),
        m3_responses={"single_pass": responses or _fixture("hca_simulated")},
        corporate_structure_document=_load(HCA_SIMPLIFIED))


def _run_fictional(session, name):
    return run_m3(
        session, case_id=h.CASE,
        evidence=h.evidence_snapshot(FICTIONAL_PASSAGES), metrics=h.metrics(),
        m3_responses={"single_pass": _fixture(name)},
        corporate_structure_document=_load(FICTIONAL_STRUCTURE))


def _findings(result) -> dict:
    return {f["finding_id"]: f
            for f in (result.governed_assessment or {}).get(
                "structural_credit_findings", [])}


def _rules(result) -> set:
    return {i["rule_id"] for i in result.validation_issues}


# -- T-ordering: the structure reaches the specialist BEFORE it runs ----------


def test_ordering_structure_in_specialist_context_before_pass(monkeypatch):
    """Spy on run_single_pass: the validated structure must be in prep BEFORE
    the pass executes, with node AND link ids and the deterministic facts."""
    captured: dict = {}
    real = comparison.run_single_pass

    def spy(session, prep, response=None, *, backend=None):
        # Record what the specialist actually receives BEFORE the pass returns.
        facts = prep.structural_facts
        captured["facts"] = copy.deepcopy(facts)
        captured["inputs"] = copy.deepcopy(comparison._single_pass_inputs(prep))
        return real(session, prep, response, backend=backend)

    monkeypatch.setattr("app.services.bo.m3_standalone.run_single_pass", spy)
    result = _run_hca(_fresh_session())

    facts = captured["facts"]
    assert facts is not None, "structure was NOT present in the specialist context"
    assert facts["borrower"]["node_id"] == "hca_borrower"
    node_ids = {n["node_id"] for n in facts["nodes"]}
    link_ids = {l["link_id"] for l in facts["links"]}
    assert "hca_borrower" in node_ids
    assert any(lid.startswith("link:") for lid in link_ids)
    assert facts["deterministic_observations"]

    # The actual specialist INPUT dict carried the corporate_structure block.
    inputs = captured["inputs"]
    assert "corporate_structure" in inputs
    assert {n["node_id"] for n in inputs["corporate_structure"]["nodes"]} == node_ids
    assert any(l["link_id"].startswith("link:")
               for l in inputs["corporate_structure"]["links"])

    # The manifest records the structure-in-context flag for audit.
    assert result.run_manifest["execution"]["structure_in_specialist_context"] is True


# -- T1: valid ownership, no extra disclosures -> no fabricated findings ------


def test_t1_valid_ownership_no_fabrication():
    result = _run_hca(_fresh_session())
    # Deterministic observations are present and accurate.
    section_a = result.m3_output["section_a_corporate_structure"]
    assert section_a["corporate_structure_status"] == "available"
    assert section_a["structural_observations"]
    # No structural credit finding is admitted as a SUPPORTED debt/guarantee/
    # cash-restriction claim: without admitted legal/debt evidence they stay
    # unverified/qualified (no fabrication).
    for finding in _findings(result).values():
        assert finding["disposition"] != "supported"


# -- T2: supported positive implication -> qualified positive -----------------


def test_t2_supported_positive_is_qualified():
    result = _run_fictional(_fresh_session(), "fictional_positive")
    finding = _findings(result)["SCF-POS-GUARANTOR"]
    assert finding["analytical_dimension"] == "guarantor_coverage"
    assert finding["credit_direction"] == "positive"
    assert finding["disposition"] == "qualified"
    # Validator 7 checked the referenced structural facts (no integrity rule).
    assert "m3.structural_reference_integrity" not in _rules(result)
    assert finding["supporting_evidence_ids"]


# -- T3: supported negative implication -> defensible negative ----------------


def test_t3_supported_negative_is_defensible():
    result = _run_fictional(_fresh_session(), "fictional_negative")
    finding = _findings(result)["SCF-NEG-CASHFLOW"]
    assert finding["analytical_dimension"] == "cash_flow_access"
    assert finding["credit_direction"] == "negative"
    assert finding["disposition"] in {"supported", "qualified"}
    assert len(finding["causal_mechanism"]) >= 15
    assert finding["supporting_evidence_ids"]


# -- T4: mixed assessment -> both coexist, none force-balanced ----------------


def test_t4_mixed_findings_coexist():
    result = _run_fictional(_fresh_session(), "fictional_mixed")
    findings = _findings(result)
    pos = findings["SCF-MIX-POS"]
    neg = findings["SCF-MIX-NEG"]
    assert pos["credit_direction"] == "positive"
    assert neg["credit_direction"] == "negative"
    # Both retained (not reconciled to a single net stance).
    assert pos["disposition"] in {"supported", "qualified"}
    assert neg["disposition"] in {"supported", "qualified"}


# -- T5: unsupported guarantee from a flag alone -> rejected/qualified ---------


def test_t5_unsupported_guarantee_is_rejected_or_qualified():
    result = _run_fictional(_fresh_session(), "fictional_unsupported_guarantee")
    finding = _findings(result)["SCF-BAD-GUARANTEE"]
    assert finding["disposition"] in {"rejected", "qualified", "unverified"}
    rules = _rules(result)
    assert ("m3.guarantee_not_proven_by_flag" in rules
            or "m3.simulated_guarantor_not_verified" in rules)
    # Excluded from the authoritative conclusion but retained for audit.
    conclusion = result.m3_analysis["consolidated_conclusion"]
    assert "SCF-BAD-GUARANTEE" not in conclusion[
        "admitted_structural_credit_finding_ids"]
    assert "SCF-BAD-GUARANTEE" in result.m3_analysis["evidence_verification"][
        "excluded_from_conclusion_ids"]


# -- T6: complexity without demonstrated risk -> NOT auto-negative ------------


def test_t6_complexity_not_auto_negative():
    # A complexity finding asserting a negative direction from entity count alone,
    # with no mechanism and no evidence, must be downgraded (not auto-negative).
    response = copy.deepcopy(_fixture("hca_simulated"))
    response["m3_assessment"]["structural_credit_findings"].append({
        "finding_id": "SCF-COMPLEX",
        "hypothesis_id": None,
        "credit_hypothesis": "Too many entities.",
        "causal_mechanism": "complex",
        "structural_observation": "14 nodes.",
        "analytical_dimension": "structural_complexity",
        "referenced_node_ids": ["hca_top"],
        "referenced_link_ids": [],
        "onboarding_source_reference": "corp-onboarding-1",
        "credit_direction": "negative",
        "assessment_availability": "sufficient",
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported",
        "admissibility": "established",
        "confidence": "high",
        "materiality": "high",
        "borrower_specific": False,
        "downstream_verification_requirements": [],
        "qualifications": [],
        "missing_information": [],
    })
    result = _run_hca(_fresh_session(), responses=response)
    finding = _findings(result)["SCF-COMPLEX"]
    assert finding["disposition"] != "supported"
    assert "m3.complexity_not_auto_negative" in _rules(result)


# -- T7: minority / JV -> no invented restrictions / cash effects -------------


def test_t7_minority_jv_not_auto_negative():
    response = copy.deepcopy(_fixture("hca_simulated"))
    response["m3_assessment"]["structural_credit_findings"].append({
        "finding_id": "SCF-JV",
        "hypothesis_id": None,
        "credit_hypothesis": "Minority JV leaks cash.",
        "causal_mechanism": "jv",
        "structural_observation": "A JV grouping node is present.",
        "analytical_dimension": "minority_or_jv",
        "referenced_node_ids": ["hca_jv"],
        "referenced_link_ids": ["link:hca_other->hca_jv"],
        "onboarding_source_reference": "corp-onboarding-1",
        "credit_direction": "negative",
        "assessment_availability": "sufficient",
        "supporting_evidence_ids": [],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported",
        "admissibility": "established",
        "confidence": "high",
        "materiality": "high",
        "borrower_specific": False,
        "downstream_verification_requirements": [],
        "qualifications": [],
        "missing_information": [],
    })
    result = _run_hca(_fresh_session(), responses=response)
    finding = _findings(result)["SCF-JV"]
    assert finding["disposition"] != "supported"
    assert "m3.minority_not_auto_negative" in _rules(result)


# -- T8: contradictory evidence does NOT overwrite admitted onboarding facts --


def test_t8_onboarding_facts_not_overwritten():
    # The AI proposal cannot rewrite the deterministic structure: Deliverable A
    # and the deterministic observations are driven by the validated onboarding
    # JSON regardless of the specialist proposal.
    baseline = _run_hca(_fresh_session())
    # A proposal that references non-existent nodes / invents percentages.
    response = copy.deepcopy(_fixture("hca_simulated"))
    response["m3_assessment"]["structural_credit_findings"].append({
        "finding_id": "SCF-CONTRA",
        "hypothesis_id": None,
        "credit_hypothesis": "The borrower directly owns 10% of Atlantic.",
        "causal_mechanism": "A rewritten ownership percentage of the structure.",
        "structural_observation": "Rewritten link.",
        "analytical_dimension": "borrower_position",
        "referenced_node_ids": ["hca_atlantic"],
        "referenced_link_ids": ["link:hca_regional->hca_atlantic"],
        "onboarding_source_reference": "corp-onboarding-1",
        "credit_direction": "negative",
        "assessment_availability": "sufficient",
        "supporting_evidence_ids": [{"$evidence": "executive officers"}],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported",
        "admissibility": "established",
        "confidence": "high",
        "materiality": "high",
        "borrower_specific": False,
        "downstream_verification_requirements": [],
        "qualifications": [],
        "missing_information": [],
    })
    contested = _run_hca(_fresh_session(), responses=response)

    # The de-wrapped Deliverable A export is identical: the AI did not rewrite it.
    assert contested.m3_corporate_structure == baseline.m3_corporate_structure
    # Deterministic observations unchanged.
    assert (contested.m3_output["section_a_corporate_structure"][
        "structural_observations"] == baseline.m3_output[
        "section_a_corporate_structure"]["structural_observations"])
    # The rewriting percentage claim (100% link asserted as 10%) is rejected.
    assert contested.governed_assessment
    finding = _findings(contested)["SCF-CONTRA"]
    assert finding["disposition"] in {"rejected", "unverified"}


# -- T9: missing corporate JSON -> no structure, no invented findings ---------


def test_t9_missing_corporate_json_partial_not_silent():
    result = run_m3(
        _fresh_session(), case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(),
        m3_responses={"single_pass": _fixture("hca_simulated")},
        corporate_structure_document=None)

    assert result.corporate_structure_status == "unavailable"
    assert result.m3_corporate_structure is None
    assert result.completion_status == "partial"
    reference = result.m3_analysis["corporate_structure_reference"]
    assert reference["unavailable_message"] == NO_JSON_MESSAGE
    # Without the structure the findings reference ids that are no longer
    # cross-checkable; none are admitted into the authoritative conclusion.
    assert result.m3_analysis["consolidated_conclusion"][
        "admitted_structural_credit_finding_ids"] == []
    # Structure was NOT in the specialist context.
    assert result.run_manifest["execution"][
        "structure_in_specialist_context"] is False
    # Senior-management assessment still proceeds.
    assert result.m3_output["section_b_management_commentary"][
        "assessment_status"] == "assessed"


# -- T10: cross-case / out-of-packet evidence must NOT support a finding ------


def test_t10_cross_case_evidence_not_admitted():
    # A finding that cites an evidence id belonging to ANOTHER company / case
    # (not in this case's sealed packet) must be downgraded, never admitted.
    response = copy.deepcopy(_fixture("hca_simulated"))
    response["m3_assessment"]["structural_credit_findings"].append({
        "finding_id": "SCF-XCASE",
        "hypothesis_id": None,
        "credit_hypothesis": "A guarantee established by another company's filing.",
        "causal_mechanism": "Borrowed evidence from an unrelated case packet.",
        "structural_observation": "Operating subsidiary guarantor marking.",
        "analytical_dimension": "guarantor_coverage",
        "referenced_node_ids": ["hca_hosp_ops"],
        "referenced_link_ids": [],
        "onboarding_source_reference": "corp-onboarding-1",
        "credit_direction": "positive",
        "assessment_availability": "sufficient",
        "supporting_evidence_ids": ["ev_from_a_totally_different_case"],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported",
        "admissibility": "established",
        "confidence": "high",
        "materiality": "high",
        "borrower_specific": False,
        "downstream_verification_requirements": [],
        "qualifications": [],
        "missing_information": [],
    })
    result = _run_hca(_fresh_session(), responses=response)
    finding = _findings(result)["SCF-XCASE"]
    assert finding["disposition"] != "supported"
    assert "m3.evidence_integrity" in _rules(result)
    assert "SCF-XCASE" not in result.m3_analysis["consolidated_conclusion"][
        "admitted_structural_credit_finding_ids"]


# -- T11: simulation isolation ------------------------------------------------


def test_t11_simulation_isolation():
    # A finding that combines the SIMULATED HCA hierarchy with external evidence
    # to assert real guarantee protection, without a simulation qualification,
    # must be downgraded/qualified via the simulation-isolation safeguard.
    response = copy.deepcopy(_fixture("hca_simulated"))
    response["m3_assessment"]["structural_credit_findings"].append({
        "finding_id": "SCF-SIM",
        "hypothesis_id": None,
        "credit_hypothesis": "The HCA operating subsidiary provides real, enforceable guarantee protection.",
        "causal_mechanism": "The marked subsidiary guarantees the real obligations per the admitted filing.",
        "structural_observation": "Operating subsidiary guarantor marking.",
        "analytical_dimension": "guarantor_coverage",
        "referenced_node_ids": ["hca_hosp_ops"],
        "referenced_link_ids": [],
        "onboarding_source_reference": "corp-onboarding-1",
        "credit_direction": "positive",
        "assessment_availability": "sufficient",
        "supporting_evidence_ids": [{"$evidence": "executive officers"}],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported",
        "admissibility": "established",
        "confidence": "high",
        "materiality": "high",
        "borrower_specific": False,
        "downstream_verification_requirements": [],
        "qualifications": [],
        "missing_information": [],
    })
    result = _run_hca(_fresh_session(), responses=response)
    finding = _findings(result)["SCF-SIM"]
    assert finding["disposition"] != "supported"
    rules = _rules(result)
    assert ("m3.simulation_isolation" in rules
            or "m3.simulated_guarantor_not_verified" in rules
            or "m3.guarantee_not_proven_by_flag" in rules)


# -- T12: senior-management regression ----------------------------------------


def test_t12_senior_management_unchanged():
    # The HCA case's senior-management observation retains its existing governed
    # disposition and credit-direction behaviour (Pillar B unchanged).
    result = _run_hca(_fresh_session())
    mgmt = result.governed_assessment["management_assessments"]
    assert len(mgmt) == 1
    observation = mgmt[0]
    assert observation["observation_id"] == "MO1"
    assert observation["disposition"] == "qualified"
    # The management observation is admitted into the consolidated conclusion.
    assert "MO1" in result.m3_analysis["consolidated_conclusion"][
        "admitted_management_signal_ids"]


# -- T-demo: six files + the new structural_credit_quality scorecard ----------


def test_demo_writes_six_files_with_structural_credit_scorecard(tmp_path):
    result = _run_fictional(_fresh_session(), "fictional_mixed")
    expected_labels = {"SCF-MIX-POS": "qualified", "SCF-MIX-NEG": "qualified"}
    paths = m3_demo.write_m3_demo(
        result, tmp_path, simplified_document=_load(FICTIONAL_STRUCTURE),
        corporate_structure_path=str(FICTIONAL_STRUCTURE),
        responses_path="fictional_mixed_single_pass.json",
        expected_labels=expected_labels)
    for key in ("m3_corporate_structure", "m3_structure_provenance", "m3_analysis",
                "validation_report", "evaluation_scorecard", "run_manifest"):
        assert key in paths and paths[key].exists()

    scorecard = json.loads(paths["evaluation_scorecard"].read_text())
    quality = scorecard["structural_credit_quality"]
    assert quality["structure_in_specialist_context"] is True
    assert quality["finding_count"] == 2
    # Explicit denominators are present and non-trivial for this case.
    assert quality["structural_fact_accuracy"]["denominator"] == 2
    assert quality["credit_direction_justification"]["denominator"] == 2
    assert quality["validator_rejection_qualification_accuracy"] == {
        "numerator": 2, "denominator": 2,
        "population": "findings_with_developer_authored_expected_disposition"}

    # HCA demo also populates the scorecard (simulated-structure denominators).
    hca = _run_hca(_fresh_session())
    hca_paths = m3_demo.write_m3_demo(
        hca, tmp_path / "hca", simplified_document=_load(HCA_SIMPLIFIED))
    hca_card = json.loads(hca_paths["evaluation_scorecard"].read_text())
    assert hca_card["structural_credit_quality"]["finding_count"] == 3
