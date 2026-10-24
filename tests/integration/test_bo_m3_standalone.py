"""End-to-end offline coverage of the standalone Module 3 pathway.

Runs :func:`app.services.bo.m3_standalone.run_m3` with a scripted offline
specialist response (no model call) and the simplified HCA onboarding document,
and asserts:

* the two deliverables are produced (A de-wrapped v2.0 root with 14 nodes / 13
  links / 5 guarantors; B references A by the stable sidecar id);
* :func:`app.services.bo.m3_demo.write_m3_demo` writes all six inspectable files
  (byte-stable) and the manifest records model_called + versions + source hash;
* ONLY ``m3_corporate_management`` executed — exactly one analysis run and one
  evidence packet, no other BO specialist, no orchestrator / challenger /
  synthesis / scoring / memo / downstream agent;
* missing input fails closed (A unavailable with the verbatim NO_JSON_MESSAGE, B
  still assessed, completion partial, no m3_corporate_structure.json written);
* a wrong case association fails closed (rejected);
* the exported structure is byte-reproducible across independent runs; and
* material analytical instability across repeated runs is REPORTED, not silently
  reconciled.

Everything here is offline; conftest forbids live SEC I/O and disables .env.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.core.hashing import canonical_json, content_hash
from app.models.base import create_engine_and_session, init_db
from app.models.orm import (
    AgentRunRow,
    AgenticAnalysisRun,
    AuditEvent,
    EvidencePacketRow,
)
from app.schemas.bo_m3_structure import NO_JSON_MESSAGE
from app.services.bo import m3_demo
from app.services.bo.completion import (
    admit_corporate_structure_source,
    build_m3_corporate_structure,
)
from app.services.bo.m3_standalone import run_m3
from tests import bo_helpers as h


SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")
REFERENCE = Path("examples/bo_m3/Sample HCA Corp Structure Final Output.json")

# Event types that would betray a cross-module or downstream agent having run.
FORBIDDEN_AUDIT_EVENTS = {
    "bo_orchestrator_executed", "bo_challenge_executed", "bo_synthesis_executed",
    "bo_scoring_executed", "bo_memo_rendered", "bo_handoff_built",
    "bo_m1s2_mode_executed", "agent6_executed",
}


def _simplified() -> dict:
    return json.loads(SIMPLIFIED.read_text(encoding="utf-8"))


def _ev(phrase):
    return {"$evidence": phrase}


def _management(**over):
    base = {
        "observation_id": "MO1", "management_id": "board",
        "legal_name": "Board of Directors", "role": "Board",
        "effective_from": None, "effective_to": None, "dimension": "competence",
        "evidence_basis": "reported_experience", "attribution": "company_reported",
        "assurance_scope": None, "assurance_date": None,
        "hypothesis": "The board oversees governance and succession planning.",
        "credit_mechanism": "Board oversight of succession may reduce leadership continuity risk.",
        "supporting_evidence_ids": [_ev("executive officers")],
        "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "qualified", "admissibility": "qualified_narrative",
        "confidence": "medium", "materiality": "medium", "missing_information": [],
    }
    base.update(over)
    return base


def _single_pass(management=None):
    return {
        "parameters": [],
        "hypotheses": [h.hyp(
            "M3-H001", "The board oversees governance and succession planning.",
            "fact", "neutral", "supported", support=["executive officers"],
            quotes=[("executive officers",
                     "The board of directors oversees governance and succession "
                     "planning")])],
        "m3_assessment": {
            "schema_version": "bo-m3-corporate-assessment-1.0",
            "borrower_identification": {
                "status": "unavailable", "entity_id": None, "evidence_ids": [],
                "limitation": "No proposed borrowing entity is identified."},
            "legal_entities": [], "ownership_relationships": [],
            "cash_flow_locations": [], "cash_transfer_relationships": [],
            "structural_credit_findings": [],
            "management_assessments": [management or _management()],
            "governance_screening": [],
            "information_gaps": ["Management outcomes are not disclosed."],
            "downstream_dependencies": [],
        },
    }


def _responses(management=None):
    return {"single_pass": _single_pass(management)}


def _fresh_session():
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return factory()


def _run(session, *, document=_simplified, case_id=h.CASE, management=None):
    return run_m3(
        session, case_id=case_id, evidence=h.evidence_snapshot(),
        metrics=h.metrics(), m3_responses=_responses(management),
        corporate_structure_document=document() if callable(document) else document)


# -- the two deliverables + demo files ---------------------------------------


def test_standalone_produces_both_deliverables_and_six_demo_files(db_session, tmp_path):
    result = _run(db_session)

    # Deliverable A: de-wrapped v2.0 payload at root.
    export = result.m3_corporate_structure
    assert export is not None
    assert {"schema_version", "artifact_type", "visual_spec", "companies"} <= set(export)
    assert "refined_structure" not in export and "artifact_kind" not in export
    assert export["schema_version"] == "2.0"
    company = next(iter(export["companies"].values()))
    assert len(company["nodes"]) == 14
    assert len(company["ownership_links"]) == 13
    assert sum(1 for n in company["nodes"] if n.get("is_guarantor")) == 5

    # Deliverable B references A by the stable sidecar id.
    assert result.m3_structure_sidecar is not None
    assert result.m3_analysis["corporate_structure_reference"]["artifact_id"] == \
        result.m3_structure_sidecar["artifact_id"]
    assert result.completion_status == "complete"

    # All six demo files are written.
    paths = m3_demo.write_m3_demo(
        result, tmp_path, simplified_document=_simplified(),
        reference_output=json.loads(REFERENCE.read_text()),
        corporate_structure_path=str(SIMPLIFIED), responses_path="resp.json")
    for key in ("m3_corporate_structure", "m3_structure_provenance", "m3_analysis",
                "validation_report", "evaluation_scorecard", "run_manifest"):
        assert key in paths and paths[key].exists()

    manifest = json.loads(paths["run_manifest"].read_text())
    assert manifest["execution"]["model_called"] is False
    assert manifest["cross_module_or_downstream_invoked"] is False
    assert manifest["validator_id"] == "validator_7"
    assert manifest["versions"]["roster_version"]
    assert manifest["versions"]["transform_version"]
    assert manifest["source_documents"]["corporate_structure"]["document_hash"]
    assert manifest["reproduce_command"].startswith("DEBUG=false ")
    assert "run-m3" in manifest["reproduce_command"]

    scorecard = json.loads(paths["evaluation_scorecard"].read_text())
    assert scorecard["actual_llm_semantic_accuracy"] == "not_measured"
    assert scorecard["completeness_caveat"]
    counts = scorecard["corporate_structure_accuracy"]["count_accuracy"]
    assert counts["denominator"] > 0 and counts["numerator"] == counts["denominator"]


def test_only_m3_corporate_management_executed(db_session):
    result = _run(db_session)

    runs = db_session.query(AgenticAnalysisRun).all()
    assert len(runs) == 1
    assert runs[0].analysis_mode == "agentic_bo_m3_standalone"
    assert runs[0].analysis_run_id == result.analysis_run_id

    packets = db_session.query(EvidencePacketRow).all()
    assert {p.agent_id for p in packets} == {"m3_corporate_management"}
    assert len(packets) == 1

    # No other specialist executed as an agent run.
    agent_ids = {r.agent_id for r in db_session.query(AgentRunRow).all()}
    assert agent_ids <= {"m3_corporate_management"}

    # No cross-module / downstream agent audit events were recorded.
    event_types = {e.event_type for e in db_session.query(AuditEvent).all()}
    assert "bo_m3_standalone_executed" in event_types
    assert not (event_types & FORBIDDEN_AUDIT_EVENTS)
    assert result.run_manifest["scope"] == "M3_only"
    assert result.run_manifest["cross_module_or_downstream_invoked"] is False


# -- fail-closed inputs -------------------------------------------------------


def test_missing_corporate_structure_fails_closed(db_session, tmp_path):
    result = _run(db_session, document=None)

    assert result.corporate_structure_status == "unavailable"
    assert result.m3_corporate_structure is None
    assert result.completion_status == "partial"
    reference = result.m3_analysis["corporate_structure_reference"]
    assert reference["unavailable_message"] == NO_JSON_MESSAGE
    # Section B still assessed from the admitted narrative evidence.
    assert result.m3_output["section_b_management_commentary"][
        "assessment_status"] == "assessed"

    paths = m3_demo.write_m3_demo(result, tmp_path)
    # Deliverable A and its sidecar are NOT written (fail closed); B still is.
    assert "m3_corporate_structure" not in paths
    assert not (tmp_path / "m3_corporate_structure.json").exists()
    assert "m3_structure_provenance" not in paths
    assert (tmp_path / "m3_analysis.json").exists()


def test_wrong_case_association_fails_closed(db_session):
    # Admit the onboarding document for one case, then attempt to build the
    # structure under a DIFFERENT case: the seam never auto-selects a case.
    src = admit_corporate_structure_source(
        _simplified(), case_id="CASE_A", source_document_id="d1")
    agents = [{"agent_id": "m3_corporate_management", "code": "M3",
               "status": "validated", "coverage": [],
               "calls": [{"inputs": {"corporate_structure_source": src}}]}]
    artifact, svg = build_m3_corporate_structure(agents, case_id="CASE_B")
    assert artifact.status == "rejected"
    assert svg is None


# -- reproducibility + analytical consistency ---------------------------------


def test_exported_structure_is_byte_reproducible(tmp_path):
    first = _run(_fresh_session())
    second = _run(_fresh_session())
    # Byte-identical de-wrapped export across independent runs over the same JSON.
    assert canonical_json(first.m3_corporate_structure) == \
        canonical_json(second.m3_corporate_structure)

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    paths_a = m3_demo.write_m3_demo(first, out_a, simplified_document=_simplified())
    paths_b = m3_demo.write_m3_demo(second, out_b, simplified_document=_simplified())
    assert paths_a["m3_corporate_structure"].read_bytes() == \
        paths_b["m3_corporate_structure"].read_bytes()


def _material_fingerprint(result) -> dict:
    """The governed MATERIAL surface that must be stable for identical inputs."""
    governed = result.governed_assessment or {}
    analysis = result.m3_analysis
    return {
        "management": [{
            "id": m.get("observation_id"), "disposition": m.get("disposition"),
            "credit_direction": m.get("credit_direction"),
            "supporting_evidence_ids": m.get("supporting_evidence_ids"),
            "materiality": m.get("materiality"),
        } for m in governed.get("management_assessments") or []],
        "validation_issues": sorted(i.get("rule_id") for i in result.validation_issues),
        "rejected_items": len(result.rejected_items),
        "completion_status": result.completion_status,
        "structure_hash": content_hash(result.m3_corporate_structure),
        "admitted_conclusion": analysis["consolidated_conclusion"][
            "admitted_management_signal_ids"],
    }


def test_repeated_identical_runs_are_materially_identical():
    first = _run(_fresh_session())
    second = _run(_fresh_session())
    assert _material_fingerprint(first) == _material_fingerprint(second)


def test_materially_different_proposal_is_reported_not_reconciled():
    # First run: a supported board observation. Second run: the SAME observation
    # id now asserts a specific revenue figure that is ABSENT from the cited
    # evidence (which carries no number), so Validator 7 governs it to
    # 'unverified' — UNSUPPORTED, not contradicted: absence of support is not
    # contradiction (Priority 2). The material claim is still kept OUT of the
    # authoritative conclusion and retained in the audit trail.
    stable = _run(_fresh_session())
    unstable = _run(
        _fresh_session(),
        management=_management(
            disposition="supported",
            hypothesis="Revenue grew 63% in FY2024 under current management.",
            credit_mechanism="The claimed growth is treated as proof of execution."))

    first_fp = _material_fingerprint(stable)
    second_fp = _material_fingerprint(unstable)
    # The comparison surfaces the instability rather than silently reconciling it.
    assert first_fp != second_fp
    differing = [k for k in first_fp if first_fp[k] != second_fp[k]]
    assert "management" in differing
    # The materially different claim is governed to UNSUPPORTED (unverified),
    # NOT contradicted/rejected, and is excluded from the authoritative
    # conclusion, yet retained for audit.
    unstable_obs = unstable.governed_assessment["management_assessments"][0]
    assert unstable_obs["disposition"] == "unverified"
    assert "MO1" not in unstable.m3_analysis["consolidated_conclusion"][
        "admitted_management_signal_ids"]
    assert "MO1" in unstable.m3_analysis["evidence_verification"][
        "excluded_from_conclusion_ids"]
