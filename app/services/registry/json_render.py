"""Generic, deterministic rendering of arbitrary JSON to HTML and PDF.

Two capabilities used by the Pipeline Explorer node inspector:

* :func:`render_json_html` — a generic hierarchical viewer: any valid JSON is
  shown as collapsible nested sections; lists of uniform objects become tables.
  Escapes all values (no raw HTML injection from artifact content).
* :func:`render_json_pdf` — deterministic PDF export for arbitrary JSON using
  readable nested sections and tables (reportlab). Byte-stable for identical
  input (no timestamps embedded). When the payload matches a known credit-memo
  schema, a specialised section ordering is applied; otherwise the generic
  structure-aware dump is used.

Neither function generates new credit conclusions or changes source data — they
only re-present the recorded JSON.
"""

from __future__ import annotations

import io
from typing import Any, Callable, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

HEAD = colors.HexColor("#1a3b5d")
GRID = colors.HexColor("#d0d7de")
LIGHT = colors.HexColor("#eef2f6")
CONTENT_WIDTH_MM = 174


class _DeterministicCanvas(Canvas):
    """Canvas that pins the document /ID and creation/modification dates.

    reportlab's ``invariant`` flag makes the PDF reproducible: it fixes the /ID
    and freezes the creation/modification dates to a constant, so two renders of
    the same JSON produce byte-identical output with no wall-clock leakage.
    """

    def __init__(self, *args, **kwargs):
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)
# A uniform-object list wider than this renders as per-item sections, not a
# single table, so it cannot overflow the A4 content frame.
_MAX_TABLE_COLUMNS = 8
# A single table cell cannot be taller than one page; reportlab cannot split a
# row whose tallest cell exceeds the frame. Values whose rendered text would be
# that tall are pulled OUT of the table and rendered as their own full-width,
# page-flowing section instead. The threshold is a conservative character budget
# for the usable A4 content frame (~68 lines x ~95 chars in the cell font),
# chosen so a cell never overflows; content is re-presented, never truncated.
_MAX_CELL_CHARS = 1600
# A whole uniform-object list with more rows than this is paginated by rendering
# each record as its own key/value section (still a splittable table per record)
# rather than one giant table, which keeps very long lists robust.
_MAX_TABLE_ROWS = 40


def _text_len(value: Any) -> int:
    """Approximate rendered length of a scalar/compact value, for the cell guard."""
    if value is None or isinstance(value, bool):
        return 4
    if isinstance(value, (dict, list)):
        return len(_compact(value))
    return len(str(value))


def _cell_too_tall(value: Any) -> bool:
    """True when a value would make a table cell taller than a page can hold."""
    return _text_len(value) > _MAX_CELL_CHARS


# ---------------------------------------------------------------------------
# Generic HTML hierarchical viewer
# ---------------------------------------------------------------------------

def _humanize(key: str) -> str:
    return key.replace("_", " ").strip().title() if isinstance(key, str) else str(key)


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _fmt_scalar(value: Any) -> str:
    if value is None:
        return "<span class='null'>null</span>"
    if isinstance(value, bool):
        return "true" if value else "false"
    return escape(str(value))


def _uniform_object_list(items: list) -> bool:
    return (
        len(items) > 0
        and all(isinstance(i, dict) for i in items)
        and len({frozenset(i.keys()) for i in items}) == 1
    )


def _html_node(value: Any, *, label: Optional[str], depth: int) -> str:
    """Render one JSON value to an HTML fragment (recursive)."""
    out: list[str] = []
    if isinstance(value, dict):
        if label is not None:
            out.append(
                f"<details open class='jv-obj' data-depth='{depth}'>"
                f"<summary>{escape(str(label))}</summary><div class='jv-body'>"
            )
        else:
            out.append("<div class='jv-body'>")
        for key, child in value.items():
            if _scalar(child):
                out.append(
                    f"<div class='jv-row'><span class='jv-key'>{escape(_humanize(key))}</span>"
                    f"<span class='jv-val'>{_fmt_scalar(child)}</span></div>"
                )
            else:
                out.append(_html_node(child, label=_humanize(key), depth=depth + 1))
        out.append("</details>" if label is not None else "</div>")
    elif isinstance(value, list):
        head = (
            f"<details open class='jv-arr' data-depth='{depth}'>"
            f"<summary>{escape(str(label))} <span class='jv-count'>[{len(value)}]</span></summary>"
            f"<div class='jv-body'>"
            if label is not None else "<div class='jv-body'>"
        )
        out.append(head)
        if _uniform_object_list(value):
            cols = list(value[0].keys())
            out.append("<table class='jv-table'><thead><tr>")
            for col in cols:
                out.append(f"<th>{escape(_humanize(col))}</th>")
            out.append("</tr></thead><tbody>")
            for row in value:
                out.append("<tr>")
                for col in cols:
                    cell = row.get(col)
                    out.append(
                        f"<td>{_fmt_scalar(cell) if _scalar(cell) else _html_node(cell, label=None, depth=depth + 1)}</td>"
                    )
                out.append("</tr>")
            out.append("</tbody></table>")
        else:
            for i, child in enumerate(value):
                if _scalar(child):
                    out.append(f"<div class='jv-row'><span class='jv-idx'>[{i}]</span>"
                               f"<span class='jv-val'>{_fmt_scalar(child)}</span></div>")
                else:
                    out.append(_html_node(child, label=f"[{i}]", depth=depth + 1))
        out.append("</details>" if label is not None else "</div>")
    else:
        out.append(f"<div class='jv-row'><span class='jv-val'>{_fmt_scalar(value)}</span></div>")
    return "".join(out)


