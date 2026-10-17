"""Export a recorded analysis run's per-agent inputs and outputs to PDF.

This is a read-only evaluation utility.  It joins ``agent_runs`` to the
immutable ``model_run_*`` stage artifacts, writes a canonical JSON companion,
and renders the same payload as a PDF.  It never reads ``.env`` or contacts a
model provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import textwrap
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy import select

from app.core.hashing import canonical_json, content_hash
from app.models.base import create_engine_and_session
from app.models.orm import AgentRunRow, AgenticAnalysisRun, ModelRun, StageArtifact
from app.services.pipeline.artifacts import verified_artifacts


REPORT_VERSION = "agent-io-evaluation-1.0"
_SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "credential", "credentials",
    "password", "private_key", "secret", "access_token", "refresh_token",
}
_USEFUL_MODEL_CONFIG_KEYS = {
    "provider", "model", "temperature", "top_p", "max_output_tokens",
    "input_tokens", "output_tokens", "total_tokens", "thinking_tokens",
    "cached_content_token_count", "latency_ms", "finish_reason",
}


class AgentReportError(RuntimeError):
    """Recorded run data is incomplete or cannot be rendered safely."""


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or any(marker in normalized for marker in (
        "api_key", "authorization", "credential", "password", "private_key",
        "secret", "access_token", "refresh_token",
    ))


def _redact(value: Any) -> Any:
    """Remove credential-shaped mapping values while preserving analytical data."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_model_config(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}
    return _redact({key: config[key] for key in sorted(config) if key in _USEFUL_MODEL_CONFIG_KEYS})


def _created_distance(left: datetime | None, right: datetime | None) -> float:
    if left is None or right is None:
        return 0.0
    return abs((left - right).total_seconds())


def _match_model_runs(
    agent_runs: list[AgentRunRow], model_runs: list[ModelRun]
) -> dict[str, ModelRun | None]:
    """Match duplicate audit rows without relying on their independently assigned IDs."""
    available = {row.run_id: row for row in model_runs}
    matched: dict[str, ModelRun | None] = {}
    for agent in agent_runs:
        if agent.reused_from_cache:
            matched[agent.run_id] = None
            continue
        candidates = [
            model for model in available.values()
            if model.case_id == agent.case_id
            and model.case_version == agent.snapshot_version
            and model.prompt_id == agent.prompt_id
            and model.model_id == agent.model_id
            and model.raw_response == agent.raw_response
        ]
        if not candidates:
            raise AgentReportError(
                f"No recorded model run resolves agent run {agent.run_id} ({agent.agent_id})."
            )
        chosen = min(candidates, key=lambda model: _created_distance(agent.created_at, model.created_at))
        matched[agent.run_id] = chosen
        available.pop(chosen.run_id)
    return matched


