"""Read-only case workbench routes (Req 24.1-24.4; task 10.1).

Thin HTTP layer over :class:`~app.services.workbench.WorkbenchService`. Every
route is a GET: the workbench is a frozen VIEW and NEVER mutates a case,
snapshot, escalation, or review (Req 24.3). Core assembly logic lives in the
service and is unit-testable without HTTP.

Routes:

* ``GET /cases/{case_id}/workbench`` -- the latest frozen version (or the
  pre-final evidence view when nothing is finalized), as structured JSON.
* ``GET /cases/{case_id}/workbench/versions/{snapshot_version}`` -- a specific
  finalized version, presented frozen.
* ``GET /cases/{case_id}/workbench.html`` -- an optional deterministic HTML
  rendering of the workbench screen.

``present_source_types`` is an optional repeated query parameter feeding the
deterministic completeness evaluator for the critical-missing-sources count.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config_registry import ConfigRegistry
from app.services.ingestion.completeness import CompletenessEvaluator
from app.services.workbench import (
    CaseNotFoundError,
    SnapshotVersionNotFoundError,
    WorkbenchService,
    WorkbenchView,
)

router = APIRouter(prefix="/cases", tags=["workbench"])

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_WORKBENCH_TEMPLATE = "workbench.html.j2"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def _service(session: Session) -> WorkbenchService:
    # The completeness evaluator is config-driven; wiring it in lets the
    # critical-missing-sources count be computed when the caller supplies the
    # present source types and a source profile is configured.
    completeness = CompletenessEvaluator(ConfigRegistry(session))
    return WorkbenchService(session, completeness=completeness)


def _assemble(
    session: Session,
    case_id: str,
    *,
    snapshot_version: int | None,
    present_source_types: list[str] | None,
) -> WorkbenchView:
    service = _service(session)
    try:
        return service.assemble(
            case_id,
            snapshot_version=snapshot_version,
            present_source_types=present_source_types,
        )
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SnapshotVersionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{case_id}/workbench")
def get_workbench(
    case_id: str,
    present_source_types: list[str] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Return the frozen workbench view-model for a case (latest version)."""
    view = _assemble(
        session,
        case_id,
        snapshot_version=None,
        present_source_types=present_source_types,
    )
    return view.to_dict()


@router.get("/{case_id}/workbench/versions/{snapshot_version}")
def get_workbench_version(
    case_id: str,
    snapshot_version: int,
    present_source_types: list[str] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Return a specific finalized version, presented frozen (Req 24.3)."""
    view = _assemble(
        session,
        case_id,
        snapshot_version=snapshot_version,
        present_source_types=present_source_types,
    )
    return view.to_dict()


@router.get("/{case_id}/workbench.html", response_class=HTMLResponse)
def get_workbench_html(
    case_id: str,
    snapshot_version: int | None = Query(default=None),
    present_source_types: list[str] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the workbench screen as deterministic HTML (optional surface)."""
    view = _assemble(
        session,
        case_id,
        snapshot_version=snapshot_version,
        present_source_types=present_source_types,
    )
    template = _env.get_template(_WORKBENCH_TEMPLATE)
    html = template.render(view=view.to_dict())
    return HTMLResponse(content=html)
