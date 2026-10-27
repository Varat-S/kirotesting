"""Module 3 Architecture V6 two-output deliverables.

Module 3 emits two connected JSON artifacts:

* **Deliverable A — ``m3_corporate_structure.json``**: the primary corporate-
  structure output. It is the refined, presentation-ready v2.0 JSON (refined
  structural facts + generated presentation specifications). The deterministic
  memo renderer consumes this. It is never replaced by an SVG/PNG; the SVG is
  only an optional downstream preview.

* **Deliverable B — ``m3_analysis.json``**: the governed analytical record.
  It carries the structural credit observations, material credit signals, the
  senior-management assessment, supporting and contradictory source references,
  evidence-verification status, materiality and limitations, information gaps,
  the consolidated Module 3 conclusion, and a stable **reference** to Deliverable
  A. The cross-module synthesizer consumes this governed analytical output.

The two are linked by stable artifact identifiers plus source provenance so an
auditor can trace the analytical record to the exact structure JSON it refers
to, and the structure JSON back to the onboarding document and transform that
produced it.

Nothing here fabricates data: when no valid case-associated corporate-structure
JSON was ingested, Deliverable A is NOT produced (``None``) and Deliverable B
records ``corporate_structure_status == "unavailable"`` with the mandated
message, while the senior-management assessment still proceeds when supported.
"""

from __future__ import annotations

from typing import Any

from app.core.hashing import content_hash
from app.schemas.bo_m3_structure import (
    NO_JSON_MESSAGE,
    CorporateStructureArtifact,
)


M3_ANALYSIS_VERSION = "bo-m3-analysis-1.0"
M3_STRUCTURE_ARTIFACT_KIND = "m3_corporate_structure"
M3_ANALYSIS_ARTIFACT_KIND = "m3_analysis"


def _structure_artifact_id(structure: CorporateStructureArtifact) -> str:
    """Stable identifier for the corporate-structure deliverable.

    Derived from the (immutable) refined v2.0 payload so identical inputs yield
    identical ids and the analytical record can reference it reproducibly.
    """
    basis = {
        "kind": M3_STRUCTURE_ARTIFACT_KIND,
        "refined": structure.refined_structure,
        "document_hash": (structure.provenance.document_hash
                          if structure.provenance else None),
    }
    return f"m3struct_{content_hash(basis)[:16]}"


def build_m3_corporate_structure_export(
    structure: CorporateStructureArtifact,
) -> dict[str, Any] | None:
    """Deliverable A (export): the refined, presentation-ready v2.0 JSON AT ROOT.

    The exported ``m3_corporate_structure.json`` follows the supplied final-output
    contract directly: its root is the refined v2.0 payload
    (``schema_version``, ``artifact_type``, ``purpose``, ``how_to_use``,
    ``visual_spec``, ``companies``, ``generated_by`` and, when present,
    ``simulation_disclaimer``). It is NOT wrapped under ``refined_structure`` and
    carries no ``artifact_kind`` envelope; the governed internal record (artifact
    id + provenance + round-trip) lives in the separate sidecar.

    Returns ``None`` when no validated structure is available (fail-closed: no
    diagram is fabricated).
    """
    if structure.status != "available" or not structure.refined_structure:
        return None
    return dict(structure.refined_structure)


def build_m3_structure_sidecar(
    structure: CorporateStructureArtifact,
) -> dict[str, Any] | None:
    """Governed internal record for Deliverable A (sidecar, not the export).

    Written to ``m3_structure_provenance.json``. Keeps the stable artifact id
    (so Deliverable B's reference is unchanged), the simulation flag, source
    provenance, the onboarding round-trip reconciliation and the document hash /
    transform version, separate from the presentation-ready export so source and
    audit metadata stay in a governed record.

    Returns ``None`` when no validated structure is available (fail-closed).
    """
    if structure.status != "available" or not structure.refined_structure:
        return None
    prov = structure.provenance
    return {
        "artifact_kind": M3_STRUCTURE_ARTIFACT_KIND,
        "artifact_id": _structure_artifact_id(structure),
        "is_simulated": structure.is_simulated,
        "provenance": prov.model_dump(mode="json") if prov else None,
        "roundtrip_reconciliation": structure.roundtrip_reconciliation,
        "document_hash": prov.document_hash if prov else None,
        "transform_version": prov.transform_version if prov else None,
    }


