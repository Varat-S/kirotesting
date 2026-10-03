"""FinalCaseSnapshot assembly, finalization, and immutability (task 7.2).

The :class:`~app.schemas.snapshots.FinalCaseSnapshot` is the post-analysis case
object (Req 16.1). This module:

* **Assembles** it from a :class:`CanonicalEvidenceSnapshot` version plus
  metrics, benchmarks, analysis, risks/mitigants, exceptions, escalations,
  human reviews, recommendation, and audit metadata. It references the evidence
  snapshot BY VERSION (``evidence_snapshot_ref``) and records the config,
  metric-definition, rule, and prompt/model versions in force so historical
  runs stay linked to the exact versions used (Req 16.1).
* **Validates on write** against the versioned JSON Schema (Req 5.5 / 16.1).
* **Finalizes** a snapshot (Req 16.2): finalization requires explicit human
  sign-off (Req 15.6) and no unresolved mandatory escalation (Req 14.7 / 15.5);
  it freezes a SHA-256 content hash over the canonical payload and marks the
  row ``finalized``. A ``before_flush`` guard then makes the finalized row
  structurally immutable -- any attempt to mutate or delete it raises
  :class:`SnapshotImmutabilityError`.
* **Supersedes** (Req 16.3): a change after finalization never edits the
  finalized row; it creates a NEW snapshot version whose ``supersedes_snapshot``
  points at the predecessor, leaving the original byte-for-byte reproducible.

Assembly is deterministic: the same evidence snapshot + versions + reviews
produce an equivalent FinalCaseSnapshot, and re-reading a finalized snapshot
reproduces identical content (verifiable via :meth:`verify_integrity`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.core.hashing import content_hash
from app.models.base import utcnow
from app.models.orm import Snapshot
from app.schemas.json_schema import validate_snapshot
from app.schemas.snapshots import (
    SCHEMA_VERSION,
    EvidenceSnapshotRef,
    FinalCaseSnapshot,
)
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.escalation.engine import EscalationEngine

FINAL_SNAPSHOT_TYPE = "final_case"


class SnapshotImmutabilityError(RuntimeError):
    """Raised when code attempts to modify or delete a finalized snapshot."""


# Columns a finalized snapshot may still take (none that affect its content).
# Everything that defines the snapshot is frozen once ``finalized`` is True.
_FROZEN_COLUMNS = frozenset(
    {
        "snapshot_type",
        "snapshot_version",
        "schema_version",
        "supersedes",
        "config_versions",
        "payload",
        "content_hash",
        "finalized",
        "finalized_at",
        "case_id",
    }
)


def install_finalized_snapshot_guard(session: Session) -> None:
    """Attach a ``before_flush`` guard forbidding edits to finalized snapshots.

    Idempotent per session. Mirrors the append-only audit-log guard: creating a
    new snapshot (including a superseding version) is allowed, but any UPDATE to
    a frozen column of an already-finalized :class:`Snapshot`, or any DELETE of
    one, raises :class:`SnapshotImmutabilityError` so immutability cannot be
    bypassed by writing to the ORM directly (Req 16.2).
    """
    if getattr(session, "_final_snapshot_guard_installed", False):
        return

    @event.listens_for(session, "before_flush")
    def _before_flush(sess: Session, flush_context, instances) -> None:  # noqa: ANN001
        for obj in sess.dirty:
            if not isinstance(obj, Snapshot):
                continue
            # Determine whether the row was finalized BEFORE this flush. A row
            # being finalized for the first time (unloaded/False -> True) is
            # allowed; mutating an already-finalized row is not.
            hist = _history(obj, "finalized")
            was_finalized = _prior_value(hist, obj.finalized)
            if not was_finalized:
                continue
            modified = {
                attr
                for attr in _FROZEN_COLUMNS
                if _history(obj, attr).has_changes()
            }
            if modified:
                raise SnapshotImmutabilityError(
                    "Finalized FinalCaseSnapshot is immutable; cannot modify "
                    f"{sorted(modified)} (snapshot id={obj.id!r}). Create a new "
                    "version that supersedes it instead."
                )
        for obj in sess.deleted:
            if isinstance(obj, Snapshot) and obj.finalized:
                raise SnapshotImmutabilityError(
                    "Finalized FinalCaseSnapshot is immutable and cannot be "
                    f"deleted (snapshot id={obj.id!r})."
                )

    session._final_snapshot_guard_installed = True  # type: ignore[attr-defined]


def _history(obj: Any, attr: str):
    from sqlalchemy import inspect as sa_inspect

    return sa_inspect(obj).attrs[attr].history


def _prior_value(hist, current: Any) -> Any:
    """Return the pre-flush value of an attribute from its change history."""
    if hist.deleted:
        return hist.deleted[0]
    if hist.unchanged:
        return hist.unchanged[0]
    # No prior persisted value (new insert): fall back to current.
    return current


class FinalSnapshotAssembler:
    """Assemble, validate, finalize, and version a FinalCaseSnapshot."""

    # Config kinds recorded on the final snapshot so historical runs stay
    # linked to the exact configuration versions in force (Req 16.1).
    FINAL_CONFIG_KINDS = (
        "tolerances",
        "policy",
        "peers",
        "trend_rules",
        "escalation_rules",
        "source_profiles",
    )

    def __init__(
        self,
        session: Session,
        registry: ConfigRegistry,
        *,
        audit: AuditLog | None = None,
    ) -> None:
        self._session = session
        self._registry = registry
        self._audit = audit
        install_finalized_snapshot_guard(session)

    # -- assembly -------------------------------------------------------------

    def assemble(
        self,
        *,
        case_id: str,
        evidence_snapshot_version: int,
        metrics: dict | None = None,
        benchmarks: dict | None = None,
        business_analysis: dict | None = None,
        financial_analysis: dict | None = None,
        risks: list | None = None,
        mitigants: list | None = None,
        escalations: list | None = None,
        human_reviews: list | None = None,
        recommendation: dict | None = None,
        audit_metadata: dict | None = None,
        metric_definition_versions: dict[str, int] | None = None,
        rule_versions: dict[str, int] | None = None,
        prompt_model_versions: dict[str, str] | None = None,
        config_kinds: tuple[str, ...] | None = None,
        supersedes_snapshot: int | None = None,
    ) -> FinalCaseSnapshot:
        """Build an (unpersisted) FinalCaseSnapshot and validate it on write.

        The evidence snapshot is referenced BY VERSION; the open escalations are
        surfaced as ``exceptions`` so the final output identifies unresolved
        exceptions (Req 15.5). The config/metric-def/rule/prompt-model versions
        are recorded for reproducibility (Req 16.1).
        """
        self._require_evidence_snapshot(case_id, evidence_snapshot_version)

        kinds = config_kinds if config_kinds is not None else self.FINAL_CONFIG_KINDS
        config_versions = self._registry.config_versions_map(kinds)

        engine = EscalationEngine(self._session, case_id=case_id)
        exceptions = [
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

        snapshot = FinalCaseSnapshot(
            schema_version=SCHEMA_VERSION,
            snapshot_version=self._next_version(case_id),
            supersedes_snapshot=supersedes_snapshot,
            evidence_snapshot_ref=EvidenceSnapshotRef(
                case_id=case_id, snapshot_version=evidence_snapshot_version
            ),
            config_versions=config_versions,
            metric_definition_versions=dict(metric_definition_versions or {}),
            rule_versions=dict(rule_versions or {}),
            prompt_model_versions=dict(prompt_model_versions or {}),
            metrics=metrics or {},
            benchmarks=benchmarks or {},
            business_analysis=business_analysis or {},
            financial_analysis=financial_analysis or {},
            risks=list(risks or []),
            mitigants=list(mitigants or []),
            exceptions=exceptions,
            escalations=list(escalations or []),
            human_reviews=list(human_reviews or []),
            recommendation=recommendation or {"status": "draft"},
            audit_metadata=audit_metadata or {},
            finalized=False,
        )

        validate_snapshot(snapshot)
        return snapshot

    def persist_draft(self, snapshot: FinalCaseSnapshot) -> Snapshot:
        """Persist a non-finalized (draft) FinalCaseSnapshot row."""
        payload = validate_snapshot(snapshot)
        row = Snapshot(
            case_id=snapshot.evidence_snapshot_ref.case_id,
            snapshot_type=snapshot.snapshot_type,
            snapshot_version=snapshot.snapshot_version,
            schema_version=snapshot.schema_version,
            supersedes=self._row_id_for_version(
                snapshot.evidence_snapshot_ref.case_id,
                snapshot.supersedes_snapshot,
            ),
            finalized=False,
            config_versions=snapshot.config_versions,
            payload=payload,
        )
        self._session.add(row)
        self._session.flush()
        return row

    # -- finalization ---------------------------------------------------------

    def finalize(
        self,
        snapshot: FinalCaseSnapshot,
        *,
        signed_off_by: str,
        has_sign_off: bool,
        reason: str | None = None,
    ) -> Snapshot:
        """Finalize a FinalCaseSnapshot: gate, freeze a hash, persist immutably.

        Finalization is BLOCKED (Req 15.6 / 16.2) unless:

        * ``has_sign_off`` is True (explicit human sign-off of the final
          recommendation), AND
        * there is no unresolved MANDATORY escalation for the case (Req 14.7).

        On success the recommendation status is set to ``final``, a SHA-256
        content hash is frozen over the canonical payload, the row is marked
        ``finalized``, and ``snapshot_finalized`` is emitted. The finalized row
        is thereafter immutable via :func:`install_finalized_snapshot_guard`.
        """
        from app.services.review.workflow import SignOffRequiredError

        case_id = snapshot.evidence_snapshot_ref.case_id

        if not has_sign_off:
            raise SignOffRequiredError(
                "Cannot finalize FinalCaseSnapshot without explicit human "
                "sign-off of the final recommendation (Req 15.6)."
            )

        engine = EscalationEngine(self._session, case_id=case_id)
        if engine.has_unresolved_mandatory(case_id):
            raise SignOffRequiredError(
                "Cannot finalize FinalCaseSnapshot while mandatory escalations "
                "are unresolved; resolve them or they must be surfaced as "
                "unresolved exceptions first (Req 14.7 / 15.5)."
            )

        finalized_snapshot = snapshot.model_copy(
            update={
                "finalized": True,
                "recommendation": {**snapshot.recommendation, "status": "final"},
            }
        )
        payload = validate_snapshot(finalized_snapshot)
        frozen_hash = content_hash(payload)

        row = Snapshot(
            case_id=case_id,
            snapshot_type=finalized_snapshot.snapshot_type,
            snapshot_version=finalized_snapshot.snapshot_version,
            schema_version=finalized_snapshot.schema_version,
            supersedes=self._row_id_for_version(
                case_id, finalized_snapshot.supersedes_snapshot
            ),
            finalized=True,
            config_versions=finalized_snapshot.config_versions,
            payload=payload,
            content_hash=frozen_hash,
            finalized_at=utcnow(),
        )
        self._session.add(row)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.SNAPSHOT_FINALIZED,
                case_id=case_id,
                actor_type=ActorType.HUMAN,
                actor_id=signed_off_by,
                after={
                    "snapshot_type": row.snapshot_type,
                    "snapshot_version": row.snapshot_version,
                    "content_hash": frozen_hash,
                    "supersedes_snapshot": finalized_snapshot.supersedes_snapshot,
                },
                reason=reason or "FinalCaseSnapshot finalized after human sign-off.",
                linked_objects=[
                    f"snapshot:{FINAL_SNAPSHOT_TYPE}:{row.snapshot_version}"
                ],
            )
        return row

    def supersede(
        self,
        predecessor: Snapshot,
        *,
        signed_off_by: str,
        has_sign_off: bool,
        metrics: dict | None = None,
        benchmarks: dict | None = None,
        business_analysis: dict | None = None,
        financial_analysis: dict | None = None,
        risks: list | None = None,
        mitigants: list | None = None,
        escalations: list | None = None,
        human_reviews: list | None = None,
        recommendation: dict | None = None,
        audit_metadata: dict | None = None,
        metric_definition_versions: dict[str, int] | None = None,
        rule_versions: dict[str, int] | None = None,
        prompt_model_versions: dict[str, str] | None = None,
        reason: str | None = None,
    ) -> Snapshot:
        """Create a NEW finalized version that supersedes a predecessor (Req 16.3).

        The predecessor row is NEVER edited; a new snapshot version is assembled
        and finalized with ``supersedes_snapshot`` pointing at the predecessor's
        version, leaving the original byte-for-byte reproducible.
        """
        if predecessor.snapshot_type != FINAL_SNAPSHOT_TYPE:
            raise ValueError(
                "Can only supersede a FinalCaseSnapshot predecessor."
            )
        case_id = predecessor.case_id
        prior_payload = predecessor.payload

        snapshot = self.assemble(
            case_id=case_id,
            evidence_snapshot_version=prior_payload["evidence_snapshot_ref"][
                "snapshot_version"
            ],
            metrics=metrics if metrics is not None else prior_payload.get("metrics"),
            benchmarks=(
                benchmarks if benchmarks is not None else prior_payload.get("benchmarks")
            ),
            business_analysis=(
                business_analysis
                if business_analysis is not None
                else prior_payload.get("business_analysis")
            ),
            financial_analysis=(
                financial_analysis
                if financial_analysis is not None
                else prior_payload.get("financial_analysis")
            ),
            risks=risks if risks is not None else prior_payload.get("risks"),
            mitigants=(
                mitigants if mitigants is not None else prior_payload.get("mitigants")
            ),
            escalations=(
                escalations
                if escalations is not None
                else prior_payload.get("escalations")
            ),
            human_reviews=(
                human_reviews
                if human_reviews is not None
                else prior_payload.get("human_reviews")
            ),
            recommendation=(
                recommendation
                if recommendation is not None
                else prior_payload.get("recommendation")
            ),
            audit_metadata=(
                audit_metadata
                if audit_metadata is not None
                else prior_payload.get("audit_metadata")
            ),
            metric_definition_versions=(
                metric_definition_versions
                if metric_definition_versions is not None
                else prior_payload.get("metric_definition_versions")
            ),
            rule_versions=(
                rule_versions
                if rule_versions is not None
                else prior_payload.get("rule_versions")
            ),
            prompt_model_versions=(
                prompt_model_versions
                if prompt_model_versions is not None
                else prior_payload.get("prompt_model_versions")
            ),
            supersedes_snapshot=predecessor.snapshot_version,
        )
        return self.finalize(
            snapshot,
            signed_off_by=signed_off_by,
            has_sign_off=has_sign_off,
            reason=reason or "New version superseding a finalized snapshot.",
        )

    # -- integrity ------------------------------------------------------------

    def verify_integrity(self, row: Snapshot) -> bool:
        """Return True if a finalized row's payload matches its frozen hash.

        Proves a finalized snapshot remains byte-for-byte reproducible (Req
        16.2 / 16.3): re-hashing the stored payload must equal the hash frozen
        at finalization.
        """
        if not row.finalized or row.content_hash is None:
            return False
        return content_hash(row.payload) == row.content_hash

    # -- lookups --------------------------------------------------------------

    def get_version(self, case_id: str, snapshot_version: int) -> Snapshot | None:
        stmt = select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.snapshot_type == FINAL_SNAPSHOT_TYPE,
            Snapshot.snapshot_version == snapshot_version,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def _row_id_for_version(
        self, case_id: str, snapshot_version: int | None
    ) -> int | None:
        if snapshot_version is None:
            return None
        row = self.get_version(case_id, snapshot_version)
        return row.id if row is not None else None

    def _require_evidence_snapshot(self, case_id: str, version: int) -> None:
        stmt = select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.snapshot_type == "canonical_evidence",
            Snapshot.snapshot_version == version,
        )
        if self._session.execute(stmt).scalar_one_or_none() is None:
            raise ValueError(
                f"No CanonicalEvidenceSnapshot v{version} for case {case_id!r}; "
                "a FinalCaseSnapshot must reference an existing evidence version."
            )

    def _next_version(self, case_id: str) -> int:
        stmt = (
            select(Snapshot.snapshot_version)
            .where(
                Snapshot.case_id == case_id,
                Snapshot.snapshot_type == FINAL_SNAPSHOT_TYPE,
            )
            .order_by(Snapshot.snapshot_version.desc())
            .limit(1)
        )
        latest = self._session.execute(stmt).scalar_one_or_none()
        return 1 if latest is None else latest + 1
