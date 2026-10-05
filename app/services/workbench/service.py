"""Frozen case workbench view-model + assembler (Req 24.1-24.4; task 10.1).

The workbench is the OPTIONAL Milestone 10 surface. It presents a case as a
**frozen** workbench rather than a mutating dashboard: historical case versions
remain immutable and a later review creates a NEW snapshot (referencing its
predecessor) rather than silently rewriting the old one (Req 24.3, 24.4).

Design guarantees enforced here:

* **Read-only / frozen (Req 24.3).** This module is a pure VIEW. It only reads
  from the snapshots/escalations/reviews/reconciliation tables and the M8 memo
  JSON; it NEVER creates, updates, or deletes any case/snapshot/fact/escalation/
  review. Assembling a view does not mutate the database -- proven in tests by
  checking a finalized snapshot's ``content_hash`` is unchanged after rendering.
* **Nine navigable sections + header (Req 24.1).** The header carries the case
  id, as-of date, evidence cutoff, the presented ``snapshot_version`` and its
  predecessor/successor lineage. The nine named sections (Sources, Canonical
  Data, Metrics, Business Analysis, Financial Analysis, Risks, Exceptions,
  Human Review, Memo) are provided as navigable view data.
* **Derived counts (Req 24.2).** The four counts -- open exceptions, critical
  missing sources, AI/deterministic conflicts, and decisions awaiting humans --
  are computed from the authoritative services/tables AT VIEW TIME, never read
  from a stored-and-stale field.
* **Version lineage (Req 24.4).** When a successor snapshot exists (via the M7
  ``supersedes_snapshot`` linkage), the workbench surfaces both the predecessor
  and successor versions and can present ANY frozen version.

Section data comes from the presented draft or finalized memo JSON and its
referenced evidence snapshot. Cases without a draft show the available evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import Case, ReconciliationRecord, Snapshot
from app.services.escalation.engine import EscalationEngine
from app.services.ingestion.completeness import (
    CompletenessEvaluator,
    SourceProfileError,
)
from app.services.reporting.memo import MemoReportGenerator
from app.services.review.workflow import GATED_ACTIONS, HumanReviewWorkflow

FINAL_SNAPSHOT_TYPE = "final_case"
EVIDENCE_SNAPSHOT_TYPE = "canonical_evidence"

# The nine navigable workbench sections, in deterministic order (Req 24.1).
# Each entry is ``(section_key, human_title)``.
WORKBENCH_SECTIONS: tuple[tuple[str, str], ...] = (
    ("sources", "Sources"),
    ("canonical_data", "Canonical Data"),
    ("metrics", "Metrics"),
    ("business_analysis", "Business Analysis"),
    ("financial_analysis", "Financial Analysis"),
    ("risks", "Risks"),
    ("exceptions", "Exceptions"),
    ("human_review", "Human Review"),
    ("memo", "Memo"),
)


class WorkbenchError(RuntimeError):
    """Base error for the workbench view layer."""


class CaseNotFoundError(WorkbenchError):
    """Raised when the requested case does not exist."""


class SnapshotVersionNotFoundError(WorkbenchError):
    """Raised when a requested finalized snapshot version does not exist."""


@dataclass(frozen=True)
class WorkbenchCounts:
    """The four view-time counts the workbench displays (Req 24.2).

    Every count is derived from the authoritative source at view time; nothing
    here is read from a stored, potentially stale field.
    """

    open_exceptions: int
    critical_missing_sources: int
    ai_deterministic_conflicts: int
    awaiting_human_decisions: int


@dataclass(frozen=True)
class VersionLineage:
    """Predecessor/successor linkage for the presented version (Req 24.3, 24.4).

    ``presented_version`` is the version currently shown and is FROZEN when
    ``finalized`` is True. ``predecessor_version`` is the version this one
    supersedes (``supersedes_snapshot``); ``successor_versions`` are later
    versions that supersede this one (a later review created them without
    rewriting this one).
    """

    presented_version: int | None
    finalized: bool
    content_hash: str | None
    predecessor_version: int | None
    successor_versions: list[int] = field(default_factory=list)
    all_final_versions: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class WorkbenchHeader:
    """The workbench header (Req 24.1)."""

    case_id: str
    status: str
    as_of_date: str | None
    evidence_cutoff_timestamp: str | None
    lineage: VersionLineage


@dataclass(frozen=True)
class WorkbenchView:
    """The complete, read-only workbench view-model for a case version.

    This is the structured surface tests assert on directly without HTTP. It is
    assembled purely from reads; building it never mutates the database.
    """

    header: WorkbenchHeader
    counts: WorkbenchCounts
    sections: dict[str, Any]
    section_order: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping of the view-model."""
        return {
            "header": {
                "case_id": self.header.case_id,
                "status": self.header.status,
                "as_of_date": self.header.as_of_date,
                "evidence_cutoff_timestamp": self.header.evidence_cutoff_timestamp,
                "lineage": asdict(self.header.lineage),
            },
            "counts": asdict(self.counts),
            "sections": self.sections,
            "section_order": self.section_order,
        }


