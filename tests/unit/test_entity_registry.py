"""Entity registry tests: borrower-vs-parent separation (Req 2.1, 2.5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.orm import Case, Entity
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord, SourceRef
from app.services.audit.log import AuditLog
from app.services.entity.registry import EntityRegistry

REF = SourceRef(document_id="10K-2024", page=1)


def _case(session: Session) -> str:
    session.add(Case(case_id="DAL_2024"))
    session.flush()
    return "DAL_2024"


def test_borrower_and_parent_are_distinct_records(db_session: Session) -> None:
    """Req 2.5: legal borrower preserved distinct from consolidated parent."""
    case_id = _case(db_session)
    reg = EntityRegistry(db_session)

    parent = EntityRecord(
        entity_id="DAL_GROUP",
        legal_name="Delta Air Lines, Inc. (Consolidated)",
        entity_type=EntityType.PARENT,
        source_refs=[REF],
    )
    borrower = EntityRecord(
        entity_id="DAL_OPCO",
        legal_name="Delta Air Lines, Inc. (Borrower)",
        entity_type=EntityType.BORROWER,
        parent_entity_id="DAL_GROUP",
        borrower_flag=True,
        source_refs=[REF],
    )
    reg.register(parent, case_id=case_id)
    reg.register(borrower, case_id=case_id)

    stored_parent = reg.get("DAL_GROUP")
    stored_borrower = reg.get("DAL_OPCO")

    assert stored_parent.entity_id != stored_borrower.entity_id
    assert stored_borrower.parent_entity_id == stored_parent.entity_id
    assert stored_borrower.borrower_flag is True
    assert stored_parent.borrower_flag is False
    assert db_session.query(Entity).count() == 2


def test_entity_cannot_be_its_own_parent() -> None:
    """Guards against collapsing borrower into its own parent."""
    with pytest.raises(ValidationError):
        EntityRecord(
            entity_id="DAL",
            legal_name="Delta",
            entity_type=EntityType.BORROWER,
            parent_entity_id="DAL",
            source_refs=[REF],
        )


def test_register_emits_entity_registered_audit_event(db_session: Session) -> None:
    """Req 18.2: entity registration emits an audit event."""
    case_id = _case(db_session)
    audit = AuditLog(db_session)
    reg = EntityRegistry(db_session, audit=audit)

    reg.register(
        EntityRecord(
            entity_id="DAL_OPCO",
            legal_name="Delta Air Lines, Inc.",
            entity_type=EntityType.BORROWER,
            borrower_flag=True,
            source_refs=[REF],
        ),
        case_id=case_id,
    )
    events = audit.events_for_case(case_id)
    assert [e.event_type for e in events] == ["entity_registered"]
    assert events[0].after["entity_id"] == "DAL_OPCO"
