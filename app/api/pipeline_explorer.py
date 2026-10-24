"""Read-only Pipeline Explorer — an interactive left-to-right dataflow view over
recorded BO / FO / M3 artifacts, with a per-node inspector and JSON/PDF export.

Every route is a read-only GET. Nothing here runs a model, mutates an artifact or
generates a credit conclusion; it only indexes and re-presents recorded outputs.
Artifact access is bounded by :mod:`app.services.registry.explorer` so only files
under the configured output root or the registered demo fixtures are reachable.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader

from app.core import get_settings
from app.core.hashing import canonical_json
from app.services.registry.explorer import (
    ArtifactAccessError,
    discover_cases,
    graph_for_case,
    inspect_node,
    load_artifact,
    manifest_for_case,
)
from app.services.registry.json_render import render_json_html, render_json_pdf

router = APIRouter(tags=["pipeline_explorer"])
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)


def _output_dir() -> str:
    return get_settings().output_dir


@router.get("/pipeline", response_class=HTMLResponse)
def pipeline_home() -> HTMLResponse:
    """Case picker + the interactive graph shell (renders even with no runs)."""
    cases = discover_cases(_output_dir(), repo_root=".")
    return HTMLResponse(
        _env.get_template("pipeline_explorer.html.j2").render(
            cases=[{"case_id": c.case_id, "label": c.label, "kind": c.kind,
                    "note": c.note} for c in cases],
        )
    )


@router.get("/pipeline/{case_id}/graph.json")
def pipeline_graph(case_id: str) -> JSONResponse:
    """The dataflow graph (nodes + declared-dependency edges) for a case."""
    graph = graph_for_case(case_id, _output_dir(), repo_root=".")
    return JSONResponse(graph)


@router.get("/pipeline/{case_id}/manifest.json")
def pipeline_manifest(case_id: str) -> Response:
    """The versioned artifact manifest for a case."""
    manifest = manifest_for_case(case_id, _output_dir(), repo_root=".")
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
    return Response(
        content=canonical_json(manifest.model_dump(mode="json")) + "\n",
        media_type="application/json",
    )


@router.get("/pipeline/{case_id}/node/{artifact_id}")
def pipeline_node(case_id: str, artifact_id: str) -> JSONResponse:
    """The node-inspector payload for one artifact (reason, not mock, if absent)."""
    try:
        info = inspect_node(case_id, artifact_id, _output_dir(), repo_root=".")
    except ArtifactAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(info)


@router.get("/pipeline/{case_id}/node/{artifact_id}/view.html",
            response_class=HTMLResponse)
def pipeline_node_html(case_id: str, artifact_id: str) -> HTMLResponse:
    """Generic hierarchical HTML viewer for an artifact's JSON."""
    try:
        payload, _ = load_artifact(case_id, artifact_id, _output_dir(), repo_root=".")
    except ArtifactAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return HTMLResponse(render_json_html(payload, title=artifact_id))


@router.get("/pipeline/{case_id}/node/{artifact_id}/download.json")
def pipeline_node_json(case_id: str, artifact_id: str) -> Response:
    """Download the exact recorded JSON for an artifact (canonical form)."""
    try:
        payload, _ = load_artifact(case_id, artifact_id, _output_dir(), repo_root=".")
    except ArtifactAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_name = artifact_id.replace("/", "_").replace("..", "_")
    return Response(
        content=canonical_json(payload) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )


@router.get("/pipeline/{case_id}/node/{artifact_id}/export.pdf")
def pipeline_node_pdf(case_id: str, artifact_id: str) -> Response:
    """Deterministic PDF export of an artifact's JSON (readable sections/tables)."""
    try:
        payload, manifest = load_artifact(
            case_id, artifact_id, _output_dir(), repo_root=".")
    except ArtifactAccessError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    entry = manifest.by_id(artifact_id)
    subtitle = f"Case {case_id} · {artifact_id}"
    if entry is not None and entry.schema_name:
        subtitle += f" · schema {entry.schema_name}"
    pdf = render_json_pdf(payload, title=artifact_id, subtitle=subtitle)
    safe_name = artifact_id.replace("/", "_").replace("..", "_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_name}.pdf"'},
    )
