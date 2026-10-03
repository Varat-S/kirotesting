"""Entity service: register entities, tag facts with scope, and detect
borrower/parent and consolidation-scope mismatches."""

from __future__ import annotations

from app.services.entity.registry import EntityRegistry
from app.services.entity.resolution import (
    EntityMismatch,
    EntityResolver,
    MismatchKind,
    ResolutionResult,
    ResolutionStatus,
)

__all__ = [
    "EntityRegistry",
    "EntityResolver",
    "EntityMismatch",
    "MismatchKind",
    "ResolutionResult",
    "ResolutionStatus",
]
