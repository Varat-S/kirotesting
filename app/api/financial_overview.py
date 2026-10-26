"""Financial Overview (FO) routes: run list, run form, per-case workbench and artifacts.

The FO counterpart of the Business Overview routes, without agents. Only ``POST /fo/run``
writes (it records one new run); every other route is a read of recorded runs.

* ``GET  /fo`` -- recorded runs and the run form (folders in ``edgar_filings/``)
* ``POST /fo/run`` -- run the FO engine over one folder, then open its workbench
* ``POST /fo/run/cancel`` -- stop a run in progress (its form token); it records nothing
* ``GET  /fo/run/progress`` -- the step a run in progress is on (its form token), polled by the run form
* ``GET  /cases/{case_id}/fo.html`` -- the case's FO workbench (``?run_id=`` picks a run)
* ``POST /cases/{case_id}/fo/runs/{run_id}/review`` -- the analyst's decision on a going-concern fail
* ``POST /cases/{case_id}/fo/runs/{run_id}/override`` -- the analyst's score for an escalated measure
* ``GET  /cases/{case_id}/fo/runs`` and ``/fo/runs/{run_id}`` -- JSON
* ``GET  /cases/{case_id}/fo/runs/{run_id}/overview.json|.html`` -- the recorded scorecard
* ``GET  /cases/{case_id}/fo/runs/{run_id}/memo.json|.html`` -- the Financial Overview memo
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.api.credit import back_to_credit, credit_context, reassess_for_credit
from app.api.deps import get_session
from app.core.config import get_settings
from app.services.fo import loader, precheck
from app.services.fo import cancel as fo_cancel
from app.services.fo import models as fo_models
from app.services.fo.memo import build_memo, filing_source, render_memo_html
from app.services.fo.reporting import render_overview_html
from app.services.fo.review import (
    FoReviewError,
    override_measure,
    overrides_for,
    review_for,
    review_run,
    review_view,
    run_header,
)
from app.services.fo.runner import FinancialOverviewRunner, FoRunError
from app.services.rms.runner import reassess_after_fo_review
from app.services.fo.workbench import FoRunNotFoundError, fo_artifact, fo_run, fo_runs, fo_view

router = APIRouter(tags=["financial_overview"])
log = logging.getLogger(__name__)
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)


def _page(session, *, view=None, runs=None, error=None, selected_folder=None, case_id=None, selected_model=None,
          stopped=None, failed=None, credit=None, status_code=200):
    agent_ok, agent_reason = precheck.agent_available()
    models, models_note = fo_models.available_models() if agent_ok else ([], None)
    return HTMLResponse(
        _env.get_template("fo_workbench.html.j2").render(
            runs=fo_runs(session) if runs is None else runs, filings=loader.list_filings(), view=view,
            error=error, stopped=stopped, failed=failed, selected_folder=selected_folder, case_id=case_id,
            agent_ok=agent_ok, agent_reason=agent_reason, models=models, models_note=models_note,
            selected_model=selected_model or fo_models.configured_model(), credit=credit,
            run_token=secrets.token_urlsafe(16)),  # identifies the next run, so its Stop button can reach it
        status_code=status_code)


def _run_for_case(session: Session, case_id: str, run_id: str):
    try:
        row = fo_run(session, run_id)
    except FoRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if row.case_id != case_id:
        raise HTTPException(status_code=404, detail="Financial Overview run does not belong to this case.")
    return row


@router.get("/fo", response_class=HTMLResponse)
def financial_overview_runs(session: Session = Depends(get_session)) -> HTMLResponse:
    """All recorded FO runs and the form to start a new one."""
    return _page(session)


@router.post("/fo/run")
def run_financial_overview(
    folder: str = Form(...),
    case_id: str = Form(default=""),
    model: str = Form(default=""),
    run_token: str = Form(default=""),
    session: Session = Depends(get_session),
):
    """Run the FO engine over one ``edgar_filings/`` folder and record the result.

    The Pre-check Agent always runs on the configured live provider, on the model picked in the form (a
    text-analysis model; the configured one by default); without one the run is refused (the keyword rules never
    decide in its place)."""
    again = {"selected_folder": folder, "case_id": case_id, "selected_model": model}
    try:
        backend = precheck.live_backend(model.strip()) if model.strip() else precheck.live_backend()
    except precheck.AgentNotConfiguredError as exc:
        return _page(session, error=f"The Pre-check Agent is required and not available: {exc}", status_code=400,
                     **again)
    try:
        result = FinancialOverviewRunner(session, output_root=get_settings().output_dir,
                                         agent_backend=backend, agent_mode="live",
                                         check_stop=fo_cancel.checker(run_token)).run_filing(
            folder, case_id.strip() or None)
    except fo_cancel.FoRunCancelled as exc:
        return _page(session, stopped=str(exc), **again)
    except (loader.FilingFolderError, FoRunError) as exc:
        return _page(session, error=str(exc), failed=fo_cancel.progress(run_token), status_code=400, **again)
    except precheck.PrecheckAgentError as exc:
        return _page(session, error=str(exc), failed=fo_cancel.progress(run_token), status_code=502, **again)
    except Exception as exc:  # an unreadable filing, or a fault in a later step, must not surface as a 500
        failed = fo_cancel.progress(run_token)
        reading = (failed["step"] or "reading the filing").startswith("reading the filing")
        return _page(session, error=(f"Could not read this filing as Inline XBRL: {exc}" if reading
                                     else f"Unexpected error ({type(exc).__name__}): {exc}"),
                     failed=failed, status_code=422 if reading else 500, **again)
    finally:
        fo_cancel.clear(run_token)
    return RedirectResponse(
        f"/cases/{quote(result.case_id)}/fo.html?run_id={quote(result.run_id)}", status_code=303)


@router.post("/fo/run/cancel")
def cancel_financial_overview_run(run_token: str = Form(default="")) -> dict:
    """Stop the run started with this token (the run form's Stop button). The run ends at its next step and records
    nothing; a model call already in flight finishes first."""
    if not fo_cancel.request_stop(run_token):
        raise HTTPException(status_code=400, detail="Unknown run token.")
    return {"stopping": True}


@router.get("/fo/run/progress")
def financial_overview_run_progress(run_token: str = Query(default="")) -> dict:
    """The step the run started with this token is on, its number, the steps done and the seconds elapsed (the run
    form polls it while the run is under way). Empty before the run's first step and after it ends."""
    return fo_cancel.progress(run_token)


@router.get("/cases/{case_id}/fo.html", response_class=HTMLResponse)
def financial_overview_workbench(
    case_id: str,
    run_id: str | None = Query(default=None),
    credit: bool = Query(default=False),
    bo_run: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """``?credit=1&bo_run=`` (set by the credit view's link): show the way back, and return there after a decision."""
    runs = fo_runs(session, case_id)
    selected = run_id or (runs[0]["run_id"] if runs else None)
    view = None
    if selected:
        _run_for_case(session, case_id, selected)
        view = fo_view(session, selected)
        saved = Path(get_settings().output_dir) / "fo" / case_id / selected
        view["output_folder"] = str(saved.resolve()) if saved.is_dir() else None
    return _page(session, view=view, runs=runs,
                 credit=credit_context(case_id, selected, bo_run) if credit else None)


def _reassess_rms(session: Session, run_id: str) -> None:
    """Redo an RMS assessment of this run on the analyst's decision. RMS writes only its own files, so a fault
    there is logged and never undoes the decision just recorded."""
    try:
        reassess_after_fo_review(session, run_id, get_settings().output_dir)
    except Exception:
        log.exception("RMS re-assessment after the FO decision on %s failed", run_id)


@router.post("/cases/{case_id}/fo/runs/{run_id}/review")
def review_financial_overview_run(
    case_id: str,
    run_id: str,
    decision: str = Form(...),
    reviewer: str = Form(default=""),
    reason: str = Form(default=""),
    credit: str = Form(default=""),
    bo_run: str = Form(default=""),
    session: Session = Depends(get_session),
):
    """Record the analyst's continue / stop decision on a going-concern fail."""
    _run_for_case(session, case_id, run_id)
    try:
        review_run(session, run_id, decision=decision, reviewer=reviewer, reason=reason,
                   output_root=get_settings().output_dir)
    except FoReviewError as exc:
        return _decision_error(session, case_id, run_id, exc, credit, bo_run)
    return _after_decision(session, case_id, run_id, credit, bo_run)


@router.post("/cases/{case_id}/fo/runs/{run_id}/override")
def override_financial_overview_measure(
    case_id: str,
    run_id: str,
    measure: str = Form(...),
    decision: str = Form(...),
    score: str = Form(default=""),
    reviewer: str = Form(default=""),
    reason: str = Form(default=""),
    credit: str = Form(default=""),
    bo_run: str = Form(default=""),
    session: Session = Depends(get_session),
):
    """Record the analyst's score for an escalated measure: accept the engine's, or override it."""
    _run_for_case(session, case_id, run_id)
    try:
        override_measure(session, run_id, measure=measure, decision=decision, reviewer=reviewer, reason=reason,
                         score=score.strip() or None, output_root=get_settings().output_dir)
    except FoReviewError as exc:
        return _decision_error(session, case_id, run_id, exc, credit, bo_run)
    return _after_decision(session, case_id, run_id, credit, bo_run)


def _after_decision(session, case_id, run_id, credit, bo_run):
    """Back to where the analyst came from: the credit view (re-assessed for its run pair) when the workbench
    was opened from it, else this run's workbench."""
    if credit:
        return back_to_credit(case_id, run_id, bo_run,
                              reassess_for_credit(session, case_id, run_id, bo_run))
    _reassess_rms(session, run_id)
    return RedirectResponse(f"/cases/{quote(case_id)}/fo.html?run_id={quote(run_id)}", status_code=303)


def _decision_error(session, case_id, run_id, exc, credit, bo_run):
    return _page(session, view=fo_view(session, run_id), runs=fo_runs(session, case_id), error=str(exc),
                 credit=credit_context(case_id, run_id, bo_run) if credit else None, status_code=400)


@router.get("/cases/{case_id}/fo/runs")
def list_financial_overview_runs(case_id: str, session: Session = Depends(get_session)) -> dict:
    return {"case_id": case_id, "runs": fo_runs(session, case_id)}


@router.get("/cases/{case_id}/fo/runs/{run_id}")
def get_financial_overview_run(case_id: str, run_id: str, session: Session = Depends(get_session)) -> dict:
    _run_for_case(session, case_id, run_id)
    view = fo_view(session, run_id)
    return {k: v for k, v in view.items() if k != "overview"}


def _memo(session: Session, case_id: str, run_id: str) -> dict:
    row = _run_for_case(session, case_id, run_id)
    return build_memo(dict(row.payload), run=run_header(row), review=review_view(review_for(session, run_id)),
                      overrides=overrides_for(session, run_id), source=filing_source(row.filing_folder))


@router.get("/cases/{case_id}/fo/runs/{run_id}/memo.json")
def get_financial_overview_memo_json(case_id: str, run_id: str, session: Session = Depends(get_session)) -> dict:
    """The Financial Overview memo for one run, built from the recorded run (with any review and overrides)."""
    return _memo(session, case_id, run_id)


@router.get("/cases/{case_id}/fo/runs/{run_id}/memo.html", response_class=HTMLResponse)
def get_financial_overview_memo_html(case_id: str, run_id: str,
                                     session: Session = Depends(get_session)) -> HTMLResponse:
    return HTMLResponse(render_memo_html(_memo(session, case_id, run_id)))


@router.get("/cases/{case_id}/fo/runs/{run_id}/{kind}.json")
def get_financial_overview_artifact(
    case_id: str, run_id: str, kind: str, session: Session = Depends(get_session)
) -> dict:
    _run_for_case(session, case_id, run_id)
    try:
        return fo_artifact(session, run_id, kind)
    except FoRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/cases/{case_id}/fo/runs/{run_id}/overview.html", response_class=HTMLResponse)
def get_financial_overview_html(case_id: str, run_id: str, session: Session = Depends(get_session)) -> HTMLResponse:
    row = _run_for_case(session, case_id, run_id)
    return HTMLResponse(render_overview_html(dict(row.payload), run=run_header(row),
                                             review=review_view(review_for(session, run_id)),
                                             overrides=overrides_for(session, run_id)))
