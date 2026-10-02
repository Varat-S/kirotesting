"""Entity service: register entities, tag facts with scope, and detect
borrower/parent and consolidation-scope mismatches."""

from __future__ import annotations

from app.services.entity.registry import EntityRegistry

__all__ = ["EntityRegistry"]
