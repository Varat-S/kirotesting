"""Health-check route."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Return a minimal liveness payload.

    Does not expose any secret values (Requirement 25.2).
    """
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "version": __version__,
    }
