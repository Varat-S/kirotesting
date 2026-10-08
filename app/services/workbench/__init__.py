"""Frozen case workbench view layer (Milestone 10, task 10.1).

Public API:

* :mod:`service` -- the read-only :class:`WorkbenchService` that assembles a
  :class:`WorkbenchView` view-model for a case version: a header (case id,
  as-of date, evidence cutoff, presented ``snapshot_version`` + predecessor/
  successor lineage), the nine navigable sections (Sources, Canonical Data,
  Metrics, Business Analysis, Financial Analysis, Risks, Exceptions, Human
  Review, Memo), and the four view-time counts (open exceptions, critical
  missing sources, AI/deterministic conflicts, decisions awaiting humans).

The workbench is a VIEW: it never mutates a case/snapshot/escalation/review
(Req 24.3). Historical versions remain frozen and a later review surfaces as a
new version referencing its predecessor (Req 24.4).
"""

from app.services.workbench.agentic import (
    AGENTIC_SECTIONS,
    AgenticWorkbenchService,
    AgenticWorkbenchView,
    AnalysisRunNotFoundError,
)
from app.services.workbench.service import (
    WORKBENCH_SECTIONS,
    CaseNotFoundError,
    SnapshotVersionNotFoundError,
    VersionLineage,
    WorkbenchCounts,
    WorkbenchError,
    WorkbenchHeader,
    WorkbenchService,
    WorkbenchView,
)

__all__ = [
    "WORKBENCH_SECTIONS",
    "CaseNotFoundError",
    "SnapshotVersionNotFoundError",
    "VersionLineage",
    "WorkbenchCounts",
    "WorkbenchError",
    "WorkbenchHeader",
    "WorkbenchService",
    "WorkbenchView",
    "AGENTIC_SECTIONS",
    "AgenticWorkbenchService",
    "AgenticWorkbenchView",
    "AnalysisRunNotFoundError",
]
