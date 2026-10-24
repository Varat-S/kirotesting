"""Integration tests for the Module 3 Architecture V6 refactor.

Exercises the live BO completion seam that threads a simplified v1.0 onboarding
corporate-structure JSON through the case-associated admission path, the
deterministic v1.0 -> v2.0 transform, and the two Architecture V6 deliverables
(m3_corporate_structure.json / m3_analysis.json) written to disk.

Also asserts the mandatory fail-closed behaviour and the management-commentary
regression after the refactor. Offline only; no model or network call.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.hashing import canonical_json
from app.services.bo.completion import (
    admit_corporate_structure_source,
    build_m3_corporate_structure,
    write_completion_artifacts,
)


SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")


def _simplified() -> dict:
    return json.loads(SIMPLIFIED.read_text())


def _m3_agent(calls):
    return {"agent_id": "m3_corporate_management", "code": "M3",
            "status": "validated", "coverage": [], "calls": calls}


def _call_with_onboarding(doc, case_id):
    src = admit_corporate_structure_source(
        doc, case_id=case_id, source_document_id="corp-onboarding-1")
    return {"inputs": {"corporate_structure_source": src}}


def test_simplified_onboarding_flows_through_completion_seam():
    agents = [_m3_agent([_call_with_onboarding(_simplified(), "HCA_CASE")])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "available"
    assert len(art.nodes) == 14
    assert len(art.links) == 13
    assert len(art.guarantor_node_ids) == 5
    assert art.provenance.transform_version is not None
    assert art.roundtrip_reconciliation["consistent"]
    assert svg and svg.count('data-guarantor="true"') == 5


def test_admission_seam_records_explicit_case_association():
    src = admit_corporate_structure_source(
        _simplified(), case_id="HCA_CASE", source_document_id="d1")
    assert src["case_id"] == "HCA_CASE"
    assert src["registered_case_id"] == "HCA_CASE"
    assert src["document_hash"]
    # The seam never auto-selects a case: wrong expected case is withheld.
    agents = [_m3_agent([{"inputs": {"corporate_structure_source": src}}])]
    art, svg = build_m3_corporate_structure(agents, case_id="OTHER_CASE")
    assert art.status == "rejected"
    assert svg is None


def test_fixture_in_examples_not_auto_loaded_v6():
    # No corporate_structure_source present -> fail closed even though the
    # simplified fixture exists in examples/.
    agents = [_m3_agent([{"inputs": {}}])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "unavailable"
    assert svg is None
    assert not art.nodes
    assert art.refined_structure is None


def test_tenk_exhibit21_do_not_trigger_structure_v6():
    call = {"inputs": {"evidence": [
        {"evidence_id": "ex21", "text": "Exhibit 21 — Subsidiaries"},
        {"evidence_id": "mdna", "text": "Item 7 MD&A narrative."}]}}
    agents = [_m3_agent([call])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "unavailable"
    assert svg is None


def _packet_with_m3(module_m3: dict) -> dict:
    return {
        "handoff": {
            "conclusion_status": "accepted",
            "business_risk_score_status": "provisional",
            "business_risk_band": "medium",
            "content_hash": "x",
            "analysis_run_id": "run1",
            "evidence_limitations": ["Transaction borrower not established."],
            "module_conclusions": {"M3": module_m3},
        },
        "source": {"analysis_run_id": "r", "sha256": "s",
                   "summary": {"agents_validated": 1, "hypotheses_recorded": 0,
                               "hypotheses_usable": 0, "parameters_accepted": 0,
                               "parameters_held_back": 0, "coverage_addressed": 0,
                               "coverage_checks": 0}},
        "execution_mode": "offline",
        "incremental_usage": {"calls": 0, "total_tokens": None},
        "challenge": {"findings": []},
        "post_challenge": {"changes": []},
        "remediation": {"specialist_calls_executed": 0, "max_specialist_calls": 2},
    }


def _build_module_m3(case_id, doc=None):
    from app.services.bo.completion import _module_conclusions
    calls = ([_call_with_onboarding(doc, case_id)] if doc is not None
             else [{"inputs": {}}])
    agents = [_m3_agent(calls)]
    governed = {
        "borrower_identification": {"status": "unavailable", "limitation": "SO confirms."},
        "management_assessments": [{"observation_id": "MO1", "credit_direction": "neutral"}],
        "governance_screening": [], "information_gaps": ["gap"],
        "validation_issues": [], "rejected_items": [],
    }
    out = _module_conclusions(agents, {}, governed, case_id=case_id)
    return out["M3"]


def test_both_deliverables_written_to_disk_when_available(tmp_path):
    m3 = _build_module_m3("HCA_CASE", _simplified())
    assert m3["m3_corporate_structure"] is not None
    assert m3["m3_analysis"] is not None
    packet = _packet_with_m3(m3)
    paths = write_completion_artifacts(packet, tmp_path)
    assert "m3_corporate_structure" in paths and paths["m3_corporate_structure"].exists()
    assert "m3_analysis" in paths and paths["m3_analysis"].exists()
    structure = json.loads(paths["m3_corporate_structure"].read_text())
    analysis = json.loads(paths["m3_analysis"].read_text())
    # Deliverable A export is the de-wrapped v2.0 payload AT ROOT (no
    # refined_structure/artifact_kind wrapper), matching the final-output contract.
    assert structure["schema_version"] == "2.0"
    assert len(structure["companies"]) == 1
    assert "refined_structure" not in structure
    assert "artifact_kind" not in structure
    # The governed internal record is written to a separate sidecar.
    assert "m3_structure_provenance" in paths and paths["m3_structure_provenance"].exists()
    sidecar = json.loads(paths["m3_structure_provenance"].read_text())
    # B references A by the stable sidecar id.
    assert analysis["corporate_structure_reference"]["artifact_id"] == sidecar["artifact_id"]
    # Deterministic: re-running yields identical bytes.
    m3b = _build_module_m3("HCA_CASE", _simplified())
    assert canonical_json(m3b["m3_corporate_structure"]) == canonical_json(
        m3["m3_corporate_structure"])


def test_missing_json_writes_analysis_only_not_structure(tmp_path):
    m3 = _build_module_m3("HCA_CASE", None)
    assert m3["m3_corporate_structure"] is None
    assert m3["corporate_structure_status"] == "unavailable"
    # Never silently marked fully complete when the corporate deliverable is
    # missing; the m3_output completion is at most partial (A unavailable).
    assert m3["status"] != "complete"
    assert m3["m3_output"]["completion"]["status"] == "partial"
    packet = _packet_with_m3(m3)
    paths = write_completion_artifacts(packet, tmp_path)
    # Fail-closed: Deliverable A is NOT written; B still is.
    assert "m3_corporate_structure" not in paths
    assert not (tmp_path / "m3_corporate_structure.json").exists()
    assert "m3_analysis" in paths and paths["m3_analysis"].exists()
    analysis = json.loads(paths["m3_analysis"].read_text())
    assert analysis["corporate_structure_status"] == "unavailable"
    assert analysis["senior_management_assessment"]["assessment_status"] == "assessed"


def test_management_commentary_regression_after_refactor():
    """Senior-management assessment stays functional and source-grounded."""
    from app.services.bo.m3_validation import validate_m3_assessment

    allowed = {"ev-1"}
    proposal = {
        "borrower_identification": {"status": "unavailable",
                                    "limitation": "SO confirms obligor."},
        "legal_entities": [],
        "management_assessments": [{
            "observation_id": "MO1", "management_id": "mgmt-1",
            "legal_name": "Dana Lee", "role": "CFO", "dimension": "competence",
            "evidence_basis": "outcome_evidence", "attribution": "independently_observed",
            "hypothesis": "The CFO executed the stated deleveraging plan.",
            "credit_mechanism": "Demonstrated execution supports repayment discipline over time.",
            "supporting_evidence_ids": ["ev-1"], "contradicting_evidence_ids": [],
            "supporting_evidence_investigated": True,
            "contradictory_evidence_investigated": True,
            "disposition": "supported", "admissibility": "established",
        }],
        "governance_screening": [],
        "information_gaps": ["Completeness not established."],
    }
    governed = validate_m3_assessment(
        proposal, allowed_evidence_ids=allowed,
        validated_borrower_entity_ids=set(),
        evidence_texts={"ev-1": "The CFO reduced net leverage as planned."},
    )
    assert governed.validator_id == "validator_7"
    assert governed.management_assessments  # commentary preserved
    obs = governed.management_assessments[0]
    # Source-grounded: evidence cited, disposition preserved (not forced).
    assert obs.supporting_evidence_ids == ["ev-1"]
    assert obs.disposition.value in {"supported", "qualified", "unverified"}
    # Unsupported assertion stays distinct from demonstrated performance: a
    # tenure-only claim must NOT be promoted to a fact.
    weak = {
        "borrower_identification": {"status": "unavailable", "limitation": "SO confirms."},
        "legal_entities": [],
        "management_assessments": [{
            "observation_id": "MO2", "management_id": "mgmt-2",
            "legal_name": "R. Patel", "role": "CEO", "dimension": "competence",
            "evidence_basis": "credentials_or_tenure", "attribution": "company_reported",
            "hypothesis": "The CEO has long tenure and strong qualifications.",
            "credit_mechanism": "Reported tenure and qualifications over many years.",
            "supporting_evidence_ids": ["ev-1"], "contradicting_evidence_ids": [],
            "supporting_evidence_investigated": True,
            "contradictory_evidence_investigated": True,
            "disposition": "supported", "admissibility": "established",
        }],
        "governance_screening": [], "information_gaps": ["gap"],
    }
    governed_weak = validate_m3_assessment(
        weak, allowed_evidence_ids=allowed, validated_borrower_entity_ids=set(),
        evidence_texts={"ev-1": "The CEO has served for 12 years."})
    assert governed_weak.management_assessments[0].disposition.value != "supported"