def build_report_payload(session, analysis_run_id: str) -> dict[str, Any]:
    run = session.get(AgenticAnalysisRun, analysis_run_id)
    if run is None:
        raise AgentReportError(f"Analysis run {analysis_run_id!r} does not exist.")

    agent_runs = list(session.scalars(
        select(AgentRunRow)
        .where(AgentRunRow.analysis_run_id == analysis_run_id)
        .order_by(AgentRunRow.created_at, AgentRunRow.run_id)
    ))
    if not agent_runs:
        raise AgentReportError(f"Analysis run {analysis_run_id!r} has no agent runs.")

    model_runs = list(session.scalars(
        select(ModelRun).where(
            ModelRun.case_id == run.case_id,
            ModelRun.case_version == run.evidence_snapshot_version,
        )
    ))
    matched = _match_model_runs(agent_runs, model_runs)

    # Verifies hashes, ordinals, and the complete artifact chain before any input
    # is included in the evaluation report.
    artifacts = verified_artifacts(session, run.case_id, run.evidence_snapshot_version)
    artifact_by_name = {row.name: row for row in artifacts}
    invocations: list[dict[str, Any]] = []
    invocation_number: dict[str, int] = defaultdict(int)

    for agent in agent_runs:
        invocation_number[agent.agent_id] += 1
        model = matched[agent.run_id]
        stage: StageArtifact | None = None
        stage_payload: dict[str, Any] = {}
        if model is not None:
            stage = artifact_by_name.get(f"model_run_{model.run_id}")
            if stage is None:
                raise AgentReportError(
                    f"Immutable input artifact is missing for model run {model.run_id}."
                )
            stage_payload = json.loads(stage.payload_json)

        usage = _redact(agent.usage or {})
        model_config = _safe_model_config(
            model.model_config_json if model is not None else agent.model_config_json
        )
        invocations.append({
            "agent_id": agent.agent_id,
            "invocation": invocation_number[agent.agent_id],
            "agent_run_id": agent.run_id,
            "model_run_id": model.run_id if model is not None else None,
            "topic": agent.topic,
            "execution_wave": agent.execution_wave,
            "parent_run_ids": list(agent.parent_run_ids or []),
            "created_at": agent.created_at.isoformat() if agent.created_at else None,
            "model_id": agent.model_id,
            "model_config": model_config,
            "prompt": {
                "prompt_id": agent.prompt_id,
                "prompt_version": agent.prompt_version,
                "prompt_hash": agent.prompt_hash,
                "response_schema_hash": agent.response_schema_hash,
                "template": _redact(stage_payload.get("prompt_template")),
                "response_schema": _redact(stage_payload.get("response_schema")),
            },
            "input": {
                "input_hash": agent.input_hash,
                "evidence_ids": list(agent.input_evidence_ids or []),
                "payload": _redact(stage_payload.get("inputs")),
            },
            "output": {
                "raw_response": _redact(agent.raw_response),
                "parsed_response": _redact(agent.parsed_response),
                "raw_response_sha256": (
                    hashlib.sha256(agent.raw_response.encode("utf-8")).hexdigest()
                    if agent.raw_response is not None else None
                ),
            },
            "evaluation": {
                "validation_status": agent.validation_status,
                "validation_detail": agent.validation_detail,
                "error_state": agent.error_state,
                "rerun_reason": agent.rerun_reason,
                "reused_from_cache": agent.reused_from_cache,
                "latency_ms": agent.latency_ms,
                "usage": usage,
            },
            "lineage": {
                "agent_definition_hash": agent.agent_definition_hash,
                "stage_artifact": stage.name if stage is not None else None,
                "stage_sha256": stage.sha256 if stage is not None else None,
                "stage_chain_hash": stage.chain_hash if stage is not None else None,
            },
        })

    started = run.started_at
    completed = run.completed_at
    payload: dict[str, Any] = {
        "document": "agent_io_evaluation",
        "version": REPORT_VERSION,
        "analysis_run": {
            "analysis_run_id": run.analysis_run_id,
            "case_id": run.case_id,
            "evidence_snapshot_version": run.evidence_snapshot_version,
            "status": run.status,
            "analysis_mode": run.analysis_mode,
            "started_at": started.isoformat() if started else None,
            "completed_at": completed.isoformat() if completed else None,
            "wall_time_seconds": (
                round((completed - started).total_seconds(), 3)
                if started is not None and completed is not None else None
            ),
            "router_version": run.router_version,
            "router_hash": run.router_hash,
            "agent_registry_version": run.agent_registry_version,
            "agent_registry_hash": run.agent_registry_hash,
            "scoring_config_version": run.scoring_config_version,
            "scoring_config_hash": run.scoring_config_hash,
            "model_configuration": _redact(run.model_configuration),
        },
        "summary": {
            "distinct_agents": len({item["agent_id"] for item in invocations}),
            "agent_invocations": len(invocations),
            "models": sorted({item["model_id"] for item in invocations}),
            "validation_statuses": dict(sorted(Counter(
                item["evaluation"]["validation_status"] for item in invocations
            ).items())),
            "cache_hits": sum(item["evaluation"]["reused_from_cache"] for item in invocations),
            "reruns": sum(bool(item["evaluation"]["rerun_reason"]) for item in invocations),
            "input_tokens": run.total_input_tokens,
            "output_tokens": run.total_output_tokens,
            "total_tokens": run.total_tokens,
            "agent_latency_ms_total": sum(
                int(item["evaluation"]["latency_ms"] or 0) for item in invocations
            ),
        },
        "invocations": invocations,
        "integrity": {
            "source": "read-only SQLite audit records and verified immutable stage artifacts",
            "redaction": "Credential-shaped mapping fields are replaced with [REDACTED].",
            "hash_scope": "All report fields except report_content_hash.",
        },
    }
    payload["report_content_hash"] = content_hash(payload)
    return payload