def build_m3_corporate_structure_artifact(
    structure: CorporateStructureArtifact,
) -> dict[str, Any] | None:
    """DEPRECATED thin alias for :func:`build_m3_structure_sidecar`.

    The exported corporate-structure JSON is now the de-wrapped v2.0 payload at
    root (see :func:`build_m3_corporate_structure_export`); this builder returns
    only the governed internal record (artifact id + provenance + round-trip).
    Retained so existing imports keep resolving. Prefer the sidecar builder.
    """
    return build_m3_structure_sidecar(structure)


def _is_admitted(signal: dict[str, Any]) -> bool:
    """Whether a finding may inform the authoritative consolidated conclusion.

    Excludes a ``rejected`` disposition outright, and a material-level
    ``unverified`` finding (materiality high/medium). Structural signals have no
    ``disposition``; they are gated instead by ``assessment_availability`` and
    are already pre-filtered to 'sufficient' before reaching this check. Rejected
    and unresolved items are never dropped from the audit trail
    (evidence_verification retains them).
    """
    disposition = signal.get("disposition")
    if disposition == "rejected":
        return False
    availability = signal.get("assessment_availability")
    if availability == "insufficient_evidence":
        return False
    if disposition == "unverified" and signal.get("materiality") in {"high", "medium"}:
        return False
    return True


