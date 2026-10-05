"""Local browser entry point for inspecting saved cases."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.models.orm import Case, Snapshot, EvidencePreview
from app.services.pipeline.default_case import DEFAULT_CASE_ID

router = APIRouter(tags=["browser"])
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    snapshots = list(
        session.scalars(
            select(Snapshot)
            .where(Snapshot.snapshot_type == "final_case")
            .order_by(Snapshot.snapshot_version)
        )
    )
    latest = {row.case_id: row for row in snapshots}
    previews = {
        row.case_id: row.evidence_version
        for row in session.scalars(
            select(EvidencePreview).order_by(EvidencePreview.evidence_version)
        )
    }
    cases = [
        {
            "case_id": case.case_id,
            "as_of_date": case.as_of_date,
            "version": latest[case.case_id].snapshot_version
            if case.case_id in latest
            else None,
            "status": ("final" if latest[case.case_id].finalized else "draft")
            if case.case_id in latest
            else "evidence review"
            if case.case_id in previews
            else "awaiting processing",
            "preview_version": previews.get(case.case_id),
        }
        for case in session.scalars(select(Case).order_by(Case.case_id))
    ]
    return HTMLResponse(
        _env.get_template("home.html.j2").render(
            request=request,
            cases=cases,
            default_preview_ready=DEFAULT_CASE_ID in previews,
            default_case_id=DEFAULT_CASE_ID,
        )
    )
