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
               svg: str | None,
               governed_m3: dict[str, Any] | None = None) -> dict[str, Any]:
    available = structure.status == "available"
    # Validated AI structural credit findings (Layer 2) are kept VISIBLY SEPARATE
    # from the deterministic observations so factual structural facts and
    # analytical conclusions are never conflated. The full governed set is
    # carried (dispositions/qualifications retained); the authoritative
    # conclusion downstream admits only accepted / appropriately-qualified items.
    ai_findings = ((governed_m3 or {}).get("structural_credit_findings") or [])
    section: dict[str, Any] = {
        "title": "Corporate Structure",
        "corporate_structure_status": structure.status,
    }
    if not available:
        section["display_message"] = structure.message or NO_JSON_MESSAGE
        section["diagram"] = None
        section["structural_observations"] = []
        section["ai_structural_credit_findings"] = ai_findings
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
    section["ai_structural_credit_findings"] = ai_findings
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
    if governed_m3.get("management_stability") is not None:
        from app.services.bo.m3_management import stability_view
        section["title"] = "Senior Management Stability, Continuity & Relevant Experience"
        section["management_stability"] = stability_view(governed_m3["management_stability"], management)
        section["assessment_status"] = "assessed" if any(
            p["profile_status"] == "verified" for p in section["management_stability"]["key_executive_details"]
        ) else "insufficient_evidence"
        section["limitations"] = section["management_stability"]["overall_management_stability_assessment"]["limitations"]
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


# -- Priority 1: a deterministic, schema-driven conclusion assembler ---------
#
# The consolidated conclusion must EXPLAIN each material accepted/appropriately-
# qualified finding (why it matters to a lender) rather than list finding ids or
# positive/negative counts. It draws only on findings that survive Validator 7
# (the same `_is_admitted` gate the analytical record uses), preserves the
# credibility-vs-direction separation, keeps simulated-structure commentary
# explicitly hypothetical, and never nets positive and negative into a score or
# a lending recommendation. Each emitted statement carries the governed finding
# id it rests on so the trace from prose -> finding is direct.

# Why a given structural finding matters to a lender, keyed on the analytical
# dimension + credit direction. These are generic credit MECHANISMS, phrased as
# possibilities ("may"), never as established facts or a recommendation.
_STRUCTURAL_LENDER_RELEVANCE: dict[tuple[str, str], str] = {
    ("guarantor_coverage", "positive"): (
        "A guarantee from this entity may improve the lender's recourse by giving "
        "a direct claim where operations or assets sit, mitigating structural "
        "subordination — subject to the guarantee's enforceability, ranking, caps "
        "and any statutory or contractual limitations."),
    ("guarantor_coverage", "negative"): (
        "Reliance on this entity's coverage may weaken the lender's recourse if the "
        "guarantee is unenforceable, capped or subordinated; enforceability and "
        "ranking require confirmation."),
    ("cash_flow_access", "negative"): (
        "A restriction on upstream distributions may reduce the borrower's financial "
        "flexibility and constrain the cash resources available to service the "
        "proposed facility, since operating cash can be trapped at the subsidiary."),
    ("cash_flow_access", "positive"): (
        "Demonstrated upstream cash access may support the borrower's ability to "
        "service the proposed facility — subject to the durability of that access "
        "and any distribution restrictions."),
    ("borrower_position", "negative"): (
        "The borrower's position in the structure may expose the facility to "
        "structural subordination relative to creditors closer to the operating "
        "assets."),
    ("minority_or_jv", "negative"): (
        "Minority or joint-venture ownership may limit the borrower's control over "
        "distributions and governance, which can affect cash available for debt "
        "service — only where a defensible mechanism and evidence support it."),
    ("structural_complexity", "negative"): (
        "Structural complexity or holding-company depth may complicate recourse and "
        "the flow of cash to the borrower — only where a defensible mechanism and "
        "evidence support it, not from entity count alone."),
}
_STRUCTURAL_LENDER_RELEVANCE_DEFAULT = (
    "This structural observation may bear on the lender's recourse or the "
    "borrower's cash resources; its credit relevance requires human review.")

