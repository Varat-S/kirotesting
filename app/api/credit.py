"""Case credit view: the Business Overview, the Financial Overview and Risk Mitigation & Structuring on one page.

Every change made here is a recorded human decision, and RMS is re-assessed straight after it (with the
assessment's last request), so the page always shows a verdict built from the decisions just made:

* ``GET  /cases/{case_id}/credit.html`` -- the page (``?fo_run=`` / ``?bo_run=`` pick runs; default the latest)
* ``POST /cases/{case_id}/credit/assess`` -- assess (or re-assess) a loan request
* ``POST /cases/{case_id}/credit/fo/{run_id}/override`` -- the analyst's score for an escalated FO measure
* ``POST /cases/{case_id}/credit/fo/{run_id}/review`` -- the analyst's going-concern decision
* ``POST /cases/{case_id}/credit/bo/{analysis_run_id}/review`` -- accept or reject the BO handoff

The decisions go through the same services as the BO and FO workbenches, which keep their own rules (one
decision per item, bound to the recorded content). RMS writes only its own files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config import get_settings
from app.models.orm import AcceptedBusinessOverviewRow, FinancialOverviewRunRow
from app.services.bo.handoff import HandoffError, review_handoff
from app.services.fo.review import FoReviewError, override_measure, review_run
from app.services.rms import RmsRequest
from app.services.rms.case_view import credit_view
from app.services.rms.runner import RmsRunError, assess_case, records

router = APIRouter(tags=["credit"])
log = logging.getLogger(__name__)
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)


def _page(session, case_id, *, fo_run=None, bo_run=None, error=None, notice=None, status_code=200):
    view = credit_view(session, case_id, output_root=get_settings().output_dir, fo_run_id=fo_run, bo_run_id=bo_run)
    if view["fo"] is None and view["bo"] is None:
        raise HTTPException(status_code=404, detail=f"Case {case_id!r} has no BO or FO run.")
    return HTMLResponse(_env.get_template("credit_case.html.j2").render(view=view, error=error, notice=notice),
                        status_code=status_code)


def credit_url(case_id, fo_run=None, bo_run=None, notice=None) -> str:
    """The credit view of these runs. Built from ids only, never from a URL the request supplied."""
    query = urlencode({k: v for k, v in (("fo_run", fo_run), ("bo_run", bo_run), ("notice", notice)) if v})
    return f"/cases/{quote(case_id)}/credit.html" + (f"?{query}" if query else "")


def credit_context(case_id, fo_run=None, bo_run=None) -> dict:
    """What a BO or FO workbench opened from the credit view needs to send the reviewer back to it: the run pair
    the credit view showed, and its address."""
    return {"url": credit_url(case_id, fo_run, bo_run), "fo_run": fo_run or "", "bo_run": bo_run or ""}


def _back(case_id, fo_run, bo_run, notice=None):
    return RedirectResponse(credit_url(case_id, fo_run, bo_run, notice), status_code=303)


def back_to_credit(case_id, fo_run, bo_run, notice):
    """After a decision made on a workbench opened from the credit view."""
    return _back(case_id, fo_run or None, bo_run or None, notice)


def reassess_for_credit(session, case_id, fo_run, bo_run) -> str:
    return _reassess(session, case_id, fo_run or None, bo_run or None)


def _reassess(session, case_id, fo_run, bo_run) -> str:
    """Re-assess these runs with their latest assessment's request, after a decision. Returns a notice."""
    output_root = get_settings().output_dir
    earlier = [r for r in records(output_root, case_id)
               if (r.get("sources") or {}).get("fo_run_id") == fo_run
               and (r.get("sources") or {}).get("bo_analysis_run_id") == bo_run]
    if not earlier:
        return "Decision recorded. Enter a requested loan to assess it."
    try:
        summary = assess_case(session, case_id, output_root=output_root, request=RmsRequest(**earlier[-1]["request"]),
                              fo_run_id=fo_run, bo_run_id=bo_run)
    except Exception:  # the decision is recorded either way; say the assessment didn't follow
        log.exception("RMS re-assessment of case %s failed", case_id)
        return "Decision recorded, but RMS could not re-assess it; see the server log."
    return f"Decision recorded; RMS re-assessed: {summary['verdict'].replace('_', ' ')}."


def _fo_row(session, case_id, run_id):
    row = session.get(FinancialOverviewRunRow, run_id)
    if row is None or row.case_id != case_id:
        raise HTTPException(status_code=404, detail=f"No Financial Overview run {run_id!r} in case {case_id!r}.")
    return row


