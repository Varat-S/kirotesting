"""Entity resolution and scope-mismatch detection (Requirement 2.2-2.4, 2.6).

Resolution maps a free-text reporting name, alias, or ticker to a registered
``entity_id`` so that facts can be tagged with an explicit entity and
consolidation scope. Where identity is ambiguous (a name/ticker matches more
than one registered entity, e.g. a borrower and its consolidated parent) the
resolver does NOT guess -- it returns an ambiguous result for escalation
(Req 2.4).

Mismatch detection compares the entity a fact was *tagged with* (or resolved to)
against the entity/consolidation scope the fact was *expected* to belong to. On
a mismatch the system raises a conflict/escalation signal and never merges the
values (Req 2.3, 2.6). Facts from different entities or consolidation scopes are
never silently combined.

Escalation boundary (Milestone 5 owns the escalation engine): here we represent
an escalation condition minimally and forward-compatibly by (a) emitting the
``entity_mismatch_detected`` audit event and (b) returning a structured
:class:`EntityMismatch` record that a later escalation engine can consume. We do
NOT build the escalation engine itself in this milestone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import Entity
from app.services.audit.log import ActorType, AuditLog, EventType


class ResolutionStatus(str, Enum):
    """Outcome of resolving a name/alias/ticker to an entity."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ResolutionResult:
    """Result of an entity-resolution attempt.

    ``entity_id`` is populated only when ``status`` is ``RESOLVED``. When
    ``AMBIGUOUS`` the ``candidates`` list carries every entity_id that matched,
    so the caller can escalate rather than guess (Req 2.4).
    """

    status: ResolutionStatus
    query: str
    entity_id: str | None = None
    candidates: list[str] = field(default_factory=list)


class MismatchKind(str, Enum):
    """Category of an entity/scope mismatch."""

    ENTITY = "entity"
    CONSOLIDATION_SCOPE = "consolidation_scope"


@dataclass(frozen=True)
class EntityMismatch:
    """A detected entity/scope mismatch (forward-compatible escalation signal).

    Consumed by the Milestone 5 escalation engine. Carries both the expected
    and the observed identity so a reviewer sees exactly what disagreed; the
    values are never merged (Req 2.3, 2.6).
    """

    kind: MismatchKind
    expected_entity_id: str | None
    observed_entity_id: str | None
    expected_scope: str | None
    observed_scope: str | None
    detail: str


def _norm(value: str) -> str:
    """Normalize a label for matching: trimmed, case-folded."""
    return value.strip().casefold()


