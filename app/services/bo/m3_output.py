"""Module 3 three-section output assembly: A (structure), B (management), C.

Combines the deterministic corporate-structure artifact (Deliverable A) with the
governed narrative management/legal-entity assessment (Deliverable B) into the
Module 3 output, and derives an integrated conclusion (Section C) plus an overall
completion status.

Key behaviour from the spec and the North Star:

* Section A stays explicitly ``unavailable`` when no valid case-associated
  corporate-structure JSON exists; the diagram is never fabricated.
* A missing corporate-structure JSON does NOT block Section B when valid 10-K
  evidence is available: the module completes *partially* (A unavailable, B
  supported) and is NOT silently marked fully complete.
* Credit direction, verification and assessment availability stay in SEPARATE
  fields. Insufficient evidence is reported separately from a neutral finding.
* No positive/negative conclusion is forced; findings may be positive, negative,
  neutral or mixed.
"""

from __future__ import annotations

from typing import Any

from app.schemas.bo_m3_structure import (
    NO_JSON_MESSAGE,
    CorporateStructureArtifact,
)


M3_OUTPUT_VERSION = "bo-m3-output-1.0"


def _section_a(structure: CorporateStructureArtifact,
               svg: str | None) -> dict[str, Any]:
    available = structure.status == "available"
    section: dict[str, Any] = {
        "title": "Corporate Structure",
        "corporate_structure_status": structure.status,
    }
    if not available:
        section["display_message"] = structure.message or NO_JSON_MESSAGE
        section["diagram"] = None
        section["structural_observations"] = []
        section["material_credit_implications"] = []
        section["source_json_metadata"] = None
        section["limitations"] = structure.limitations or [
            "No validated corporate-structure diagram is available for this case."]
        section["validation_issues"] = structure.validation_issues
        return section

    prov = structure.provenance
    section["display_message"] = None
    section["diagram"] = {
        "format": "svg",
        "svg": svg,
        "renderer_version": prov.renderer_version if prov else None,
        "is_simulated": structure.is_simulated,
        "node_count": len(structure.nodes),
        "relationship_count": len(structure.links),
        "guarantor_count": len(structure.guarantor_node_ids),
        "grouping_node_ids": structure.grouping_node_ids,
        "plain_text_tree": structure.plain_text_tree,
    }
    section["source_json_metadata"] = prov.model_dump(mode="json") if prov else None
    section["structural_observations"] = [
        s.model_dump(mode="json") for s in structure.structural_signals]
    # Material implications = signals with sufficient evidence and non-low
    # materiality, carried with their qualifications (never promoted to fact).
    section["material_credit_implications"] = [
        s.model_dump(mode="json") for s in structure.structural_signals
        if s.assessment_availability == "sufficient" and s.materiality.value in {"high", "medium"}]
    section["limitations"] = structure.limitations
    section["validation_issues"] = structure.validation_issues
    section["open_questions"] = [
        s.observation for s in structure.structural_signals
        if s.assessment_availability == "insufficient_evidence"]
    return section


def _section_b(governed_m3: dict[str, Any] | None) -> dict[str, Any]:
    section: dict[str, Any] = {
        "title": "Management Commentary & Execution Credibility",
    }
    if not governed_m3:
        section["assessment_status"] = "unavailable"
        section["management_signals"] = []
        section["limitations"] = [
            "No governed management assessment is available; valid narrative "
            "evidence (e.g. Form 10-K) was not processed for this case."]
        return section

    management = governed_m3.get("management_assessments") or []
    governance = governed_m3.get("governance_screening") or []
    section["assessment_status"] = "assessed" if management or governance else "insufficient_evidence"
    section["management_signals"] = management
    section["governance_signals"] = governance
    section["information_gaps"] = governed_m3.get("information_gaps") or []
    section["borrower_identification"] = governed_m3.get("borrower_identification")
    section["limitations"] = [
        "Soundness of management commentary is assessed against admitted evidence "
        "and canonical figures; it is not a judgement of personal competence or "
        "integrity.",
    ]
    return section


def _completion_status(section_a: dict[str, Any], section_b: dict[str, Any]) -> dict[str, Any]:
    a_available = section_a["corporate_structure_status"] == "available"
    b_assessed = section_b["assessment_status"] in {"assessed", "insufficient_evidence"}
    b_supported = section_b["assessment_status"] == "assessed"

    if not a_available and not b_assessed:
        # Neither deliverable is present -> incomplete.
        status = "incomplete"
    elif a_available and b_supported:
        # Only path to 'complete': A available AND B assessed.
        status = "complete"
    elif b_assessed:
        # B proceeded (assessed or explicitly insufficient) though A is
        # unavailable -> partial, never silently full.
        status = "partial"
    else:
        # A available but B not assessed at all.
        status = "incomplete"
    return {
        "status": status,
        "corporate_structure_available": a_available,
        "management_assessment_available": b_supported,
        "note": (
            "Corporate structure unavailable; senior-management assessment "
            "completed from narrative evidence. Module is partially complete."
            if status == "partial" else None),
    }


def _section_c(section_a: dict[str, Any], section_b: dict[str, Any],
               completion: dict[str, Any]) -> dict[str, Any]:
    """Concise integrated credit commentary for a potential lender.

    Reports the factual availability of each deliverable and the mix of
    directions observed, without forcing an approve/reject stance.
    """
    points: list[str] = []
    if section_a["corporate_structure_status"] == "available":
        diagram = section_a["diagram"]
        points.append(
            f"A validated corporate-structure diagram is available "
            f"({diagram['node_count']} entities, {diagram['relationship_count']} "
            f"ownership links, {diagram['guarantor_count']} illustrative guarantor "
            f"marking(s)); structural observations are descriptive and qualified."
            + (" The structure is simulated/illustrative, not verified."
               if diagram["is_simulated"] else ""))
    else:
        points.append(
            "Corporate structure is unavailable; no structural credit observations "
            "are drawn and none are inferred.")

    b_status = section_b["assessment_status"]
    if b_status == "assessed":
        directions = sorted({
            s.get("credit_direction") for s in section_b["management_signals"]
            if s.get("credit_direction")})
        points.append(
            "Management commentary was tested against admitted evidence; observed "
            f"credit directions: {', '.join(directions) or 'none recorded'}.")
    elif b_status == "insufficient_evidence":
        points.append(
            "Management commentary could not be assessed on the admitted evidence; "
            "reported separately as insufficient evidence.")
    else:
        points.append("No management assessment is available for this case.")

    return {
        "title": "Module 3 Conclusion",
        "integrated_commentary": points,
        "completion_status": completion["status"],
        "requires_human_review": True,
        "forced_outcome": False,
        "note": "Draft analytical work for human credit-analyst review; no final "
                "credit decision is made here.",
    }


def assemble_m3_output(
    *,
    structure: CorporateStructureArtifact,
    governed_m3: dict[str, Any] | None,
    structure_svg: str | None = None,
) -> dict[str, Any]:
    """Assemble the Module 3 three-section output and completion status."""
    section_a = _section_a(structure, structure_svg)
    section_b = _section_b(governed_m3)
    completion = _completion_status(section_a, section_b)
    section_c = _section_c(section_a, section_b, completion)
    return {
        "schema_version": M3_OUTPUT_VERSION,
        "module": "M3",
        "section_a_corporate_structure": section_a,
        "section_b_management_commentary": section_b,
        "section_c_conclusion": section_c,
        "completion": completion,
    }
