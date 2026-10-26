"""Read-only standalone M3 preview of an explicitly selected saved output folder.

No database, provider, pipeline execution or generic file-serving endpoint.
"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from app.core.hashing import content_hash

from app.services.bo.corporate_structure import (
    CaseCorporateStructureSource, build_corporate_structure_artifact,
)
from app.services.bo.corporate_structure_render import render_corporate_structure_svg


FILES = frozenset({"m3_analysis.json", "m3_corporate_structure.json",
    "m3_structure_provenance.json", "validation_report.json",
    "evaluation_scorecard.json", "run_manifest.json", "m3_browser_review.json"})
_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
                   autoescape=True)


def create_m3_preview_app(output: Path) -> FastAPI:
    root = output.resolve(strict=True)

    def read(name, default=None):
        path = root / name
        if name not in FILES or path.is_symlink():
            raise HTTPException(404)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(409, "Saved Module 3 output could not be read.") from None

    manifest = read("run_manifest.json")
    if not isinstance(manifest, dict) or not manifest.get("case_id"):
        raise ValueError("Select a saved standalone Module 3 output with run_manifest.json.")
    case_id = manifest["case_id"]
    app = FastAPI(title="Saved Module 3 review", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def private_read_only_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        return response

    def selected(selected_case):
        if selected_case != case_id:
            raise HTTPException(404, "This preview contains one saved case.")
        manifest = read("run_manifest.json")
        validation = read("validation_report.json", {})
        review = read("m3_browser_review.json", {})
        if any(d.get("case_id", case_id) != case_id for d in [manifest, validation, review]):
            raise HTTPException(409, "Saved artifacts refer to different cases.")
        return manifest, validation, review

    @app.get("/")
    def home():
        return RedirectResponse("/m3/" + case_id)

    @app.get("/m3/{selected_case}", response_class=HTMLResponse)
    def page(selected_case: str):
        manifest, validation, review = selected(selected_case)
        analysis = read("m3_analysis.json", {})
        management = analysis.get("senior_management_assessment") or {}
        proposals = review.get("proposals", [])
        counts = {status: sum(p.get("status") == status for p in proposals)
                  for status in ("accepted", "qualified", "rejected", "unverified", "unavailable")}
        structure = read("m3_corporate_structure.json")
        return HTMLResponse(_env.get_template("m3_preview.html.j2").render(
            case_id=case_id, manifest=manifest, validation=validation,
            stages=validation.get("validation_stages") or {}, analysis=analysis,
            management=management, review=review, counts=counts,
            structure_available=bool(structure),
            simulation=(read("m3_structure_provenance.json", {}) or {}).get("is_simulated", False),
            source_documents=manifest.get("source_documents") or [],
            files=sorted(FILES - {"m3_browser_review.json"})))

    @app.get("/m3/{selected_case}/structure.svg")
    def diagram(selected_case: str):
        selected(selected_case)
        payload = read("m3_corporate_structure.json")
        if not payload:
            raise HTTPException(404, "No validated structure available.")
        sidecar = read("m3_structure_provenance.json", {})
        reference = (read("m3_analysis.json", {}) or {}).get("corporate_structure_reference") or {}
        expected_id = "m3struct_" + content_hash({"kind": "m3_corporate_structure",
            "refined": payload, "document_hash": sidecar.get("document_hash")})[:16]
        if sidecar.get("artifact_id") != expected_id or reference.get("artifact_id") != expected_id:
            raise HTTPException(409, "Structure does not match the saved analysis reference.")
        # Re-render the already case-associated saved export, verified against
        # its sidecar and analysis reference; never ingest a filename-matched fixture.
        artifact = build_corporate_structure_artifact(
            CaseCorporateStructureSource(case_id=case_id, registered_case_id=case_id,
                payload=payload, source_document_id=(sidecar.get("provenance") or {}).get("source_document_id")),
            expected_case_id=case_id)
        if artifact.status != "available":
            raise HTTPException(409, "Saved structure did not pass deterministic validation.")
        return Response(render_corporate_structure_svg(artifact, payload.get("visual_spec") or {}),
                        media_type="image/svg+xml")

    @app.get("/m3/{selected_case}/artifacts/{name}")
    def artifact(selected_case: str, name: str):
        selected(selected_case)
        if name not in FILES - {"m3_browser_review.json"}:
            raise HTTPException(404)
        value = read(name)
        if value is None:
            raise HTTPException(404)
        return Response(json.dumps(value, indent=2, ensure_ascii=False), media_type="application/json")

    return app
