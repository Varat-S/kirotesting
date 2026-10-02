"""Audit service: append-only event log. Events are never modified or deleted.

Exposes the :class:`AuditLog` append interface plus the event/actor enums and
the immutability guard (Requirement 18).
"""

from __future__ import annotations

from app.services.audit.log import (
    ActorType,
    AuditImmutabilityError,
    AuditLog,
    EventType,
    install_immutability_guard,
)

__all__ = [
    "ActorType",
    "AuditImmutabilityError",
    "AuditLog",
    "EventType",
    "install_immutability_guard",
]