# Why a management finding matters to a lender, by credit direction.
_MANAGEMENT_LENDER_RELEVANCE: dict[str, str] = {
    "positive": (
        "Demonstrated management execution may support confidence in the borrower's "
        "ability to deliver its plan and service the facility — subject to the "
        "strength of the underlying outcome evidence."),
    "negative": (
        "This development is credit-negative for the borrower regardless of how "
        "credibly management explains it; a credible explanation does not reverse "
        "the adverse direction."),
    "mixed": (
        "This observation carries both supportive and adverse implications for the "
        "lender, which are preserved separately rather than netted."),
    "neutral": (
        "This observation is relevant context for management execution but does not, "
        "on the admitted evidence, point in a positive or negative credit direction."),
}


def _dedupe(items) -> list[str]:
    """Stable de-duplication preserving first-seen order (byte-stable)."""
    seen: dict[str, None] = {}
    for item in items or []:
        if item and item not in seen:
            seen[item] = None
    return list(seen)


def _finding_statement(finding: dict[str, Any], *, pillar: str,
                       is_simulated: bool) -> dict[str, Any]:
    """Compose ONE structured conclusion statement from an admitted finding.

    Preserves the core observation, credit direction, credit-risk mechanism,
    materiality, supporting evidence references, qualifications and unresolved
    gaps, and adds a ``why_it_matters_to_a_lender`` sentence. Carries the
    governed source id (``finding_id``/``observation_id``) for a direct trace.
    Structural statements derived from a SIMULATED hierarchy are explicitly
    marked hypothetical and must not assert real debts/guarantees/restrictions.
    """
    direction = finding.get("credit_direction") or "neutral"
    if pillar == "A":
        source_id = finding.get("finding_id")
        dimension = finding.get("analytical_dimension") or "borrower_position"
        core = (finding.get("structural_observation")
                or finding.get("credit_hypothesis") or "")
        mechanism = finding.get("causal_mechanism") or ""
        why = _STRUCTURAL_LENDER_RELEVANCE.get(
            (dimension, direction), _STRUCTURAL_LENDER_RELEVANCE_DEFAULT)
    else:
        source_id = finding.get("observation_id")
        dimension = finding.get("dimension")
        core = finding.get("hypothesis") or ""
        mechanism = finding.get("credit_mechanism") or ""
        why = _MANAGEMENT_LENDER_RELEVANCE.get(
            direction, _MANAGEMENT_LENDER_RELEVANCE["neutral"])
        if dimension in {"leadership_continuity", "relevant_experience", "transition_risk"}:
            why = mechanism + " This is a qualified continuity implication, not proof of management competence."

    qualifications = _dedupe(finding.get("qualifications"))
    if is_simulated and pillar == "A":
        qualifications = _dedupe([
            "Derived from a SIMULATED corporate hierarchy: this is a hypothetical, "
            "illustrative example and does not assert a real, confirmed arrangement.",
            *qualifications])

    statement: dict[str, Any] = {
        "pillar": pillar,
        "source_id": source_id,
        "analytical_dimension": dimension,
        "core_observation": core,
        "credit_direction": direction,
        "credit_risk_mechanism": mechanism,
        "materiality": finding.get("materiality"),
        "disposition": finding.get("disposition"),
        "supporting_evidence_ids": list(finding.get("supporting_evidence_ids") or []),
        "qualifications": qualifications,
        "unresolved_information_gaps": _dedupe(finding.get("missing_information")),
        "why_it_matters_to_a_lender": why,
        "hypothetical_simulated_basis": bool(is_simulated and pillar == "A"),
    }
    note = finding.get("credibility_vs_direction_note")
    if note:
        statement["credibility_vs_direction_note"] = note
    return statement