class EntityResolver:
    """Resolve names/aliases/tickers to entities and detect scope mismatches."""

    def __init__(self, session: Session, audit: AuditLog | None = None) -> None:
        self._session = session
        self._audit = audit

    # -- resolution -----------------------------------------------------------

    def resolve(self, query: str, *, case_id: str | None = None) -> ResolutionResult:
        """Resolve ``query`` (legal name, alias, or ticker) to an ``entity_id``.

        Matching is case-insensitive against ``legal_name``, ``aliases`` and
        ``tickers``. Multiple matches yield an ``AMBIGUOUS`` result (never a
        silent pick); no match yields ``UNRESOLVED`` (Req 2.4).
        """
        needle = _norm(query)
        if not needle:
            return ResolutionResult(ResolutionStatus.UNRESOLVED, query)

        stmt = select(Entity)
        if case_id is not None:
            stmt = stmt.where(Entity.case_id == case_id)

        matches: list[str] = []
        for entity in self._session.execute(stmt).scalars().all():
            labels = {_norm(entity.legal_name)}
            labels.update(_norm(a) for a in (entity.aliases or []))
            labels.update(_norm(t) for t in (entity.tickers or []))
            if needle in labels:
                matches.append(entity.entity_id)

        # De-duplicate while preserving order.
        unique = list(dict.fromkeys(matches))
        if len(unique) == 1:
            return ResolutionResult(
                ResolutionStatus.RESOLVED, query, entity_id=unique[0]
            )
        if len(unique) > 1:
            return ResolutionResult(
                ResolutionStatus.AMBIGUOUS, query, candidates=unique
            )
        return ResolutionResult(ResolutionStatus.UNRESOLVED, query)

    # -- mismatch detection ---------------------------------------------------

    def detect_mismatch(
        self,
        *,
        expected_entity_id: str | None,
        observed_entity_id: str | None,
        expected_scope: str | None = None,
        observed_scope: str | None = None,
        case_id: str | None = None,
        fact_id: str | None = None,
    ) -> EntityMismatch | None:
        """Return an :class:`EntityMismatch` if entity/scope disagree, else None.

        Compares the entity a fact resolved/was tagged to against the expected
        entity, and the observed consolidation scope against the expected scope.
        On any disagreement a mismatch is returned AND (when audit is wired) an
        ``entity_mismatch_detected`` event is emitted. The caller must NOT merge
        the values (Req 2.3, 2.6).
        """
        mismatch: EntityMismatch | None = None

        if (
            expected_entity_id is not None
            and observed_entity_id is not None
            and expected_entity_id != observed_entity_id
        ):
            mismatch = EntityMismatch(
                kind=MismatchKind.ENTITY,
                expected_entity_id=expected_entity_id,
                observed_entity_id=observed_entity_id,
                expected_scope=expected_scope,
                observed_scope=observed_scope,
                detail=(
                    f"Fact attributed to {observed_entity_id!r} but expected "
                    f"{expected_entity_id!r}."
                ),
            )
        elif (
            expected_scope is not None
            and observed_scope is not None
            and _norm(expected_scope) != _norm(observed_scope)
        ):
            mismatch = EntityMismatch(
                kind=MismatchKind.CONSOLIDATION_SCOPE,
                expected_entity_id=expected_entity_id,
                observed_entity_id=observed_entity_id,
                expected_scope=expected_scope,
                observed_scope=observed_scope,
                detail=(
                    f"Fact scope {observed_scope!r} does not match expected "
                    f"{expected_scope!r}."
                ),
            )

        if mismatch is not None and self._audit is not None:
            self._audit.record(
                EventType.ENTITY_MISMATCH_DETECTED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                before={
                    "expected_entity_id": expected_entity_id,
                    "expected_scope": expected_scope,
                },
                after={
                    "observed_entity_id": observed_entity_id,
                    "observed_scope": observed_scope,
                    "kind": mismatch.kind.value,
                },
                reason=mismatch.detail,
                linked_objects=[x for x in (fact_id,) if x is not None],
            )

        return mismatch

    def tag_fact_scope(
        self,
        *,
        reporting_name: str,
        consolidation_scope: str,
        expected_entity_id: str | None = None,
        case_id: str | None = None,
        fact_id: str | None = None,
    ) -> tuple[ResolutionResult, EntityMismatch | None]:
        """Resolve a reporting name and check it against the expected entity.

        Convenience helper that combines resolution + mismatch detection for the
        common "tag a fact with entity_id + consolidation_scope" flow (Req 2.2).
        An ambiguous resolution is itself an escalation condition and is surfaced
        via a :class:`EntityMismatch` with no observed entity.
        """
        resolution = self.resolve(reporting_name, case_id=case_id)

        if resolution.status is ResolutionStatus.AMBIGUOUS:
            mismatch = EntityMismatch(
                kind=MismatchKind.ENTITY,
                expected_entity_id=expected_entity_id,
                observed_entity_id=None,
                expected_scope=None,
                observed_scope=consolidation_scope,
                detail=(
                    f"Reporting name {reporting_name!r} is ambiguous across "
                    f"{resolution.candidates!r}; escalate rather than guess."
                ),
            )
            if self._audit is not None:
                self._audit.record(
                    EventType.ENTITY_MISMATCH_DETECTED,
                    case_id=case_id,
                    actor_type=ActorType.SYSTEM,
                    after={
                        "candidates": resolution.candidates,
                        "reporting_name": reporting_name,
                    },
                    reason=mismatch.detail,
                    linked_objects=[x for x in (fact_id,) if x is not None],
                )
            return resolution, mismatch

        observed = resolution.entity_id
        mismatch = self.detect_mismatch(
            expected_entity_id=expected_entity_id,
            observed_entity_id=observed,
            observed_scope=consolidation_scope,
            case_id=case_id,
            fact_id=fact_id,
        )
        return resolution, mismatch
