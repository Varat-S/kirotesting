"""Entity resolution + mismatch tests (Req 2.2, 2.3, 2.4, 2.6).

Covers task 2.5: a seeded wrong-entity attribution triggers a conflict /
escalation signal and the values are never merged; ambiguous identity escalates
rather than guessing.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.orm import Case
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord, SourceRef
from app.services.audit.log import AuditLog
from app.services.entity import (
    EntityRegistry,
    EntityResolver,
    MismatchKind,
    ResolutionStatus,
)

REF = SourceRef(document_id="10K-2024", page=1)


def _seed(session: Session) -> tuple[str, EntityRegistry]:
    session.add(Case(case_id="DAL_2024"))
    session.flush()
    reg = EntityRegistry(session)
    reg.register(
        EntityRecord(
            entity_id="DAL_GROUP",
            legal_name="Delta Air Lines, Inc. (Consolidated)",
            aliases=["Delta Group"],
            tickers=["DAL"],
            entity_type=EntityType.PARENT,
            source_refs=[REF],
        ),
        case_id="DAL_2024",
    )
    reg.register(
        EntityRecord(
            entity_id="DAL_OPCO",
            legal_name="Delta Air Lines Operating Co.",
            aliases=["Delta OpCo"],
            entity_type=EntityType.BORROWER,
            parent_entity_id="DAL_GROUP",
            borrower_flag=True,
            source_refs=[REF],
        ),
        case_id="DAL_2024",
    )
    return "DAL_2024", reg


def test_resolve_by_alias_and_ticker(db_session: Session) -> None:
    """Req 2.2: aliases/tickers/reporting names map to entity_id."""
    case_id, _ = _seed(db_session)
    resolver = EntityResolver(db_session)

    by_ticker = resolver.resolve("DAL", case_id=case_id)
    assert by_ticker.status is ResolutionStatus.RESOLVED
    assert by_ticker.entity_id == "DAL_GROUP"

    by_alias = resolver.resolve("delta opco", case_id=case_id)
    assert by_alias.status is ResolutionStatus.RESOLVED
    assert by_alias.entity_id == "DAL_OPCO"


def test_ambiguous_name_escalates_not_guesses(db_session: Session) -> None:
    """Req 2.4: ambiguous identity escalates rather than guessing."""
    case_id = "DAL_2024"
    db_session.add(Case(case_id=case_id))
    db_session.flush()
    reg = EntityRegistry(db_session)
    for eid in ("E1", "E2"):
        reg.register(
            EntityRecord(
                entity_id=eid,
                legal_name=f"{eid} name",
                aliases=["Shared Alias"],
                entity_type=EntityType.OTHER,
                source_refs=[REF],
            ),
            case_id=case_id,
        )
    resolver = EntityResolver(db_session)
    res = resolver.resolve("Shared Alias", case_id=case_id)
    assert res.status is ResolutionStatus.AMBIGUOUS
    assert set(res.candidates) == {"E1", "E2"}


def test_wrong_entity_triggers_mismatch_and_audit(db_session: Session) -> None:
    """Req 2.6: wrong-entity attribution raises a conflict/escalation signal."""
    case_id, _ = _seed(db_session)
    audit = AuditLog(db_session)
    resolver = EntityResolver(db_session, audit=audit)

    # A fact expected to belong to the borrower was tagged with the parent.
    mismatch = resolver.detect_mismatch(
        expected_entity_id="DAL_OPCO",
        observed_entity_id="DAL_GROUP",
        case_id=case_id,
        fact_id="FACT-REVENUE-FY24",
    )
    assert mismatch is not None
    assert mismatch.kind is MismatchKind.ENTITY
    assert mismatch.expected_entity_id == "DAL_OPCO"
    assert mismatch.observed_entity_id == "DAL_GROUP"

    types = [e.event_type for e in audit.events_for_case(case_id)]
    assert "entity_mismatch_detected" in types


def test_scope_mismatch_detected(db_session: Session) -> None:
    """Req 2.3: differing consolidation scope is a mismatch (never merged)."""
    case_id, _ = _seed(db_session)
    resolver = EntityResolver(db_session)
    mismatch = resolver.detect_mismatch(
        expected_entity_id="DAL_OPCO",
        observed_entity_id="DAL_OPCO",
        expected_scope="standalone",
        observed_scope="consolidated",
        case_id=case_id,
    )
    assert mismatch is not None
    assert mismatch.kind is MismatchKind.CONSOLIDATION_SCOPE


def test_matching_entity_and_scope_no_mismatch(db_session: Session) -> None:
    case_id, _ = _seed(db_session)
    resolver = EntityResolver(db_session)
    mismatch = resolver.detect_mismatch(
        expected_entity_id="DAL_OPCO",
        observed_entity_id="DAL_OPCO",
        expected_scope="consolidated",
        observed_scope="consolidated",
        case_id=case_id,
    )
    assert mismatch is None


def test_tag_fact_scope_ambiguous_escalates(db_session: Session) -> None:
    """Ambiguous reporting-name tag yields an escalation signal."""
    case_id = "DAL_2024"
    db_session.add(Case(case_id=case_id))
    db_session.flush()
    reg = EntityRegistry(db_session)
    for eid in ("A", "B"):
        reg.register(
            EntityRecord(
                entity_id=eid,
                legal_name=f"{eid}",
                aliases=["Ambiguous Co"],
                entity_type=EntityType.OTHER,
                source_refs=[REF],
            ),
            case_id=case_id,
        )
    resolver = EntityResolver(db_session, audit=AuditLog(db_session))
    resolution, mismatch = resolver.tag_fact_scope(
        reporting_name="Ambiguous Co",
        consolidation_scope="consolidated",
        case_id=case_id,
        fact_id="F1",
    )
    assert resolution.status is ResolutionStatus.AMBIGUOUS
    assert mismatch is not None
