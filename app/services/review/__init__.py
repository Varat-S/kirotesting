"""Human review + FinalCaseSnapshot finalization (Milestone 7).

Public API:

* :mod:`workflow` -- the non-destructive human-in-the-loop review workflow
  (task 7.1): all reviewer actions, additive before/after recording, gated
  sign-off, unresolved-exception identification, and ``human_review`` events
  (Req 15.1-15.6).
* :mod:`finalization` -- assembly, finalization, and immutability of the
  :class:`~app.schemas.snapshots.FinalCaseSnapshot` (task 7.2): references a
  ``CanonicalEvidenceSnapshot`` by version, records config/metric-def/rule/
  prompt-model versions, validates on write, freezes a content hash, blocks
  mutation of a finalized snapshot, and supersedes a predecessor on change
  (Req 16.1-16.4).
"""

from app.services.review.finalization import (
    FinalSnapshotAssembler,
    SnapshotImmutabilityError,
    install_finalized_snapshot_guard,
)
from app.services.review.workflow import (
    GATED_ACTIONS,
    HumanReviewWorkflow,
    ReviewAction,
    SignOffRequiredError,
)

__all__ = [
    "GATED_ACTIONS",
    "FinalSnapshotAssembler",
    "HumanReviewWorkflow",
    "ReviewAction",
    "SignOffRequiredError",
    "SnapshotImmutabilityError",
    "install_finalized_snapshot_guard",
]
