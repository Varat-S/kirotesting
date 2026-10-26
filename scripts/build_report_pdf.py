"""Build a PDF test report for a module's live run — general and reusable.

Any module whose runner writes a run directory containing:

  * ``<deliverables_file>``  — the module's output JSON (any shape), and
  * ``<validator_file>``     — a validator report of the shared form
        ``{"passed": bool, "checks": [{"check_id", "name", "passed", "detail"}]}``,
  * optionally ``evidence_provenance.json`` — the evidence-assembly summary,

can be rendered with this one script. The styling (cover, PASS/FAIL verdict
banner, metadata, provenance, per-check results) is shared across modules, so a
new module gets a consistent report with no new formatting code.

Two layers:

  * A GENERIC engine renders the verdict, metadata, provenance and validator
    checks, and falls back to a structure-aware dump of the deliverables JSON
    (nested dicts -> key/value tables; lists of uniform dicts -> tables). A brand
    new module works immediately with zero extra code.
  * An OPTIONAL per-module renderer can be registered in ``DELIVERABLE_RENDERERS``
    to lay a specific module's fields out more deliberately. Module 4 ships one
    as the reference implementation.

The report is an UNAPPROVED draft artifact for human review: it always shows the
model output AND the deterministic verdict, never hiding a failed check.

Usage (from the repo root):
    .venv\\Scripts\\python -m scripts.build_report_pdf --run-dir output/module4_pro \\
        --module module4 --model gemini-2.5-pro --case-id HCA_FY2025_M4_LIVE

    # a future module, no custom renderer needed:
    .venv\\Scripts\\python -m scripts.build_report_pdf --run-dir output/module5_run \\
        --module module5 --title "Module 5 Specialist" \\
        --deliverables-file module5_deliverables.json --validator-file validator9_report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

GREEN = colors.HexColor("#1b7a3d")
RED = colors.HexColor("#b3261e")
GREY = colors.HexColor("#5f6368")
HEAD = colors.HexColor("#1a3b5d")
LIGHT = colors.HexColor("#eef2f6")
GRID = colors.HexColor("#d0d7de")

CONTENT_WIDTH_MM = 174  # A4 width minus the 18mm margins used below


# ---------------------------------------------------------------------------
# Styling + small flowable helpers (shared by every module)
# ---------------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=20, textColor=HEAD, spaceAfter=4),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontSize=10, textColor=GREY, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=14, textColor=HEAD, spaceBefore=12, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=11, textColor=HEAD, spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=13, alignment=TA_LEFT),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8, leading=11, textColor=GREY),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10),
        "cellb": ParagraphStyle("cellb", parent=base["Normal"], fontSize=8, leading=10, textColor=colors.white),
    }


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape("" if text is None else str(text)), style)


def _humanize(key: str) -> str:
    """Turn a snake_case JSON key into a readable label."""
    return key.replace("_m", " ($m)").replace("_", " ").strip().capitalize()


def _scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _kv_table(rows: list[tuple[str, Any]], st: dict) -> Table:
    data = [[_p(k, st["cell"]), _p(v, st["cell"])] for k, v in rows]
    t = Table(data, colWidths=[58 * mm, (CONTENT_WIDTH_MM - 58) * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, GRID),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def _records_table(rows: list[dict], columns: list[tuple[str, str]], st: dict) -> Table:
    """A table from a list of uniform dicts. columns = [(json_key, header_label)]."""
    header = [_p(label, st["cellb"]) for _, label in columns]
    body = [[_p(row.get(key), st["cell"]) for key, _ in columns] for row in rows]
    ncol = len(columns)
    t = Table([header] + body, repeatRows=1, colWidths=[(CONTENT_WIDTH_MM / ncol) * mm] * ncol)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEAD),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.3, GRID),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def _banner(text: str, color: colors.Color, st: dict) -> Table:
    t = Table([[_p(text, st["cellb"])]], colWidths=[CONTENT_WIDTH_MM * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), color),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return t


# ---------------------------------------------------------------------------
# Generic structure-aware deliverables renderer (works for ANY module)
# ---------------------------------------------------------------------------


def _render_any(value: Any, st: dict, story: list, *, heading: str | None = None, level: int = 3) -> None:
    """Render arbitrary JSON: scalars as key/value, lists of dicts as tables."""
    if heading:
        story.append(_p(heading, st["h3" if level >= 3 else "h2"]))

    if isinstance(value, dict):
        scalar_rows = [(_humanize(k), v) for k, v in value.items() if _scalar(v)]
        if scalar_rows:
            story.append(_kv_table(scalar_rows, st))
            story.append(Spacer(1, 4))
        for k, v in value.items():
            if isinstance(v, dict):
                _render_any(v, st, story, heading=_humanize(k), level=3)
            elif isinstance(v, list) and v:
                _render_list(v, st, story, heading=_humanize(k))
    elif isinstance(value, list):
        _render_list(value, st, story, heading=None)
    else:
        story.append(_p(value, st["body"]))


def _render_list(items: list, st: dict, story: list, *, heading: str | None) -> None:
    if heading:
        story.append(_p(heading, st["small"]))
    dict_items = [i for i in items if isinstance(i, dict)]
    if dict_items and len(dict_items) == len(items):
        # Uniform list of dicts -> table using the union of keys (scalars only).
        keys: list[str] = []
        for it in dict_items:
            for k, v in it.items():
                if _scalar(v) and k not in keys:
                    keys.append(k)
        if keys:
            columns = [(k, _humanize(k)) for k in keys]
            story.append(_records_table(dict_items, columns, st))
            story.append(Spacer(1, 4))
        # Any nested structure inside the rows is rendered after the table.
        for idx, it in enumerate(dict_items):
            nested = {k: v for k, v in it.items() if not _scalar(v)}
            if nested:
                _render_any(nested, st, story, heading=f"{heading or 'Item'} #{idx + 1} detail", level=3)
    else:
        for it in items:
            story.append(_p(f"• {it}", st["body"]))


# ---------------------------------------------------------------------------
# Optional per-module custom renderers (nicer layout for known field names)
# ---------------------------------------------------------------------------


def _render_module4(deliverables: dict, st: dict, story: list) -> None:
    d1 = deliverables.get("deliverable_1", {})
    story.append(_p("Deliverable 1 — Existing Debt &amp; Creditor Priority Map", st["h3"]))
    story.append(_p((d1.get("extraction_source") or {}).get("narrative"), st["body"]))
    story.append(Spacer(1, 4))
    if d1.get("senior_notes_schedule"):
        story.append(_p("Senior notes schedule", st["small"]))
        story.append(
            _records_table(
                d1["senior_notes_schedule"],
                [
                    ("senior_note_series", "Series"),
                    ("principal_amount_m", "Principal $m"),
                    ("coupon_interest_rate", "Coupon"),
                    ("maturity_year", "Maturity"),
                    ("collateral_security_status", "Security"),
                    ("priority_ranking_tier", "Tier"),
                ],
                st,
            )
        )
        story.append(Spacer(1, 4))
    cpf = d1.get("creditor_priority_framework") or {}
    if cpf:
        story.append(_p("Creditor priority framework", st["small"]))
        story.append(
            _kv_table(
                [
                    ("Tier 1 (direct OpCo secured) $m", cpf.get("tier_1_direct_opco_secured_m")),
                    ("Tier 2 (senior unsecured guaranteed) $m", cpf.get("tier_2_senior_unsecured_guaranteed_m")),
                    ("Tier 3 (unguaranteed HoldCo) $m", cpf.get("tier_3_unguaranteed_holdco_m")),
                    ("Consolidated total indebtedness $m", cpf.get("consolidated_total_indebtedness_m")),
                    ("Structural subordination drag ratio", cpf.get("structural_subordination_drag_ratio")),
                ],
                st,
            )
        )

    d2 = deliverables.get("deliverable_2", {})
    story.append(_p("Deliverable 2 — Historical Refinancing &amp; Debt Maturity Analysis", st["h3"]))
    story.append(_p((d2.get("extraction_source") or {}).get("narrative"), st["body"]))
    m = d2.get("metrics") or {}
    if m:
        story.append(
            _kv_table(
                [
                    ("Weighted-avg refinancing lead time (months)", m.get("weighted_avg_refinancing_lead_time_months")),
                    ("Refinancing cost spread delta (bps)", m.get("refinancing_cost_spread_delta_bps")),
                    ("Weighted-avg maturity extension (years)", m.get("weighted_avg_maturity_extension_years")),
                    ("Historical distress events", m.get("historical_refinancing_distress_events")),
                ],
                st,
            )
        )
    if d2.get("refinancing_capacity_discipline_verdict"):
        story.append(_p("Verdict: " + str(d2["refinancing_capacity_discipline_verdict"]), st["body"]))

    d3 = deliverables.get("deliverable_3", {})
    story.append(_p("Deliverable 3 — Debt Utilisation &amp; Leverage Impact", st["h3"]))
    story.append(_p((d3.get("extraction_source") or {}).get("narrative"), st["body"]))
    if d3.get("debt_utilisation_leverage_verdict"):
        story.append(_p("Verdict: " + str(d3["debt_utilisation_leverage_verdict"]), st["body"]))

    d4 = deliverables.get("deliverable_4", {})
    story.append(_p("Deliverable 4 — Module 4 Credit Conclusion", st["h3"]))
    story.append(_p(d4.get("core_credit_assessment"), st["body"]))
    box = d4.get("executive_credit_summary_box") or {}
    if box:
        story.append(Spacer(1, 4))
        story.append(_p("Executive credit summary box", st["small"]))
        story.append(
            _kv_table(
                [
                    ("Target entity &amp; period", box.get("target_entity_and_period")),
                    ("Overall verdict", box.get("overall_credit_rating_verdict")),
                    ("Recommended loan placement", box.get("recommended_loan_placement")),
                    ("Takeaways", " | ".join(box.get("executive_takeaways", []) or [])),
                ],
                st,
            )
        )


# Register custom renderers by module id. A module absent from this map falls
# back to the generic structure-aware renderer above.
DeliverableRenderer = Callable[[dict, dict, list], None]
DELIVERABLE_RENDERERS: dict[str, DeliverableRenderer] = {
    "module4": _render_module4,
}


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _load(run_dir: Path, name: str) -> Any:
    path = run_dir / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def build(
    run_dir: Path,
    out_path: Path,
    *,
    module: str,
    title: str,
    subtitle: str,
    deliverables_file: str,
    validator_file: str,
    validator_label: str,
    model: str | None,
    case_id: str | None,
    borrower: str | None,
) -> None:
    st = _styles()
    deliverables = _load(run_dir, deliverables_file)
    validator = _load(run_dir, validator_file)
    provenance = _load(run_dir, "evidence_provenance.json") or {}

    story: list[Any] = []

    # -- cover + verdict ------------------------------------------------------
    story.append(_p(title, st["title"]))
    story.append(_p(subtitle, st["sub"]))
    story.append(HRFlowable(width="100%", thickness=1, color=HEAD, spaceAfter=8))

    checks = (validator or {}).get("checks", []) if validator else []
    passed = bool(validator and validator.get("passed"))
    n_checks = len(checks)
    n_pass = sum(1 for c in checks if c.get("passed"))
    if validator is None:
        verdict_text, color = "NO VALIDATOR REPORT FOUND", GREY
    elif passed:
        verdict_text, color = "ALL DETERMINISTIC CHECKS PASSED", GREEN
    else:
        verdict_text = f"{validator_label} FAILED — {n_checks - n_pass} of {n_checks} checks failed; mandatory escalation"
        color = RED
    story.append(_banner(verdict_text, color, st))
    story.append(Spacer(1, 8))

    # -- run metadata ---------------------------------------------------------
    story.append(_p("Run metadata", st["h3"]))
    story.append(
        _kv_table(
            [
                ("Module", module),
                ("Case", case_id or "(unspecified)"),
                ("Borrower", borrower or "(unspecified)"),
                ("Model", model or "(see run)"),
                ("Validator", validator_label),
                ("Validator result", "n/a" if validator is None else ("PASSED" if passed else "FAILED")),
                ("Checks passed", "n/a" if validator is None else f"{n_pass} / {n_checks}"),
                ("Nature", "AI-generated draft; deterministic re-check applied. Not a credit decision."),
            ],
            st,
        )
    )

    # -- evidence provenance (shown when the runner produced it) --------------
    if provenance:
        story.append(_p("Evidence given to the model", st["h3"]))
        story.append(_kv_table([(_humanize(k), v if _scalar(v) else ", ".join(map(str, v)) if isinstance(v, list) else str(v)) for k, v in provenance.items()], st))
        story.append(
            _p(
                "Evidence was selected from the pipeline's admitted facts and "
                "verbatim passages only; nothing was invented. Gaps are surfaced, "
                "not filled.",
                st["small"],
            )
        )

    # -- validator detail -----------------------------------------------------
    story.append(PageBreak())
    story.append(_p(f"{validator_label} — deterministic re-checks", st["h2"]))
    story.append(
        _p(
            "The validator never calls a model. It independently re-checks the "
            "model's output. A failure raises a mandatory escalation for human "
            "review rather than passing silently.",
            st["small"],
        )
    )
    story.append(Spacer(1, 4))
    if not checks:
        story.append(_p("No validator checks were recorded for this run.", st["body"]))
    for check in checks:
        ok = check.get("passed")
        hdr = Table(
            [[_p("PASS" if ok else "FAIL", st["cellb"]),
              _p(f"{check.get('check_id')} — {check.get('name')}", st["cell"])]],
            colWidths=[18 * mm, (CONTENT_WIDTH_MM - 18) * mm],
        )
        hdr.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), GREEN if ok else RED),
                    ("BACKGROUND", (1, 0), (1, 0), LIGHT),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(hdr)
        story.append(_p(check.get("detail"), st["body"]))
        story.append(Spacer(1, 6))

    # -- deliverables ---------------------------------------------------------
    if deliverables:
        story.append(PageBreak())
        story.append(_p(f"{module} deliverables (model output)", st["h2"]))
        renderer = DELIVERABLE_RENDERERS.get(module)
        if renderer is not None:
            renderer(deliverables, st, story)
        else:
            # Generic, structure-aware fallback for any not-yet-customized module.
            _render_any(deliverables, st, story, heading=None, level=2)
        story.append(Spacer(1, 6))
        story.append(
            _p(
                "Reviewer note: the figures above are the model's proposed values. "
                "Where the validator reported a failure (see prior page), those "
                "values are NOT reconciled to the filing and must be corrected by "
                "an analyst before any use.",
                st["small"],
            )
        )

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=title,
    )
    doc.build(story)


# ---------------------------------------------------------------------------
# Per-module presets so common modules need no flags beyond --run-dir
# ---------------------------------------------------------------------------

MODULE_PRESETS: dict[str, dict[str, str]] = {
    "module4": {
        "title": "Module 4 Specialist — Live Test Report",
        "subtitle": "Debt Structure, Refinancing History & Debt Utilisation · "
        "AI-assisted draft for human review (UNAPPROVED)",
        "deliverables_file": "m4_analysis.json",
        "validator_file": "validator8_report.json",
        "validator_label": "Validator 8",
        "default_out": "module4_test_report.pdf",
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, help="Directory holding the run's JSON outputs.")
    ap.add_argument("--module", required=True, help="Module id, e.g. 'module4' (selects preset + custom renderer if any).")
    ap.add_argument("--out", default=None, help="Output PDF path (defaults inside the run dir).")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--deliverables-file", default=None, help="Deliverables JSON filename inside the run dir.")
    ap.add_argument("--validator-file", default=None, help="Validator report JSON filename inside the run dir.")
    ap.add_argument("--validator-label", default=None, help="Display name of the validator, e.g. 'Validator 8'.")
    ap.add_argument("--model", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--borrower", default=None)
    args = ap.parse_args(argv)

    preset = MODULE_PRESETS.get(args.module, {})
    title = args.title or preset.get("title") or f"{args.module} — Live Test Report"
    subtitle = args.subtitle or preset.get("subtitle") or "AI-assisted draft for human review (UNAPPROVED)"
    deliverables_file = args.deliverables_file or preset.get("deliverables_file") or f"{args.module}_deliverables.json"
    validator_file = args.validator_file or preset.get("validator_file") or "validator_report.json"
    validator_label = args.validator_label or preset.get("validator_label") or "Validator"
    default_out = preset.get("default_out") or f"{args.module}_test_report.pdf"

    run_dir = Path(args.run_dir)
    out_path = Path(args.out) if args.out else run_dir / default_out
    build(
        run_dir, out_path,
        module=args.module, title=title, subtitle=subtitle,
        deliverables_file=deliverables_file, validator_file=validator_file,
        validator_label=validator_label, model=args.model, case_id=args.case_id,
        borrower=args.borrower,
    )
    print(f"PDF written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