@router.get("/cases/{case_id}/credit.html", response_class=HTMLResponse)
def credit_page(case_id: str, fo_run: str | None = Query(default=None), bo_run: str | None = Query(default=None),
                notice: str | None = Query(default=None), session: Session = Depends(get_session)):
    return _page(session, case_id, fo_run=fo_run, bo_run=bo_run, notice=notice)


@router.post("/cases/{case_id}/credit/assess")
def credit_assess(
    case_id: str,
    fo_run: str = Form(...),
    bo_run: str = Form(default=""),
    requested_amount_m: str = Form(...),
    tenor_years: str = Form(default=""),
    interest_rate: str = Form(default=""),
    collateral_value_m: str = Form(default=""),
    session: Session = Depends(get_session),
):
    bo_run = bo_run or None
    try:
        request = RmsRequest(
            float(requested_amount_m),
            tenor_years=int(tenor_years) if tenor_years.strip() else None,
            interest_rate=float(interest_rate) if interest_rate.strip() else None,
            collateral_value_m=float(collateral_value_m) if collateral_value_m.strip() else None)
        summary = assess_case(session, case_id, output_root=get_settings().output_dir, request=request,
                              fo_run_id=fo_run, bo_run_id=bo_run)
    except (ValueError, RmsRunError) as exc:  # RmsRunError is a ValueError too
        return _page(session, case_id, fo_run=fo_run, bo_run=bo_run, error=f"Not assessed: {exc}", status_code=400)
    return _back(case_id, fo_run, bo_run, f"Assessed: {summary['verdict'].replace('_', ' ')}.")


@router.post("/cases/{case_id}/credit/fo/{run_id}/override")
def credit_fo_override(
    case_id: str, run_id: str,
    measure: str = Form(...), decision: str = Form(...), score: str = Form(default=""),
    reviewer: str = Form(default=""), reason: str = Form(default=""), bo_run: str = Form(default=""),
    session: Session = Depends(get_session),
):
    _fo_row(session, case_id, run_id)
    try:
        override_measure(session, run_id, measure=measure, decision=decision, reviewer=reviewer, reason=reason,
                         score=score.strip() or None, output_root=get_settings().output_dir)
    except FoReviewError as exc:
        return _page(session, case_id, fo_run=run_id, bo_run=bo_run or None, error=str(exc), status_code=400)
    return _back(case_id, run_id, bo_run or None, _reassess(session, case_id, run_id, bo_run or None))


@router.post("/cases/{case_id}/credit/fo/{run_id}/review")
def credit_fo_review(
    case_id: str, run_id: str,
    decision: str = Form(...), reviewer: str = Form(default=""), reason: str = Form(default=""),
    bo_run: str = Form(default=""), session: Session = Depends(get_session),
):
    _fo_row(session, case_id, run_id)
    try:
        review_run(session, run_id, decision=decision, reviewer=reviewer, reason=reason,
                   output_root=get_settings().output_dir)
    except FoReviewError as exc:
        return _page(session, case_id, fo_run=run_id, bo_run=bo_run or None, error=str(exc), status_code=400)
    return _back(case_id, run_id, bo_run or None, _reassess(session, case_id, run_id, bo_run or None))


@router.post("/cases/{case_id}/credit/bo/{analysis_run_id}/review")
def credit_bo_review(
    case_id: str, analysis_run_id: str,
    decision: str = Form(...), reviewer: str = Form(default=""), reason: str = Form(default=""),
    acknowledge_unresolved: bool = Form(default=False), fo_run: str = Form(default=""),
    session: Session = Depends(get_session),
):
    row = session.query(AcceptedBusinessOverviewRow).filter_by(analysis_run_id=analysis_run_id).one_or_none()
    if row is None or row.case_id != case_id:
        raise HTTPException(status_code=404, detail=f"No Business Overview handoff for {analysis_run_id!r}.")
    try:
        review_handoff(session, row.handoff_id, reviewer=reviewer, reason=reason, decision=decision,
                       acknowledge_unresolved=acknowledge_unresolved)
    except HandoffError as exc:
        return _page(session, case_id, fo_run=fo_run or None, bo_run=analysis_run_id, error=str(exc),
                     status_code=400)
    return _back(case_id, fo_run or None, analysis_run_id,
                 _reassess(session, case_id, fo_run or None, analysis_run_id) if fo_run else "Decision recorded.")
