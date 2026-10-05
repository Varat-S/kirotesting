"""Local browser entry point for inspecting saved cases."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.models.orm import Case, Snapshot

router = APIRouter(tags=["browser"])
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
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
    cases = [
        {
            "case_id": case.case_id,
            "as_of_date": case.as_of_date,
            "version": latest[case.case_id].snapshot_version
            if case.case_id in latest
            else None,
            "status": ("final" if latest[case.case_id].finalized else "draft")
            if case.case_id in latest
            else "awaiting processing",
        }
        for case in session.scalars(select(Case).order_by(Case.case_id))
    ]
    return HTMLResponse(
        _env.get_template("home.html.j2").render(request=request, cases=cases)
    )
