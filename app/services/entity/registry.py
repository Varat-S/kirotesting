"""Entity registry (Requirement 2.1, 2.5).

Registers :class:`EntityRecord`-shaped entities and preserves the legal borrower
distinct from its consolidated/ultimate parent. Full alias/ticker resolution and
mismatch escalation land in Milestone 2 (task 2.3); here we provide the registry
and the borrower-vs-parent separation guarantee plus a ``entity_registered``
audit event.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import CaseEntity, Entity
from app.schemas.evidence import EntityRecord
from app.services.audit.log import ActorType, AuditLog, EventType


class EntityRegistry:
    """Persist and look up :class:`EntityRecord`s for a case."""

    def __init__(self, session: Session, audit: AuditLog | None = None) -> None:
        self._session = session
        self._audit = audit

    def register(self, record: EntityRecord, case_id: str | None = None) -> Entity:
        """Register an entity. Emits ``entity_registered`` when audit is wired.

        Legal borrower and consolidated parent are stored as distinct rows; the
        schema already rejects an entity being its own parent.
        """
        row = self.get(record.entity_id)
        if row is not None:
            if (
                row.legal_name != record.legal_name
                or row.parent_entity_id != record.parent_entity_id
            ):
                raise ValueError(
                    "Existing global entity identity disagrees; explicit review required."
                )
        else:
            row = Entity(
                entity_id=record.entity_id,
                case_id=None,
                legal_name=record.legal_name,
                aliases=list(record.aliases),
                tickers=list(record.tickers),
                entity_type=record.entity_type.value,
                parent_entity_id=record.parent_entity_id,
                borrower_flag=record.borrower_flag,
                guarantor_flag=record.guarantor_flag,
                jurisdiction=record.jurisdiction,
                source_refs=[ref.model_dump(mode="json") for ref in record.source_refs],
            )
        self._session.add(row)
        self._session.flush()
        if case_id is not None:
            association = self._session.get(CaseEntity, (case_id, record.entity_id))
            if association is None:
                self._session.add(
                    CaseEntity(
                        case_id=case_id,
                        entity_id=record.entity_id,
                        role=record.entity_type.value,
                        borrower_flag=record.borrower_flag,
                        guarantor_flag=record.guarantor_flag,
                        expected_consolidation_scope=record.expected_consolidation_scope,
                    )
                )
                self._session.flush()
            elif (
                association.role,
                association.borrower_flag,
                association.guarantor_flag,
            ) != (
                record.entity_type.value,
                record.borrower_flag,
                record.guarantor_flag,
            ):
                raise ValueError(
                    "Case role already registered; use explicit human review to change it."
                )

        if self._audit is not None:
            self._audit.record(
                EventType.ENTITY_REGISTERED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                after={
                    "entity_id": record.entity_id,
                    "legal_name": record.legal_name,
                    "entity_type": record.entity_type.value,
                    "borrower_flag": record.borrower_flag,
                    "parent_entity_id": record.parent_entity_id,
                },
                linked_objects=[record.entity_id],
            )
        return row

    def get(self, entity_id: str) -> Entity | None:
        """Return a stored entity by id, if present."""
        return self._session.get(Entity, entity_id)

    def for_case(self, case_id: str) -> list[CaseEntity]:
        return list(
            self._session.scalars(
                select(CaseEntity)
                .where(CaseEntity.case_id == case_id)
                .order_by(CaseEntity.entity_id)
            )
        )
