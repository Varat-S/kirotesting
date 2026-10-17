"""Deterministic JSON and HTML output for the Structural Overview thin slice."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.hashing import canonical_json
from app.schemas.structural_overview import StructuralOverviewAssessment
from app.services.structural_overview.adapter import assessment_content_hash


_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "reporting" / "templates"


def render_assessment_html(payload: dict[str, Any] | StructuralOverviewAssessment) -> str:
    """Render only normalized assessment data; never inspect BO source records."""
    assessment = payload if isinstance(payload, StructuralOverviewAssessment) else StructuralOverviewAssessment.model_validate(payload)
    if assessment_content_hash(assessment) != assessment.content_hash:
        raise ValueError("Assessment content hash is invalid; refusing to render.")
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        keep_trailing_newline=True,
    )
    return env.get_template("structural_overview.html.j2").render(
        assessment=assessment.model_dump(mode="json")
    )


def write_artifacts(
    assessment: dict[str, Any] | StructuralOverviewAssessment,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """Write canonical assessment JSON first, then render HTML from that JSON."""
    model = assessment if isinstance(assessment, StructuralOverviewAssessment) else StructuralOverviewAssessment.model_validate(assessment)
    if assessment_content_hash(model) != model.content_hash:
        raise ValueError("Assessment content hash is invalid; refusing to write.")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "assessment.json"
    html_path = output / "assessment.html"
    json_path.write_text(canonical_json(model.model_dump(mode="json")) + "\n", encoding="utf-8", newline="\n")
    frozen = json.loads(json_path.read_text(encoding="utf-8"))
    html_path.write_text(render_assessment_html(frozen), encoding="utf-8", newline="\n")
    return json_path, html_path