def _pillar_summary(statements: list[dict[str, Any]], *, pillar_label: str,
                    directions: list[str]) -> str:
    """A short human-readable pillar summary that references WHY, not id lists."""
    pos = [s for s in statements if s["credit_direction"] == "positive"]
    neg = [s for s in statements if s["credit_direction"] == "negative"]
    mixed = [s for s in statements if s["credit_direction"] == "mixed"]
    neutral = [s for s in statements if s["credit_direction"] == "neutral"]
    parts: list[str] = []
    if pos:
        parts.append("potentially supportive factor(s) for lender recourse or "
                     "repayment resources")
    if neg:
        parts.append("factor(s) that may weaken recourse or constrain repayment "
                     "resources")
    if mixed:
        parts.append("factor(s) carrying both supportive and adverse implications")
    if neutral and not (pos or neg or mixed):
        parts.append("context-only factor(s) with no positive or negative "
                     "credit direction established")
    if not parts:
        return (f"{pillar_label}: no material finding survived validation; nothing "
                "is admitted into the authoritative conclusion.")
    joined = "; ".join(parts)
    note = ""
    if pos and neg:
        note = (" Supportive and adverse factors are preserved separately and are "
                "not netted into a single score.")
    return (f"{pillar_label}: {joined}. Each is explained below with its credit "
            f"mechanism, evidence and qualifications." + note)