def build_m3_analysis_artifact(
    structure: CorporateStructureArtifact,
    governed_m3: dict[str, Any] | None,
    m3_output: dict[str, Any],
    *,
    structure_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deliverable B: the governed analytical record consumed by cross-module synthesis.

    Pulls the structural credit observations and material signals, the senior-
    management assessment (unchanged, source-grounded), information gaps, the
    consolidated conclusion, and a stable reference to Deliverable A. Credit
    direction, verification status, materiality and limitations are preserved as
    separate fields; nothing is promoted to fact and no outcome is forced.
    """
    section_a = m3_output["section_a_corporate_structure"]
    section_b = m3_output["section_b_management_commentary"]
    section_c = m3_output["section_c_conclusion"]
    completion = m3_output["completion"]

    # Only admitted findings may inform the authoritative conclusion: a rejected
    # disposition, or a material-level 'unverified', is excluded here (it is
    # still retained in full under evidence_verification). This never forces an
    # equal positive/negative count and never promotes anything to fact.
    management_signals = section_b.get("management_signals", []) or []
    governance_signals = section_b.get("governance_signals", []) or []
    structural_observations = section_a.get("structural_observations", []) or []
    material_implications = section_a.get("material_credit_implications", []) or []
    # Layer 2: validated AI structural credit findings, kept SEPARATE from the
    # deterministic observations. The full governed set is retained (with
    # dispositions/qualifications); only admitted findings inform the conclusion.
    ai_structural_findings = section_a.get("ai_structural_credit_findings", []) or []

    admitted_management = [s for s in management_signals if _is_admitted(s)]
    admitted_governance = [s for s in governance_signals if _is_admitted(s)]
    admitted_structural = [s for s in material_implications if _is_admitted(s)]
    admitted_ai_structural = [f for f in ai_structural_findings if _is_admitted(f)]

    available = structure.status == "available"
    structure_reference: dict[str, Any] = {
        "artifact_kind": M3_STRUCTURE_ARTIFACT_KIND,
        "artifact_id": (structure_artifact["artifact_id"]
                        if structure_artifact else None),
        "status": structure.status,
        "document_hash": (structure.provenance.document_hash
                          if structure.provenance else None),
        "transform_version": (structure.provenance.transform_version
                              if structure.provenance else None),
        "available": available,
        "unavailable_message": (None if available
                                else (structure.message or NO_JSON_MESSAGE)),
    }

    analysis: dict[str, Any] = {
        "schema_version": M3_ANALYSIS_VERSION,
        "artifact_kind": M3_ANALYSIS_ARTIFACT_KIND,
        "module": "M3",
        "corporate_structure_status": structure.status,
        "corporate_structure_reference": structure_reference,
        # Structural credit observations + material signals (descriptive, qualified).
        # material_credit_implications carries only ADMITTED signals; the full
        # descriptive observation set stays available for context.
        "structural_observations": structural_observations,
        "material_credit_implications": admitted_structural,
        # Validated AI structural credit findings (full governed set, analytical
        # conclusions distinct from the deterministic facts above). Rejected /
        # material-unverified items are retained here and under
        # evidence_verification, but are excluded from the conclusion below.
        "ai_structural_credit_findings": ai_structural_findings,
        "admitted_structural_credit_finding_ids": sorted(
            str(f.get("finding_id")) for f in admitted_ai_structural
            if f.get("finding_id")),
        "structural_limitations": section_a.get("limitations", []),
        # Senior-management assessment (unchanged, source-grounded): the full
        # signal sets are retained here for transparency, including qualified and
        # downgraded items. The authoritative conclusion below draws only on the
        # admitted subset.
        "senior_management_assessment": {
            "assessment_status": section_b.get("assessment_status"),
            "management_signals": management_signals,
            "governance_signals": governance_signals,
            "borrower_identification": section_b.get("borrower_identification"),
            "limitations": section_b.get("limitations", []),
            **(section_b.get("management_stability") or {}),
        },
        # Evidence/verification posture carried from the governed assessment.
        # EVERY rejected/unresolved item is retained here for audit, including
        # those excluded from the authoritative conclusion.
        "evidence_verification": {
            "validation_issues": (governed_m3 or {}).get("validation_issues", []),
            "rejected_items": (governed_m3 or {}).get("rejected_items", []),
            "excluded_from_conclusion_ids": sorted(
                str(s.get("observation_id") or s.get("finding_id") or s.get("signal_id"))
                for s in (management_signals + governance_signals
                          + material_implications + ai_structural_findings)
                if not _is_admitted(s)),
            "replay_provenance": (governed_m3 or {}).get("replay_provenance"),
        },
        "information_gaps": sorted(set(
            list((governed_m3 or {}).get("information_gaps", []))
            + list(section_b.get("information_gaps", []))
        )),
        # Authoritative conclusion: section C commentary plus the explicit
        # admitted-finding references it rests on. Rejected / material-unverified
        # findings are intentionally absent here (retained only for audit above).
        "consolidated_conclusion": {
            **section_c,
            "admitted_management_signal_ids": sorted(
                str(s.get("observation_id")) for s in admitted_management
                if s.get("observation_id")),
            "admitted_governance_signal_ids": sorted(
                str(s.get("finding_id")) for s in admitted_governance
                if s.get("finding_id")),
            "admitted_structural_signal_ids": sorted(
                str(s.get("signal_id")) for s in admitted_structural
                if s.get("signal_id")),
            "admitted_structural_credit_finding_ids": sorted(
                str(f.get("finding_id")) for f in admitted_ai_structural
                if f.get("finding_id")),
            "disposition": "human_review_required",
        },
        "completion": completion,
        "requires_human_review": True,
        "forced_outcome": False,
    }
    return analysis


def build_m3_deliverables(
    structure: CorporateStructureArtifact,
    governed_m3: dict[str, Any] | None,
    m3_output: dict[str, Any],
) -> dict[str, Any]:
    """Build both Module 3 deliverables (A + B) with stable cross-references.

    * ``m3_corporate_structure`` — Deliverable A export: the de-wrapped v2.0
      payload at root (None when unavailable).
    * ``m3_structure_sidecar`` — the governed internal record for A (artifact id
      + provenance + round-trip; None when unavailable).
    * ``m3_analysis`` — Deliverable B: the governed analytical record whose
      ``corporate_structure_reference.artifact_id`` equals the sidecar id.
    """
    structure_export = build_m3_corporate_structure_export(structure)
    structure_sidecar = build_m3_structure_sidecar(structure)
    analysis_artifact = build_m3_analysis_artifact(
        structure, governed_m3, m3_output, structure_artifact=structure_sidecar)
    return {
        "m3_corporate_structure": structure_export,     # Deliverable A export (None if unavailable)
        "m3_structure_sidecar": structure_sidecar,       # Governed internal record (None if unavailable)
        "m3_analysis": analysis_artifact,                # Deliverable B (always present)
    }
