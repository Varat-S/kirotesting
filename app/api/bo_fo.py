"""Business Overview + Financial Overview routes: get a 10-K in, then run both sections concurrently.

* ``GET  /run`` -- the shared filing folders, the two ways to add one, the run form and recent runs
* ``GET  /run/reports?ticker=`` -- a ticker's 10-Ks on SEC EDGAR, to choose which year to download
* ``POST /run/fetch`` -- download the chosen 10-K into ``edgar_filings/``
* ``POST /run/import`` -- upload a 10-K's files (or a .zip) into ``edgar_filings/``
* ``POST /run/start`` -- start both sections in the background over one folder
* ``GET  /run/jobs/{job_id}`` -- a run's progress (JSON, polled by the page)

Runs started here are live (both sections on the configured provider), as on ``/fo``. Offline
runs are available from the CLI (``run-bo-fo --bo-responses ... --fo-responses ...``).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config import get_settings
from app.services import bo_fo
from app.services.acquisition import shared_filing
from app.services.acquisition.sec_edgar import EdgarError, SecEdgarClient
from app.services.audit.log import AuditLog
from app.services.extraction.sec.xml import MAX_FILE_BYTES
from app.services.fo import precheck
from app.services.fo import models as fo_models
from app.services.rms import RmsRequest

router = APIRouter(tags=["business and financial overview"])
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)


def _bo_available() -> tuple[bool, str | None]:
    try:
        bo_fo.bo_live_backend()
    except Exception as exc:  # any configuration fault means the BO cannot run live
        return False, str(exc)
    return True, None


def _page(*, error=None, notice=None, selected_folder=None, job=None, values=None, reports=None, company=None,
          status_code=200):
    settings = get_settings()
    fo_ok, fo_reason = precheck.agent_available()
    bo_ok, bo_reason = _bo_available()
    models, models_note = fo_models.available_models() if fo_ok else ([], None)
    return HTMLResponse(
        _env.get_template("bo_fo_run.html.j2").render(
            filings=shared_filing.list_shared(), selected_folder=selected_folder,
            error=error, notice=notice, job=job, jobs=bo_fo.jobs.recent(), values=values or {},
            reports=reports, company=company,
            sec_ok=bool(settings.sec_user_agent), fo_ok=fo_ok, fo_reason=fo_reason,
            bo_ok=bo_ok, bo_reason=bo_reason, models=models, models_note=models_note,
            selected_model=(values or {}).get("model") or fo_models.configured_model()),
        status_code=status_code)


def _client(session):
    return SecEdgarClient(get_settings().sec_user_agent, audit=AuditLog(session))


@router.get("/run", response_class=HTMLResponse)
def run_page(folder: str = Query(default=""), job: str = Query(default="")) -> HTMLResponse:
    """Shared filings, the fetch and upload forms, and the combined run form."""
    current = bo_fo.jobs.get(job) if job else None
    if job and current is None:
        return _page(error=f"No run {job!r} in this server session (runs are kept in memory).", status_code=404)
    return _page(selected_folder=folder or (current or {}).get("folder"), job=current)


NO_SEC_CONTACT = "Set SEC_USER_AGENT (your name and email) in .env, then restart the app, to use SEC EDGAR."


@router.get("/run/reports", response_class=HTMLResponse)
def list_reports(
    ticker: str = Query(...),
    amendments: str = Query(default=""),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Step 1 of a download: the ticker's 10-Ks on SEC EDGAR, newest first, to choose from."""
    values = {"ticker": ticker, "amendments": amendments}
    if not get_settings().sec_user_agent:
        return _page(error=NO_SEC_CONTACT, values=values, status_code=400)
    try:
        company, reports = shared_filing.annual_reports(
            _client(session), ticker.strip(), include_amendments=bool(amendments))
    except (EdgarError, ValueError) as exc:
        return _page(error=f"Could not list {ticker.strip().upper()}'s 10-Ks: {exc}", values=values,
                     status_code=400)
    if not reports:
        return _page(error=f"SEC EDGAR lists no 10-K for {company['name']} ({company['ticker']}).",
                     values=values, status_code=404)
    return _page(values=values, reports=reports, company=company)