def render_json_html(payload: Any, *, title: Optional[str] = None) -> str:
    """Render arbitrary JSON as a collapsible hierarchical HTML fragment."""
    body = _html_node(payload, label=title, depth=0)
    return f"<div class='json-viewer'>{body}</div>"


# ---------------------------------------------------------------------------
# Deterministic PDF export for arbitrary JSON
# ---------------------------------------------------------------------------

def _pdf_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("jv_title", parent=base["Title"], fontSize=18,
                                 textColor=HEAD, spaceAfter=10),
        "h": ParagraphStyle("jv_h", parent=base["Heading2"], fontSize=12,
                             textColor=HEAD, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("jv_body", parent=base["BodyText"], fontSize=9,
                                leading=12),
        "cell": ParagraphStyle("jv_cell", parent=base["BodyText"], fontSize=8,
                               leading=10),
    }
    return styles


def _pdf_cell(value: Any, st: dict) -> Paragraph:
    if value is None:
        text = "—"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        text = escape(_compact(value))
    else:
        text = escape(str(value))
    return Paragraph(text, st["cell"])


def _compact(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _kv_table(rows: list[tuple[str, Any]], st: dict) -> Table:
    data = [[Paragraph(f"<b>{escape(_humanize(k))}</b>", st["cell"]), _pdf_cell(v, st)]
            for k, v in rows]
    width = CONTENT_WIDTH_MM * mm
    table = Table(data, colWidths=[width * 0.32, width * 0.68])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, colors.HexColor("#f7f9fb")]),
    ]))
    return table


def _records_table(items: list[dict], st: dict) -> Table:
    cols = list(items[0].keys())
    header = [Paragraph(f"<b>{escape(_humanize(c))}</b>", st["cell"]) for c in cols]
    data = [header]
    for row in items:
        data.append([_pdf_cell(row.get(c), st) for c in cols])
    width = CONTENT_WIDTH_MM * mm
    table = Table(data, colWidths=[width / len(cols)] * len(cols), repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
    ]))
    return table


def _long_scalar_section(key: str, value: Any, st: dict, story: list) -> None:
    """Render an oversized scalar as its own page-flowing section.

    A standalone ``Paragraph`` flows and paginates across pages, so no content is
    lost or truncated — unlike a fixed table cell, which cannot exceed one page.
    """
    story.append(Paragraph(f"<b>{escape(_humanize(key))}</b>", st["h"]))
    text = value if isinstance(value, str) else _compact(value)
    story.append(Paragraph(escape(str(text)), st["body"]))
    story.append(Spacer(1, 4))


def _records_as_sections(items: list[dict], st: dict, story: list) -> None:
    """Render each record as its own key/value section (robust for any size)."""
    for i, row in enumerate(items):
        story.append(Paragraph(f"[{i}]", st["h"]))
        _render_any_pdf(row, st, story)


def _render_any_pdf(value: Any, st: dict, story: list, *,
                    heading: Optional[str] = None, level: int = 1) -> None:
    if heading:
        story.append(Paragraph(escape(heading), st["h"]))
    if isinstance(value, dict):
        # Short scalars go in a compact key/value table; scalars whose rendered
        # text would overflow a table cell are pulled out into their own
        # page-flowing section so the PDF never raises on an oversized row.
        short = [(k, v) for k, v in value.items() if _scalar(v) and not _cell_too_tall(v)]
        long_scalars = [(k, v) for k, v in value.items()
                        if _scalar(v) and _cell_too_tall(v)]
        if short:
            story.append(_kv_table(short, st))
            story.append(Spacer(1, 4))
        for key, child in long_scalars:
            _long_scalar_section(key, child, st, story)
        for key, child in value.items():
            if not _scalar(child):
                _render_any_pdf(child, st, story, heading=_humanize(key), level=level + 1)
    elif isinstance(value, list):
        # A uniform-object list renders as a table only when it is narrow enough
        # to fit A4, short enough to paginate cleanly, and has NO oversized cell;
        # otherwise it falls back to per-item sections so no row ever overflows.
        is_records = _uniform_object_list(value)
        narrow = is_records and len(value[0].keys()) <= _MAX_TABLE_COLUMNS
        short_enough = len(value) <= _MAX_TABLE_ROWS
        no_tall_cell = is_records and not any(
            _cell_too_tall(row.get(c)) for row in value for c in value[0].keys())
        if narrow and short_enough and no_tall_cell:
            story.append(_records_table(value, st))
            story.append(Spacer(1, 4))
        elif _uniform_object_list(value):
            _records_as_sections(value, st, story)
        else:
            for i, child in enumerate(value):
                if _scalar(child) and not _cell_too_tall(child):
                    story.append(Paragraph(f"[{i}] {escape(str(child))}", st["body"]))
                elif _scalar(child):
                    _long_scalar_section(f"[{i}]", child, st, story)
                else:
                    _render_any_pdf(child, st, story, heading=f"[{i}]", level=level + 1)
    else:
        if _cell_too_tall(value):
            story.append(Paragraph(escape(str(value)), st["body"]))
        else:
            story.append(Paragraph(escape(str(value)), st["body"]))


