"""Deterministic selection policy for hybrid BO specialist investigation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


HYBRID_SELECTION_POLICY_VERSION = "bo-hybrid-selection-1"
_MATERIAL = frozenset({"high", "medium"})


@dataclass(frozen=True)
class HybridCandidate:
    hypothesis_id: str
    claim: str
    dimension: str
    direction: str
    materiality: str
    claim_type: str
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    confidence: str = "medium"
    demonstrated_mitigant: bool = False
    provenance_ambiguous: bool = False
    coverage_gap: bool = False
    deterministic_valid: bool = True


@dataclass(frozen=True)
class HybridSelection:
    hypothesis_id: str
    investigate: bool
    reasons: tuple[str, ...]
    duplicate_of: str | None = None
    policy_version: str = HYBRID_SELECTION_POLICY_VERSION


@dataclass
class HybridSelectionResult:
    decisions: list[HybridSelection] = field(default_factory=list)

    @property
    def selected_ids(self) -> tuple[str, ...]:
        return tuple(d.hypothesis_id for d in self.decisions if d.investigate)


class InvestigationMode(str, Enum):
    SINGLE_PASS = "single_pass"
    FULL = "full_investigation"
    HYBRID = "hybrid_selective"


@dataclass(frozen=True)
class HybridInput:
    candidate: HybridCandidate
    payload: Any


@dataclass
class HybridExecutionResult:
    mode: InvestigationMode
    outputs: list[Any]
    selection: HybridSelectionResult
    lineage: list[dict[str, Any]]
    policy_version: str = HYBRID_SELECTION_POLICY_VERSION


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _near_duplicate(a: str, b: str) -> bool:
    left, right = _tokens(a), _tokens(b)
    return bool(left and right and len(left & right) / len(left | right) >= 0.82)


def select_for_investigation(
    candidates: list[HybridCandidate],
) -> HybridSelectionResult:
    """Select claims that need scrutiny, including weak material claims.

    Selection is intentionally risk-sensitive.  A claim does not need to look
    convincing to qualify: absent support and failed deterministic validation
    are positive selection signals because they warrant an audit trail.
    """
    decisions: list[HybridSelection] = []
    canonical: list[HybridCandidate] = []
    for candidate in candidates:
        duplicate = next((prior for prior in canonical
                          if prior.dimension == candidate.dimension
                          and _near_duplicate(prior.claim, candidate.claim)), None)
        if duplicate is not None:
            decisions.append(HybridSelection(
                candidate.hypothesis_id, False, ("deduplicated",), duplicate.hypothesis_id))
            continue
        canonical.append(candidate)
        reasons: list[str] = []
        material = candidate.materiality in _MATERIAL
        if material and candidate.direction == "negative":
            reasons.append("material_adverse_risk")
        if candidate.direction == "positive" and candidate.demonstrated_mitigant:
            reasons.append("claimed_demonstrated_mitigant")
        if candidate.claim_type in {"inference", "assumption"}:
            reasons.append("inferential_or_assumed_mechanism")
        if candidate.contradicting_evidence_ids:
            reasons.append("conflicting_evidence")
        if candidate.provenance_ambiguous or not candidate.supporting_evidence_ids:
            reasons.append("weak_or_ambiguous_provenance")
        if candidate.coverage_gap:
            reasons.append("material_scope_coverage_gap")
        if material and candidate.confidence in {"low", "medium_low"}:
            reasons.append("low_confidence_potentially_material")
        if material and not candidate.deterministic_valid:
            reasons.append("failed_deterministic_validation")
        decisions.append(HybridSelection(candidate.hypothesis_id, bool(reasons),
                                         tuple(dict.fromkeys(reasons))))
    return HybridSelectionResult(decisions)


def execute_investigation_mode(
    mode: InvestigationMode,
    broad_candidates: list[HybridInput],
    investigate: Callable[[list[HybridInput]], list[Any]],
    *,
    output_id: Callable[[Any], str],
) -> HybridExecutionResult:
    """Execute single, full or selective investigation with one output per id.

    The caller owns model execution.  This service owns deterministic selection,
    merging and lineage.  Selective results replace their broad candidates;
    unselected candidates remain in the authoritative specialist output.
    """
    if mode is InvestigationMode.SINGLE_PASS:
        selected = HybridSelectionResult([
            HybridSelection(item.candidate.hypothesis_id, False, ("single_pass_mode",))
            for item in broad_candidates])
        investigated: list[Any] = []
    elif mode is InvestigationMode.FULL:
        selected = HybridSelectionResult([
            HybridSelection(item.candidate.hypothesis_id, True, ("full_investigation_mode",))
            for item in broad_candidates])
        investigated = investigate(broad_candidates)
    else:
        selected = select_for_investigation([item.candidate for item in broad_candidates])
        ids = set(selected.selected_ids)
        investigated = investigate(
            [item for item in broad_candidates if item.candidate.hypothesis_id in ids])

    replacements = {output_id(item): item for item in investigated}
    outputs = [replacements.get(item.candidate.hypothesis_id, item.payload)
               for item in broad_candidates]
    lineage = [{
        "hypothesis_id": decision.hypothesis_id,
        "source": "selective_investigation" if decision.hypothesis_id in replacements
                  else "broad_single_pass",
        "investigate": decision.investigate,
        "selection_reasons": list(decision.reasons),
        "duplicate_of": decision.duplicate_of,
        "selection_policy_version": decision.policy_version,
    } for decision in selected.decisions]
    return HybridExecutionResult(mode, outputs, selected, lineage)
