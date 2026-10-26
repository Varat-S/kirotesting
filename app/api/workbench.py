"""Read-only case workbench routes (Req 24.1-24.4; task 10.1).

Thin HTTP layer over :class:`~app.services.workbench.WorkbenchService`. Every
route is a GET: the workbench is a frozen VIEW and NEVER mutates a case,
snapshot, escalation, or review (Req 24.3). Core assembly logic lives in the
service and is unit-testable without HTTP.

Routes:

* ``GET /cases/{case_id}/workbench`` -- the latest frozen version (or the
  latest draft/evidence view when nothing is finalized), as structured JSON.
* ``GET /cases/{case_id}/workbench/versions/{snapshot_version}`` -- a specific
  draft or final version, exposing its status and predecessor/successor lineage.
* ``GET /cases/{case_id}/workbench.html`` -- an optional deterministic HTML
  rendering of the workbench screen.

``present_source_types`` is an optional repeated query parameter feeding the
deterministic completeness evaluator for the critical-missing-sources count.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.api.deps import get_session
from app.core.config_registry import ConfigRegistry
from app.models.orm import Snapshot
from app.services.reporting.memo import MemoReportGenerator
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
    """Read a specific saved version, exposing whether it is draft or final."""
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
    request: Request,
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
    html = template.render(view=view.to_dict(), request=request)
    return HTMLResponse(content=html)


def _memo(session: Session, case_id: str, snapshot_version: int | None) -> dict:
    query = select(Snapshot).where(
        Snapshot.case_id == case_id, Snapshot.snapshot_type == "final_case"
    )
    if snapshot_version is not None:
        query = query.where(Snapshot.snapshot_version == snapshot_version)
    row = session.scalars(query.order_by(Snapshot.snapshot_version.desc())).first()
    if row is None:
        raise HTTPException(
            status_code=404, detail="No memo snapshot found for this case/version."
        )
    generator = MemoReportGenerator(session)
    generate = (
        generator.generate_json if row.finalized else generator.generate_draft_json
    )
    return generate(case_id=case_id, snapshot_version=row.snapshot_version)


@router.get("/{case_id}/memo.json")
def get_memo_json(
    case_id: str,
    snapshot_version: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    """Read a saved draft or finalized memo without changing its approval state."""
    return _memo(session, case_id, snapshot_version)


# --- Agentic analysis workbench (Milestone 19; read-only) --------------------


@router.get("/{case_id}/agentic/runs")
def list_agentic_runs(
    case_id: str, session: Session = Depends(get_session)
) -> dict:
    """List the agentic analysis runs for a case (multiple runs per snapshot)."""
    from app.services.workbench.agentic import AgenticWorkbenchService

    return {"case_id": case_id,
            "runs": AgenticWorkbenchService(session).runs_for_case(case_id)}


@router.get("/{case_id}/agentic/runs/{analysis_run_id}")
def get_agentic_workbench(
    case_id: str,
    analysis_run_id: str,
    session: Session = Depends(get_session),
) -> dict:
    """Return the read-only agentic workbench view for an explicit run."""
    from app.services.workbench.agentic import (
        AgenticWorkbenchService,
        AnalysisRunNotFoundError,
    )

    try:
        view = AgenticWorkbenchService(session).assemble(analysis_run_id)
    except AnalysisRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if view.case_id != case_id:
        raise HTTPException(status_code=404,
                            detail="Analysis run does not belong to this case.")
    return view.to_dict()


@router.get("/{case_id}/memo.html", response_class=HTMLResponse)
def get_memo_html(
    case_id: str,
    snapshot_version: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    return HTMLResponse(
        MemoReportGenerator(session).render_html(
            _memo(session, case_id, snapshot_version)
        )
    )


# --- Business Overview workbench (BO-only module) -----------------------------
# GET routes are read-only views over recorded artifacts. The single POST
# records the reviewer's explicit decision through the existing human-review
# workflow; nothing here approves a handoff implicitly.


def _bo_call(fn, *args):
    from app.services.bo.workbench import BoRunNotFoundError

    try:
        return fn(*args)
    except BoRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _bo_run_for_case(session: Session, case_id: str, analysis_run_id: str) -> dict:
    from app.services.bo.workbench import bo_view

    view = _bo_call(bo_view, session, analysis_run_id)
    if view["case"]["case_id"] != case_id:
        raise HTTPException(status_code=404,
                            detail="Business Overview run does not belong to this case.")
    return view


@router.get("/{case_id}/bo/runs")
def list_bo_runs(case_id: str, session: Session = Depends(get_session)) -> dict:
    from app.services.bo.workbench import bo_runs

    return {"case_id": case_id, "runs": bo_runs(session, case_id)}


@router.get("/{case_id}/bo/runs/{analysis_run_id}")
def get_bo_run(
    case_id: str, analysis_run_id: str, session: Session = Depends(get_session)
) -> dict:
    return _bo_run_for_case(session, case_id, analysis_run_id)


@router.get("/{case_id}/bo.html", response_class=HTMLResponse)
def get_bo_workbench_html(
    case_id: str,
    analysis_run_id: str | None = Query(default=None),
    credit: bool = Query(default=False),
    fo_run: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """``?credit=1&fo_run=`` (set by the credit view's link): show the way back, and return there after the
    review decision."""
    return _bo_page(session, case_id, analysis_run_id, credit=credit, fo_run=fo_run)


def _bo_page(session, case_id, analysis_run_id, *, credit=False, fo_run=None, error=None, status_code=200):
    from app.api.credit import credit_context
    from app.services.bo.workbench import bo_runs

    runs = bo_runs(session, case_id)
    selected = analysis_run_id or (runs[0]["analysis_run_id"] if runs else None)
    view = _bo_run_for_case(session, case_id, selected) if selected else None
    return HTMLResponse(
        _env.get_template("bo_workbench.html.j2").render(
            runs=runs, view=view, error=error,
            credit=credit_context(case_id, fo_run, selected) if credit else None),
        status_code=status_code)


@router.get("/{case_id}/bo/runs/{analysis_run_id}/{kind}.json")
def get_bo_artifact_json(
    case_id: str, analysis_run_id: str, kind: str,
    session: Session = Depends(get_session),
) -> dict:
    from app.services.bo.workbench import bo_artifact

    _bo_run_for_case(session, case_id, analysis_run_id)
    return _bo_call(bo_artifact, session, analysis_run_id, kind)


@router.get("/{case_id}/bo/runs/{analysis_run_id}/{kind}.html",
            response_class=HTMLResponse)
def get_bo_artifact_html(
    case_id: str, analysis_run_id: str, kind: str,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    from app.services.bo.reporting import render_audit_html, render_memo_html
    from app.services.bo.workbench import bo_artifact

    if kind not in ("memo", "audit"):
        raise HTTPException(status_code=404, detail="Unknown Business Overview page.")
    _bo_run_for_case(session, case_id, analysis_run_id)
    payload = _bo_call(bo_artifact, session, analysis_run_id, kind)
    render = render_memo_html if kind == "memo" else render_audit_html
    return HTMLResponse(render(payload))


@router.post("/{case_id}/bo/runs/{analysis_run_id}/review")
def review_bo_run(
    case_id: str,
    analysis_run_id: str,
    reviewer: str = Form(...),
    reason: str = Form(...),
    decision: str = Form(...),
    acknowledge_unresolved: bool = Form(default=False),
    credit: str = Form(default=""),
    fo_run: str = Form(default=""),
    session: Session = Depends(get_session),
):
    """The reviewer's decision on the handoff. Returns the recorded decision as JSON; from the credit view
    (``credit``), goes back there instead, with RMS re-assessed for its run pair."""
    from app.api.credit import back_to_credit, reassess_for_credit
    from app.services.bo.handoff import HandoffError, review_handoff

    view = _bo_run_for_case(session, case_id, analysis_run_id)
    try:
        result = review_handoff(
            session, view["handoff"]["handoff_id"], reviewer=reviewer,
            reason=reason, decision=decision,
            acknowledge_unresolved=acknowledge_unresolved)
    except HandoffError as exc:
        if credit:
            return _bo_page(session, case_id, analysis_run_id, credit=True, fo_run=fo_run or None,
                            error=str(exc), status_code=409)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if credit:
        notice = (reassess_for_credit(session, case_id, fo_run, analysis_run_id) if fo_run
                  else "Decision recorded.")
        return back_to_credit(case_id, fo_run, analysis_run_id, notice)
    _reassess_rms(session, analysis_run_id)
    return result


def _reassess_rms(session: Session, analysis_run_id: str) -> None:
    """Redo an RMS assessment of this BO run on the reviewer's decision. RMS writes only its own files, so a
    fault there is logged and never undoes the decision just recorded."""
    import logging

    from app.core.config import get_settings
    from app.services.rms.runner import reassess_after_bo_review

    try:
        reassess_after_bo_review(session, analysis_run_id, get_settings().output_dir)
    except Exception:
        logging.getLogger(__name__).exception(
            "RMS re-assessment after the BO decision on %s failed", analysis_run_id)