def _pdf_text(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u2192": "->"}
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text.encode("latin-1", "replace").decode("latin-1")


def write_pdf(payload: dict[str, Any], path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
            TableStyle, XPreformatted,
        )
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise AgentReportError("PDF output requires reportlab (install the dev dependencies).") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontSize=18,
                              leading=22, textColor=colors.HexColor("#17324d")))
    styles.add(ParagraphStyle(name="AgentTitle", parent=styles["Heading1"], fontSize=14,
                              leading=17, textColor=colors.HexColor("#17324d"), spaceAfter=6))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Heading2"], fontSize=10,
                              leading=12, textColor=colors.HexColor("#315b7d"), spaceBefore=8))
    code = ParagraphStyle(name="Code", fontName="Courier", fontSize=5.7, leading=7,
                          borderColor=colors.HexColor("#c8d2dc"), borderWidth=.4,
                          borderPadding=5, backColor=colors.HexColor("#f5f7f9"),
                          splitLongWords=True)
    small = ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=7.5, leading=9)
    centered = ParagraphStyle(name="Centered", parent=small, alignment=TA_CENTER)

    def para(text: Any, style=small):
        return Paragraph(escape(_pdf_text(text)).replace("\n", "<br/>"), style)

    def block(value: Any):
        rendered = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        wrapped: list[str] = []
        for line in _pdf_text(rendered).splitlines():
            indent = line[:len(line) - len(line.lstrip())]
            wrapped.extend(textwrap.wrap(
                line, width=145, subsequent_indent=indent + "  ",
                replace_whitespace=False, drop_whitespace=False,
                break_long_words=True, break_on_hyphens=False,
            ) or [""])
        return XPreformatted(escape("\n".join(wrapped)), code)

    run = payload["analysis_run"]
    summary = payload["summary"]
    story: list[Any] = [
        Paragraph("Agent Input / Output Evaluation Report", styles["ReportTitle"]),
        para(f"Case {run['case_id']} | run {run['analysis_run_id']} | status {run['status']}"),
        para(f"Generated from verified local audit records | report hash {payload['report_content_hash']}"),
        Spacer(1, 4 * mm),
    ]
    summary_data = [
        ["Agents", "Invocations", "Model(s)", "Tokens in", "Tokens out", "Total tokens", "Wall time", "Agent latency"],
        [summary["distinct_agents"], summary["agent_invocations"], ", ".join(summary["models"]),
         summary["input_tokens"], summary["output_tokens"], summary["total_tokens"],
         f"{run['wall_time_seconds']} s", f"{summary['agent_latency_ms_total']} ms"],
    ]
    table = LongTable([[para(cell, centered) for cell in row] for row in summary_data],
                      colWidths=[15*mm, 18*mm, 35*mm, 22*mm, 22*mm, 23*mm, 20*mm, 23*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#aebdca")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#eef3f7")),
    ]))
    story.extend([table, Spacer(1, 4 * mm), Paragraph("Run configuration", styles["SectionTitle"]), block(run)])

    for index, invocation in enumerate(payload["invocations"], 1):
        story.append(PageBreak())
        story.append(Paragraph(
            f"{index}. {_pdf_text(invocation['agent_id'])} — invocation {invocation['invocation']}",
            styles["AgentTitle"],
        ))
        evaluation = invocation["evaluation"]
        metadata = {
            key: invocation[key] for key in (
                "agent_run_id", "model_run_id", "topic", "execution_wave", "parent_run_ids",
                "created_at", "model_id", "model_config",
            )
        }
        metadata["evaluation"] = evaluation
        metadata["lineage"] = invocation["lineage"]
        story.extend([
            Paragraph("Execution and evaluation", styles["SectionTitle"]), block(metadata),
            Paragraph("Prompt identity and exact template", styles["SectionTitle"]), block(invocation["prompt"]),
            Paragraph("Agent input", styles["SectionTitle"]), block(invocation["input"]),
            Paragraph("Agent output", styles["SectionTitle"]), block(invocation["output"]),
        ])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#66788a"))
        canvas.drawString(18 * mm, 10 * mm, _pdf_text(run["analysis_run_id"]))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm, bottomMargin=16*mm,
        title=f"Agent I/O evaluation — {run['analysis_run_id']}",
        author="Recorded analysis audit export",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def export_report(database: Path, analysis_run_id: str, output_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    engine, session_factory = create_engine_and_session(f"sqlite:///{database.resolve().as_posix()}")
    session = session_factory()
    try:
        payload = build_report_payload(session, analysis_run_id)
    finally:
        session.close()
        engine.dispose()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "agent_io_evaluation.json"
    pdf_path = output_dir / "agent_io_evaluation.pdf"
    json_path.write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
    frozen = json.loads(json_path.read_text(encoding="utf-8"))
    if content_hash({k: v for k, v in frozen.items() if k != "report_content_hash"}) != frozen["report_content_hash"]:
        raise AgentReportError("Written report JSON failed its content-hash check.")
    write_pdf(frozen, pdf_path)
    return json_path, pdf_path, frozen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_run_id")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or Path("output") / "agent_io" / args.analysis_run_id
    json_path, pdf_path, payload = export_report(args.database, args.analysis_run_id, output_dir)
    print(json.dumps({
        "analysis_run_id": args.analysis_run_id,
        "agents": payload["summary"]["distinct_agents"],
        "invocations": payload["summary"]["agent_invocations"],
        "report_content_hash": payload["report_content_hash"],
        "json": str(json_path),
        "pdf": str(pdf_path),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
