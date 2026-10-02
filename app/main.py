"""FastAPI application entrypoint for the AI-Assisted Credit Memo Generator.

Run locally with::

    uvicorn app.main:app --reload

This is a scaffold entrypoint. Stage-specific routers (cases, documents,
entities, extraction, metrics, analysis, escalations, reviews, outputs) are
added in later milestones.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router


def create_app() -> FastAPI:
    """Application factory. Builds and returns the FastAPI instance."""
    app = FastAPI(
        title="AI-Assisted Credit Memo Generator (PoC)",
        version=__version__,
    )
    app.include_router(health_router)
    return app


app = create_app()
