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


def build_m3_corporate_structure_artifact(
    structure: CorporateStructureArtifact,
) -> dict[str, Any] | None:
    """Deliverable A: the refined, presentation-ready corporate-structure JSON.

    Returns ``None`` when no validated structure is available (fail-closed: no
    diagram is fabricated). The returned object is the refined v2.0 payload with
    a thin audit envelope (artifact id + provenance + round-trip record).
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
        "refined_structure": structure.refined_structure,
    }


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
        "structural_observations": section_a.get("structural_observations", []),
        "material_credit_implications": section_a.get("material_credit_implications", []),
        "structural_limitations": section_a.get("limitations", []),
        # Senior-management assessment (unchanged, source-grounded).
        "senior_management_assessment": {
            "assessment_status": section_b.get("assessment_status"),
            "management_signals": section_b.get("management_signals", []),
            "governance_signals": section_b.get("governance_signals", []),
            "borrower_identification": section_b.get("borrower_identification"),
            "limitations": section_b.get("limitations", []),
        },
        # Evidence/verification posture carried from the governed assessment.
        "evidence_verification": {
            "validation_issues": (governed_m3 or {}).get("validation_issues", []),
            "rejected_items": (governed_m3 or {}).get("rejected_items", []),
            "replay_provenance": (governed_m3 or {}).get("replay_provenance"),
        },
        "information_gaps": sorted(set(
            list((governed_m3 or {}).get("information_gaps", []))
            + list(section_b.get("information_gaps", []))
        )),
        "consolidated_conclusion": section_c,
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
    """Build both Module 3 deliverables (A + B) with stable cross-references."""
    structure_artifact = build_m3_corporate_structure_artifact(structure)
    analysis_artifact = build_m3_analysis_artifact(
        structure, governed_m3, m3_output, structure_artifact=structure_artifact)
    return {
        "m3_corporate_structure": structure_artifact,  # Deliverable A (None if unavailable)
        "m3_analysis": analysis_artifact,               # Deliverable B (always present)
    }
