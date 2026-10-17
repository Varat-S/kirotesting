"""Step 11 presentation class: Display / On Demand / Suppress (deterministic).

The class decides where a finding appears (memo body, on-demand detail, audit
trail only). It is SEPARATE from verification status, credit direction and
materiality: direction, confidence, citation count and any label a model
proposes are deliberately not inputs. A model's proposal is recorded next to
the decision for audit, never used.

Suppressed findings are not deleted. They remain in ``hypothesis_records`` with
the policy version and reason, and the audit report lists them.

Policy table: ``BO_ARCHITECTURE_V3_SPEC.md`` §5. Changing a rule changes
``PRESENTATION_POLICY_VERSION``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.agentic import AcceptanceState, Materiality
from app.schemas.bo import Admissibility, Disposition, DisplayPriority

PRESENTATION_POLICY_VERSION = "presentation-policy-1"

# Where materiality came from. Only "rubric" is deterministic; a model-proposed
# materiality (single-pass path) is recorded as such so a reader can tell.
MATERIALITY_BASES = ("rubric", "model_proposed", "model_proposed_capped")


@dataclass(frozen=True)
class PresentationDecision:
    presentation: DisplayPriority
    rule: str
    open_question: bool
    basis: dict

    def as_record(self) -> dict:
        return {"policy_version": PRESENTATION_POLICY_VERSION, "rule": self.rule,
                "open_question": self.open_question, **self.basis}


def classify_presentation(
    *,
    disposition: Disposition,
    admissibility: Admissibility,
    materiality: Materiality,
    acceptance_state: AcceptanceState = AcceptanceState.ACCEPTED,
    materiality_basis: str = "model_proposed",
    model_proposed: str | None = None,
) -> PresentationDecision:
    """Apply the versioned policy; first matching rule wins."""
    if materiality_basis not in MATERIALITY_BASES:
        raise ValueError(f"Unknown materiality basis {materiality_basis!r}.")
    basis = {
        "inputs": {
            "disposition": disposition.value,
            "admissibility": admissibility.value,
            "materiality": materiality.value,
            "acceptance_state": acceptance_state.value,
        },
        "materiality_basis": materiality_basis,
        "model_proposed": model_proposed,
    }
    material = materiality in (Materiality.HIGH, Materiality.MEDIUM)

    def decide(cls: DisplayPriority, rule: str, open_question: bool = False):
        return PresentationDecision(cls, rule, open_question, basis)

    if acceptance_state is not AcceptanceState.ACCEPTED:
        return decide(DisplayPriority.SUPPRESS, "not_accepted")
    if disposition is Disposition.REJECTED:
        return decide(DisplayPriority.SUPPRESS, "rejected")
    unresolved = (disposition is Disposition.UNVERIFIED
                  or admissibility is Admissibility.AUDIT_ONLY)
    if unresolved:
        if material:
            # A material matter we could not establish is an open question for
            # the reader, never a finding and never silently dropped.
            return decide(DisplayPriority.ON_DEMAND, "unresolved_material", True)
        return decide(DisplayPriority.SUPPRESS, "unresolved_immaterial")
    if material:
        return decide(DisplayPriority.DISPLAY, "usable_material")
    return decide(DisplayPriority.ON_DEMAND, "usable_low_materiality")