def _section_c(section_a: dict[str, Any], section_b: dict[str, Any],
               completion: dict[str, Any]) -> dict[str, Any]:
    """Meaningful integrated credit commentary for a potential lender.

    Draws ONLY on findings that survive Validator 7 (``_is_admitted``) and, for
    each material admitted finding, EXPLAINS the core observation, credit
    direction, credit-risk mechanism, materiality, supporting evidence,
    qualifications, unresolved gaps and why it matters to a lender — for both
    Pillar A (corporate structure & structural credit risk) and Pillar B (senior
    management & execution credibility). It never lists bare ids or pos/neg
    counts, never nets findings into a score, keeps simulated-structure
    commentary hypothetical, and forces no approve/reject stance.
    """
    # Local import avoids a circular import at module load (m3_artifacts imports
    # nothing from here, but keep the admission gate as the single source).
    from app.services.bo.m3_artifacts import _is_admitted

    available = section_a["corporate_structure_status"] == "available"
    is_simulated = bool(available and (section_a.get("diagram") or {}).get(
        "is_simulated"))

    # Pillar A: admitted AI structural credit findings (material only). Rejected
    # / material-unverified / insufficient-evidence findings never contribute.
    ai_findings = section_a.get("ai_structural_credit_findings") or []
    admitted_a = [
        f for f in ai_findings
        if _is_admitted(f) and f.get("materiality") in {"high", "medium"}]
    admitted_a = sorted(admitted_a, key=lambda f: str(f.get("finding_id") or ""))

    # Pillar B: admitted management + governance signals (material only).
    management = section_b.get("management_signals") or []
    governance = section_b.get("governance_signals") or []
    admitted_b_mgmt = sorted(
        (s for s in management
         if _is_admitted(s) and s.get("materiality") in {"high", "medium"}),
        key=lambda s: str(s.get("observation_id") or ""))
    admitted_b_gov = sorted(
        (s for s in governance
         if _is_admitted(s) and s.get("materiality") in {"high", "medium"}),
        key=lambda s: str(s.get("finding_id") or ""))

    pillar_a_statements = [
        _finding_statement(f, pillar="A", is_simulated=is_simulated)
        for f in admitted_a]
    pillar_b_statements = [
        _finding_statement(s, pillar="B", is_simulated=is_simulated)
        for s in admitted_b_mgmt]
    # Governance screening reuses the structural-finding shape (finding_id).
    pillar_b_statements += [
        _finding_statement(s, pillar="B", is_simulated=is_simulated)
        for s in admitted_b_gov]

    # Pillar A narrative lead-in (structure availability + what can/cannot be said).
    pillar_a_summary: str
    if not available:
        pillar_a_summary = (
            "Pillar A — Corporate Structure: the corporate-structure diagram is "
            "unavailable, so no structural credit observation can be drawn and none "
            "is inferred. What cannot be established: borrower position, guarantor "
            "coverage and upstream cash access are all unknown on the admitted "
            "evidence.")
    elif not admitted_a:
        diagram = section_a["diagram"]
        pillar_a_summary = (
            f"Pillar A — Corporate Structure: a validated ownership hierarchy is "
            f"available ({diagram['node_count']} entities, "
            f"{diagram['relationship_count']} ownership links), but no debt, "
            f"guarantee or cash-distribution finding is sufficiently supported to "
            f"enter the authoritative conclusion. What CAN be established: the "
            f"ownership hierarchy and control relationships. What CANNOT: whether "
            f"any entity guarantees the facility, and whether upstream cash is "
            f"restricted — these are not evidenced here (insufficient evidence is "
            f"not a neutral finding)."
            + (" The hierarchy is simulated/illustrative, not a verified structure."
               if is_simulated else ""))
    else:
        pillar_a_summary = _pillar_summary(
            pillar_a_statements,
            pillar_label="Pillar A — Corporate Structure & structural credit risk",
            directions=[s["credit_direction"] for s in pillar_a_statements])
        if is_simulated:
            pillar_a_summary += (
                " NOTE: the hierarchy is SIMULATED; every structural finding below "
                "is hypothetical and does not assert a real arrangement.")

    # Pillar B narrative lead-in.
    b_status = section_b["assessment_status"]
    if section_b.get("management_stability"):
        pillar_b_summary = "Pillar B — Senior Management Stability & Continuity: " + section_b["management_stability"]["consolidated_management_stability_conclusion"]["summary"]
        if admitted_b_gov:
            pillar_b_summary += " Independent governance observations remain separate from the leadership-stability conclusion."
    elif b_status == "assessed" and pillar_b_statements:
        pillar_b_summary = _pillar_summary(
            pillar_b_statements,
            pillar_label=("Pillar B — Senior Management Stability & Continuity" if section_b.get("management_stability")
                          else "Pillar B — Senior Management & execution credibility"),
            directions=[s["credit_direction"] for s in pillar_b_statements])
    elif b_status == "assessed":
        pillar_b_summary = (
            "Pillar B — Senior Management: management commentary was assessed against "
            "admitted evidence, but no material observation was sufficiently supported "
            "to enter the authoritative conclusion.")
    elif b_status == "insufficient_evidence":
        pillar_b_summary = (
            "Pillar B — Senior Management: management commentary could not be assessed "
            "on the admitted evidence and is reported separately as insufficient "
            "evidence (distinct from a neutral finding).")
    else:
        pillar_b_summary = (
            "Pillar B — Senior Management: no management assessment is available for "
            "this case.")

    # Legacy-compatible flat commentary bullets: the two pillar summaries plus a
    # one-line why-it-matters per admitted statement (NOT an id list).
    integrated_commentary: list[str] = [pillar_a_summary]
    for statement in pillar_a_statements:
        integrated_commentary.append(
            f"[{statement['credit_direction']}] {statement['core_observation']} "
            f"{statement['why_it_matters_to_a_lender']}")
    integrated_commentary.append(pillar_b_summary)
    for statement in pillar_b_statements:
        integrated_commentary.append(
            f"[{statement['credit_direction']}] {statement['core_observation']} "
            f"{statement['why_it_matters_to_a_lender']}")

    return {
        "title": "Module 3 Conclusion",
        "integrated_commentary": integrated_commentary,
        # Structured, traceable pillars (each statement carries its source id).
        "pillar_a_structural_credit": {
            "summary": pillar_a_summary,
            "statements": pillar_a_statements,
        },
        "pillar_b_senior_management": {
            "summary": pillar_b_summary,
            "statements": pillar_b_statements,
        },
        "conclusion_trace": {
            "pillar_a_source_ids": [s["source_id"] for s in pillar_a_statements
                                    if s["source_id"]],
            "pillar_b_source_ids": [s["source_id"] for s in pillar_b_statements
                                    if s["source_id"]],
        },
        "completion_status": completion["status"],
        "requires_human_review": True,
        "forced_outcome": False,
        "is_lending_recommendation": False,
        "note": "Draft analytical work for human credit-analyst review; no final "
                "credit decision or lending recommendation is made here.",
    }


def assemble_m3_output(
    *,
    structure: CorporateStructureArtifact,
    governed_m3: dict[str, Any] | None,
    structure_svg: str | None = None,
) -> dict[str, Any]:
    """Assemble the Module 3 three-section output and completion status."""
    section_a = _section_a(structure, structure_svg, governed_m3)
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
