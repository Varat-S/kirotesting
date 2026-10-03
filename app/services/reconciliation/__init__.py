"""Reconciliation service: zero-safe comparison, definition compatibility, and
fact-state resolution. Conflicts are preserved; human resolution is additive.

Public API (Milestone 4):

* :mod:`compatibility` -- definition-compatibility checks before comparison
  (task 4.1).
* :mod:`service` -- zero-safe reconciliation, conflict detection, exact dedup,
  contradiction records, and the non-destructive human-correction workflow
  (tasks 4.2, 4.3).
* :mod:`snapshot` -- assembly + versioning of the CanonicalEvidenceSnapshot
  (task 4.4).

The escalation ENGINE is Milestone 5. A ``conflicting`` resolution + its
reconciliation record is the forward-compatible signal that engine will consume;
this package emits ``fact_conflict_detected`` and does NOT route escalations.
"""

from app.services.reconciliation.compatibility import (
    CompatibilityDimension,
    CompatibilityResult,
    check_compatibility,
    is_structured,
    prefer_structured,
)
from app.services.reconciliation.service import (
    DEFAULT_TOLERANCES,
    ComparisonMethod,
    HumanCorrectionWorkflow,
    Reconciler,
    ReconciliationResult,
    ToleranceConfig,
    deduplicate_exact,
)
from app.services.reconciliation.snapshot import SnapshotAssembler

__all__ = [
    "DEFAULT_TOLERANCES",
    "ComparisonMethod",
    "CompatibilityDimension",
    "CompatibilityResult",
    "HumanCorrectionWorkflow",
    "Reconciler",
    "ReconciliationResult",
    "SnapshotAssembler",
    "ToleranceConfig",
    "check_compatibility",
    "deduplicate_exact",
    "is_structured",
    "prefer_structured",
]