@router.post("/run/fetch")
def fetch_filing(
    ticker: str = Form(...),
    accession: str = Form(...),
    with_proxy: str = Form(default=""),
    session: Session = Depends(get_session),
):
    """Step 2: download the chosen 10-K into edgar_filings/ with its bundle.json and company.json."""
    values = {"ticker": ticker}
    if not get_settings().sec_user_agent:
        return _page(error=NO_SEC_CONTACT, values=values, status_code=400)
    try:
        filing = shared_filing.fetch_filing(
            _client(session), ticker.strip(), accession=accession.strip(), with_proxy=bool(with_proxy))
    except (shared_filing.SharedFilingError, EdgarError, ValueError) as exc:
        return _page(error=f"Download failed: {exc}", values=values, status_code=400)
    return RedirectResponse(f"/run?folder={quote(filing.name)}", status_code=303)


@router.post("/run/import")
async def import_filing(
    files: list[UploadFile] = File(...),
    accession: str = Form(default=""),
    filing_date: str = Form(default=""),
    ticker: str = Form(default=""),
    session: Session = Depends(get_session),
):
    values = {"accession": accession, "filing_date": filing_date, "import_ticker": ticker}
    uploads: dict[str, bytes] = {}
    for upload in files:
        name = Path(upload.filename or "").name
        if not name:
            continue
        data = await upload.read(MAX_FILE_BYTES * 4 + 1)  # a .zip may hold several files
        if name in uploads:
            return _page(error=f"{name!r} was selected twice.", values=values, status_code=400)
        uploads[name] = data
    try:
        parsed_date = date.fromisoformat(filing_date) if filing_date.strip() else None
        client = _client(session) if get_settings().sec_user_agent else None
        filing = shared_filing.import_filing(
            uploads, accession=accession.strip() or None, filing_date=parsed_date,
            ticker=ticker.strip() or None, client=client)
    except (shared_filing.SharedFilingError, EdgarError, ValueError) as exc:
        return _page(error=f"Import failed: {exc}", values=values, status_code=400)
    return RedirectResponse(f"/run?folder={quote(filing.name)}", status_code=303)


@router.post("/run/start")
def start_run(
    folder: str = Form(...),
    case_id: str = Form(default=""),
    model: str = Form(default=""),
    roster: str = Form(default="v2"),
    enable_m4_debt_specialist: str = Form(default=""),
    cutoff: str = Form(default=""),
    requested_amount_m: str = Form(default=""),
    tenor_years: str = Form(default=""),
):
    values = {"case_id": case_id, "model": model, "roster": roster, "cutoff": cutoff,
              "requested_amount_m": requested_amount_m, "tenor_years": tenor_years}
    settings = get_settings()
    try:
        database = bo_fo.sqlite_path(settings.database_url)
        parsed_cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00")) if cutoff.strip() else None
        rms_request = (RmsRequest(float(requested_amount_m), tenor_years=int(tenor_years) if tenor_years.strip()
                                  else None)
                       if requested_amount_m.strip() else None)
        settled = bo_fo.plan(folder, case_id.strip() or None, parsed_cutoff)
        if roster not in ("v2", "v3"):
            raise ValueError("The BO roster is v2 or v3.")
        # The picked model runs both sections: every BO tier and the FO's Pre-check Agent.
        bo_backend = bo_fo.bo_live_backend(model.strip() or None)
        fo_backend = precheck.live_backend(model.strip() or None)
    except Exception as exc:  # a folder, cut-off or provider problem: nothing has started
        return _page(error=f"The run cannot start: {exc}", selected_folder=folder, values=values,
                     status_code=400)
    job_id = bo_fo.jobs.start(
        database=database, folder=folder, case_id=settled["case_id"], cutoff=settled["cutoff"],
        bo_backend=bo_backend, fo_backend=fo_backend, fo_mode="live",
        output_root=settings.output_dir, data_root=settings.data_dir, roster=roster,
        enable_m4_debt_specialist=bool(enable_m4_debt_specialist), rms_request=rms_request)
    return RedirectResponse(f"/run?job={quote(job_id)}", status_code=303)


@router.get("/run/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    job = bo_fo.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown run.")
    return JSONResponse(jsonable_encoder(job), headers={"Cache-Control": "no-store"})