# Specialised ordering for known credit-memo schemas (top-level section order).
_KNOWN_SCHEMA_SECTION_ORDER: dict[str, list[str]] = {
    "bo-m3-analysis-1.0": [
        "module", "completion", "corporate_structure_status",
        "senior_management_assessment", "structural_observations",
        "material_credit_implications", "consolidated_conclusion",
        "evidence_verification", "information_gaps", "structural_limitations",
    ],
    "bo-memo-1.0": ["document", "case", "run", "status", "score", "sections",
                    "module_conclusions", "sources"],
}


def _detect_schema(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        return payload.get("schema_version") or payload.get("version")
    return None


def render_json_pdf(
    payload: Any,
    *,
    title: str = "Artifact",
    subtitle: Optional[str] = None,
) -> bytes:
    """Render arbitrary JSON to a deterministic PDF (readable sections/tables).

    When ``payload`` carries a known credit-memo ``schema_version``, its
    top-level sections are ordered by a specialised template; otherwise the
    generic structure-aware dump is used. The output never embeds timestamps, so
    identical input yields byte-identical output.
    """
    st = _pdf_styles()
    story: list = [Paragraph(escape(title), st["title"])]
    if subtitle:
        story.append(Paragraph(escape(subtitle), st["body"]))
        story.append(Spacer(1, 6))

    schema = _detect_schema(payload)
    order = _KNOWN_SCHEMA_SECTION_ORDER.get(schema or "")
    if order and isinstance(payload, dict):
        story.append(Paragraph(f"Specialised rendering for schema: {escape(schema)}", st["body"]))
        story.append(Spacer(1, 6))
        seen = set()
        for key in order:
            if key in payload:
                seen.add(key)
                child = payload[key]
                if _scalar(child) and not _cell_too_tall(child):
                    story.append(_kv_table([(key, child)], st))
                    story.append(Spacer(1, 4))
                elif _scalar(child):
                    _long_scalar_section(key, child, st, story)
                else:
                    _render_any_pdf(child, st, story, heading=_humanize(key))
        remaining = {k: v for k, v in payload.items() if k not in seen}
        if remaining:
            _render_any_pdf(remaining, st, story, heading="Other fields")
    else:
        _render_any_pdf(payload, st, story)

    def _build(flowables: list) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=18 * mm, rightMargin=18 * mm,
            topMargin=18 * mm, bottomMargin=18 * mm,
            title=title, author="Pipeline Explorer (deterministic export)",
            invariant=1,  # pin the PDF /ID and producer for reproducibility
        )
        doc.build(flowables, canvasmaker=_DeterministicCanvas)
        return buffer.getvalue()

    try:
        return _build(story)
    except LayoutError:
        # Final safety net: no artifact may ever return HTTP 500 from the PDF
        # route. If any residual flowable still overflows, re-render the whole
        # payload as page-flowing paragraphs (no tables), which cannot overflow.
        # All content is preserved — every key/value is emitted as flowing text.
        fallback: list = [
            Paragraph(escape(title), st["title"]),
            Paragraph(escape(subtitle) if subtitle else "", st["body"]),
            Paragraph("Rendered in text-flow mode (content preserved; a value "
                      "exceeded the tabular layout).", st["body"]),
            Spacer(1, 6),
        ]
        _render_text_flow(payload, st, fallback)
        return _build(fallback)


def _render_text_flow(value: Any, st: dict, story: list, *, prefix: str = "") -> None:
    """Render any JSON as flowing, paginating paragraphs only (no tables).

    Guaranteed never to overflow a page frame, so it is the safe fallback when
    tabular layout cannot fit a value. Preserves every key and value verbatim.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            label = f"{prefix}{_humanize(key)}"
            if _scalar(child):
                story.append(Paragraph(f"<b>{escape(label)}:</b> {escape(_scalar_text(child))}",
                                       st["body"]))
            else:
                story.append(Paragraph(f"<b>{escape(label)}</b>", st["h"]))
                _render_text_flow(child, st, story, prefix=prefix + "  ")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            if _scalar(child):
                story.append(Paragraph(f"{escape(prefix)}[{i}] {escape(_scalar_text(child))}",
                                       st["body"]))
            else:
                story.append(Paragraph(f"{escape(prefix)}[{i}]", st["h"]))
                _render_text_flow(child, st, story, prefix=prefix + "  ")
    else:
        story.append(Paragraph(escape(_scalar_text(value)), st["body"]))


def _scalar_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