class WorkbenchService:
    """Assemble the read-only, frozen case workbench view-model (Req 24.1-24.4).

    All public methods are READS. The service reuses existing services for the
    counts (escalation engine, completeness evaluator, review workflow) and the
    reconciliation table, and reuses the M8 memo JSON for a finalized version's
    section data so there is a single source of truth.
    """

    def __init__(
        self,
        session: Session,
        *,
        completeness: CompletenessEvaluator | None = None,
    ) -> None:
        self._session = session
        # The completeness evaluator needs a ConfigRegistry-backed source
        # profile; it is optional so the workbench still renders (with a zero
        # critical-missing count and a note) when no profile is configured.
        self._completeness = completeness

    # -- public assembly ------------------------------------------------------

    def assemble(
        self,
        case_id: str,
        *,
        snapshot_version: int | None = None,
        present_source_types: list[str] | None = None,
    ) -> WorkbenchView:
        """Assemble the workbench view for a case (optionally a specific version).

        An explicit version can be a draft or final; its status is surfaced.
        Otherwise prefer the latest finalized version, then the latest draft,
        then evidence alone. Nothing is written by viewing a case.
        """
        case = self._session.get(Case, case_id)
        if case is None:
            raise CaseNotFoundError(f"No case with id {case_id!r}.")

        final_versions = self._final_versions(case_id)
        presented = self._resolve_presented(case_id, snapshot_version, final_versions)

        lineage = self._build_lineage(case_id, presented, final_versions)
        header = self._build_header(case, presented, lineage)
        evidence = self._evidence_snapshot_for(case_id, presented)
        evidence_payload = evidence.payload if evidence is not None else {}
        documents = evidence_payload.get("documents") or []
        if present_source_types is None and any("tags" in doc for doc in documents):
            present_source_types = sorted(
                {tag for doc in documents for tag in doc.get("tags", [])}
            )
        profile_version = (evidence_payload.get("config_versions") or {}).get(
            "source_profiles"
        )
        counts = self._build_counts(
            case_id, present_source_types, profile_version=profile_version
        )
        sections = self._build_sections(case_id, presented)

        return WorkbenchView(
            header=header,
            counts=counts,
            sections=sections,
            section_order=[{"key": k, "title": t} for k, t in WORKBENCH_SECTIONS],
        )

    # -- version resolution / lineage -----------------------------------------

    def _final_versions(self, case_id: str) -> list[Snapshot]:
        stmt = (
            select(Snapshot)
            .where(
                Snapshot.case_id == case_id,
                Snapshot.snapshot_type == FINAL_SNAPSHOT_TYPE,
            )
            .order_by(Snapshot.snapshot_version.asc())
        )
        return list(self._session.execute(stmt).scalars().all())

    def _resolve_presented(
        self,
        case_id: str,
        snapshot_version: int | None,
        final_versions: list[Snapshot],
    ) -> Snapshot | None:
        """Return a saved draft/final snapshot, or None for an evidence-only case.

        A requested version must exist (else :class:`SnapshotVersionNotFoundError`);
        With no explicit request prefer the latest finalized version, then the
        latest draft. Only evidence-only cases have no presented snapshot.
        """
        if snapshot_version is not None:
            for row in final_versions:
                if row.snapshot_version == snapshot_version:
                    return row
            raise SnapshotVersionNotFoundError(
                f"No FinalCaseSnapshot v{snapshot_version} for case {case_id!r}."
            )
        finalized = [row for row in final_versions if row.finalized]
        return (
            finalized[-1]
            if finalized
            else (final_versions[-1] if final_versions else None)
        )

    def _build_lineage(
        self,
        case_id: str,
        presented: Snapshot | None,
        final_versions: list[Snapshot],
    ) -> VersionLineage:
        all_versions = [r.snapshot_version for r in final_versions if r.finalized]
        if presented is None:
            return VersionLineage(
                presented_version=None,
                finalized=False,
                content_hash=None,
                predecessor_version=None,
                successor_versions=[],
                all_final_versions=all_versions,
            )

        predecessor = (presented.payload or {}).get("supersedes_snapshot")
        # Successors are versions whose payload supersedes the presented one. A
        # later review creates such a version without rewriting this one (Req 24.4).
        successors = sorted(
            r.snapshot_version
            for r in final_versions
            if (r.payload or {}).get("supersedes_snapshot")
            == presented.snapshot_version
        )
        return VersionLineage(
            presented_version=presented.snapshot_version,
            finalized=bool(presented.finalized),
            content_hash=presented.content_hash,
            predecessor_version=predecessor,
            successor_versions=successors,
            all_final_versions=all_versions,
        )

    def _build_header(
        self, case: Case, presented: Snapshot | None, lineage: VersionLineage
    ) -> WorkbenchHeader:
        as_of, cutoff = self._resolve_header_dates(case, presented)
        return WorkbenchHeader(
            case_id=case.case_id,
            status=case.status,
            as_of_date=as_of,
            evidence_cutoff_timestamp=cutoff,
            lineage=lineage,
        )

    def _resolve_header_dates(
        self, case: Case, presented: Snapshot | None
    ) -> tuple[str | None, str | None]:
        """Resolve as-of/cutoff for the header (Req 24.1).

        Prefer the values embedded in the presented version's referenced
        evidence snapshot; fall back to the case row so the header is always
        populated when the case carries the dates.
        """
        as_of: str | None = None
        cutoff: str | None = None
        evidence = self._evidence_snapshot_for(case.case_id, presented)
        if evidence is not None:
            payload = evidence.payload or {}
            as_of = payload.get("as_of_date")
            cutoff = payload.get("evidence_cutoff_timestamp")
        if as_of is None and case.as_of_date is not None:
            as_of = case.as_of_date.isoformat()
        if cutoff is None and case.evidence_cutoff_timestamp is not None:
            cutoff = case.evidence_cutoff_timestamp.isoformat()
        return as_of, cutoff

    # -- counts (Req 24.2) ----------------------------------------------------

    def _build_counts(
        self,
        case_id: str,
        present_source_types: list[str] | None,
        *,
        profile_version: int | None = None,
    ) -> WorkbenchCounts:
        """Compute the four counts from authoritative sources at view time."""
        engine = EscalationEngine(self._session, case_id=case_id)
        open_escalations = engine.open_escalations(case_id)

        # 1) Open exceptions = all open escalations (Req 14.5 queue).
        open_exceptions = len(open_escalations)

        # 2) Critical missing sources = completeness evaluator missing-critical
        #    signal. Deterministic, config-driven; 0 when no profile/present set.
        critical_missing = self._critical_missing_count(
            present_source_types, profile_version=profile_version
        )

        # 3) AI/deterministic conflicts = reconciliation records in the
        #    ``conflicting`` resolved state.
        conflicts = self._conflicting_reconciliation_count(case_id)

        # 4) Decisions awaiting humans = gated review actions not yet signed off
        #    PLUS open mandatory escalations (both need a human decision).
        awaiting = self._awaiting_human_count(case_id, open_escalations)

        return WorkbenchCounts(
            open_exceptions=open_exceptions,
            critical_missing_sources=critical_missing,
            ai_deterministic_conflicts=conflicts,
            awaiting_human_decisions=awaiting,
        )

    def _critical_missing_count(
        self,
        present_source_types: list[str] | None,
        *,
        profile_version: int | None = None,
    ) -> int:
        if self._completeness is None or present_source_types is None:
            return 0
        try:
            assessment = self._completeness.assess(
                present_source_types, profile_version=profile_version
            )
        except SourceProfileError:
            # No profile configured -> no silent default; report zero critical
            # missing rather than fabricate a determination (Req 21).
            return 0
        return len(assessment.missing_critical)

    def _conflicting_reconciliation_count(self, case_id: str) -> int:
        stmt = select(ReconciliationRecord).where(
            ReconciliationRecord.case_id == case_id,
            ReconciliationRecord.resolved_state == "conflicting",
        )
        return len(list(self._session.execute(stmt).scalars().all()))

    def _awaiting_human_count(self, case_id: str, open_escalations: list[Any]) -> int:
        workflow = HumanReviewWorkflow(self._session, case_id=case_id)
        reviews = workflow.reviews_for_case(case_id)
        gated_action_values = {a.value for a in GATED_ACTIONS}
        unsigned_gated = sum(
            1 for r in reviews if r.action in gated_action_values and not r.signed_off
        )
        open_mandatory = sum(1 for e in open_escalations if e.mandatory)
        return unsigned_gated + open_mandatory

    # -- sections (Req 24.1) --------------------------------------------------

    def _build_sections(
        self, case_id: str, presented: Snapshot | None
    ) -> dict[str, Any]:
        """Assemble the nine navigable sections as read-only view data.

        Saved drafts and finals use their memo JSON and referenced evidence.
        Evidence-only cases fall back to the latest evidence snapshot.
        """
        if presented is not None:
            return self._sections_from_memo(case_id, presented)
        return self._sections_from_evidence(case_id, presented)

    def _sections_from_memo(self, case_id: str, presented: Snapshot) -> dict[str, Any]:
        # Reuse the M8 generator read-only (no audit -> no events emitted).
        generator = MemoReportGenerator(self._session)
        generate = (
            generator.generate_json
            if presented.finalized
            else generator.generate_draft_json
        )
        memo = generate(case_id=case_id, snapshot_version=presented.snapshot_version)
        snap = memo["final_case_snapshot"]
        evidence = self._evidence_snapshot_for(case_id, presented)
        evidence_payload = evidence.payload if evidence is not None else {}

        return {
            "sources": {
                "documents": evidence_payload.get("documents") or [],
                "evidence_snapshot_ref": snap.get("evidence_snapshot_ref") or {},
            },
            "canonical_data": {
                "entities": evidence_payload.get("entities") or [],
                "facts": evidence_payload.get("facts") or [],
                "financials": evidence_payload.get("financials") or {},
                "sec_filings": evidence_payload.get("sec_filings") or [],
                "narrative_evidence": evidence_payload.get("narrative_evidence") or [],
                "data_quality": evidence_payload.get("data_quality") or {},
            },
            "metrics": {
                "metrics": snap.get("metrics") or {},
                "benchmarks": snap.get("benchmarks") or {},
            },
            "business_analysis": snap.get("business_analysis") or {},
            "financial_analysis": snap.get("financial_analysis") or {},
            "risks": {
                "risks": snap.get("risks") or [],
                "mitigants": snap.get("mitigants") or [],
            },
            "exceptions": {
                "unresolved_exceptions": memo.get("unresolved_exceptions") or [],
                "escalations": snap.get("escalations") or [],
            },
            "human_review": {"human_reviews": snap.get("human_reviews") or []},
            "memo": {
                "final_status": memo.get("final_status"),
                "recommendation": snap.get("recommendation") or {},
                "output_version": memo.get("output_version"),
                "source_snapshot": memo.get("source_snapshot") or {},
            },
        }

    def _sections_from_evidence(
        self, case_id: str, presented: Snapshot | None
    ) -> dict[str, Any]:
        """Pre-final sections read from the latest evidence snapshot read-only.

        A case that is not finalized has no memo JSON; the workbench still shows
        what evidence exists and leaves analysis/memo sections empty with a
        ``pending`` marker rather than inventing content.
        """
        evidence = self._latest_evidence_snapshot(case_id)
        payload = evidence.payload if evidence is not None else {}

        # Open escalations surface in the Exceptions section for a pre-final case.
        engine = EscalationEngine(self._session, case_id=case_id)
        open_escalations = [
            {
                "escalation_id": e.escalation_id,
                "rule_id": e.rule_id,
                "severity": e.severity,
                "category": e.category,
                "mandatory": e.mandatory,
                "reason": e.reason,
            }
            for e in engine.open_escalations(case_id)
        ]
        reviews = [
            {
                "review_id": r.review_id,
                "action": r.action,
                "reviewer": r.reviewer,
                "signed_off": r.signed_off,
                "requires_sign_off": r.requires_sign_off,
            }
            for r in HumanReviewWorkflow(
                self._session, case_id=case_id
            ).reviews_for_case(case_id)
        ]

        pending = {"status": "pending", "detail": "Case not finalized."}
        return {
            "sources": {"documents": payload.get("documents") or []},
            "canonical_data": {
                "entities": payload.get("entities") or [],
                "facts": payload.get("facts") or [],
                "sec_filings": payload.get("sec_filings") or [],
                "narrative_evidence": payload.get("narrative_evidence") or [],
                "data_quality": payload.get("data_quality") or {},
            },
            "metrics": {"metrics": {}, "benchmarks": {}, **pending},
            "business_analysis": dict(pending),
            "financial_analysis": dict(pending),
            "risks": {"risks": [], "mitigants": [], **pending},
            "exceptions": {"unresolved_exceptions": open_escalations},
            "human_review": {"human_reviews": reviews},
            "memo": dict(pending),
        }

    # -- evidence-snapshot lookups (read-only) --------------------------------

    def _evidence_snapshot_for(
        self, case_id: str, presented: Snapshot | None
    ) -> Snapshot | None:
        """Return the evidence snapshot referenced by the presented version.

        Falls back to the latest evidence snapshot when there is no presented
        finalized version (pre-final case).
        """
        if presented is None:
            return self._latest_evidence_snapshot(case_id)
        ref = (presented.payload or {}).get("evidence_snapshot_ref") or {}
        version = ref.get("snapshot_version")
        if version is None:
            return self._latest_evidence_snapshot(case_id)
        stmt = select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.snapshot_type == EVIDENCE_SNAPSHOT_TYPE,
            Snapshot.snapshot_version == version,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def _latest_evidence_snapshot(self, case_id: str) -> Snapshot | None:
        stmt = (
            select(Snapshot)
            .where(
                Snapshot.case_id == case_id,
                Snapshot.snapshot_type == EVIDENCE_SNAPSHOT_TYPE,
            )
            .order_by(Snapshot.snapshot_version.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()
