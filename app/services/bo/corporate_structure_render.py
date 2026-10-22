"""Deterministic corporate-structure renderer (SVG, optional PNG export).

The AI specialist analyses the structure, but this renderer *draws* strictly
from validated JSON values — there is no free-form image generation. Rendering
the same validated input is byte-stable (deterministic): no timestamps, random
ids or hash-ordered iteration, floats formatted canonically.

The renderer follows the uploaded ``visual_spec`` exactly: canvas dimensions,
colours by role, node geometry, display names + roles, ownership relationships,
ownership percentages (only from ``ownership_links``), guarantor shields (only
where ``is_guarantor`` is true), header/subtitle/legend/footer, text wrapping,
font sizes, alignment and connector layout. It honours ``negative_requirements``:
no extra header disclaimer line, no legend note beyond the listed entries, and
for single-child relationships a perfectly straight vertical connector (no bend,
jog or lateral offset).
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

from app.schemas.bo_m3_structure import (
    CORP_STRUCTURE_RENDERER_VERSION,
    CorporateStructureArtifact,
    StructureLink,
    StructureNode,
)


class CorporateStructureRenderError(ValueError):
    pass


def _fmt(value: float) -> str:
    """Canonical number formatting for byte-stable SVG (no trailing zeros)."""
    r = round(float(value), 3)
    if r == int(r):
        return str(int(r))
    return f"{r:.3f}".rstrip("0").rstrip(".")


def _wrap(name: str, width_px: float, font_px: float, max_lines: int = 3) -> list[str]:
    """Deterministic greedy word-wrap into at most ``max_lines`` centred lines.

    Width estimate uses a fixed average glyph ratio so the same input always
    wraps identically. No access to real font metrics (kept deterministic).
    """
    avg_char_px = font_px * 0.56
    max_chars = max(1, int((width_px - 12) / avg_char_px))
    words = name.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines - 1 and current and len(current) > max_chars:
            # Hard-stop: keep remaining words on the final line (may be long).
            continue
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1:])
        lines = [*head, tail]
    return lines or [name]


def _color_for_role(role: str, colors_by_role: dict[str, str]) -> str:
    if role in colors_by_role:
        return colors_by_role[role]
    # Case-insensitive fallback, then the explicit "Other".
    lowered = {k.casefold(): v for k, v in colors_by_role.items()}
    return lowered.get((role or "").casefold(), colors_by_role.get("Other", "#A7A7A7"))


def render_corporate_structure_svg(
    artifact: CorporateStructureArtifact, visual_spec: dict[str, Any],
) -> str:
    """Render a validated structure artifact to a deterministic SVG string.

    ``visual_spec`` is the (read-only) spec from the source JSON. The artifact
    must have ``status == "available"``; otherwise no diagram is drawn.
    """

    if artifact.status != "available":
        raise CorporateStructureRenderError(
            "a corporate-structure diagram may be rendered only from an available, "
            "validated artifact")

    canvas = visual_spec.get("canvas") or {}
    width = _fmt(canvas.get("width_px", 1440))
    height = _fmt(canvas.get("height_px", 1080))
    background = canvas.get("background_hex", "#FFFFFF")

    nodes_by_id: dict[str, StructureNode] = {n.node_id: n for n in artifact.nodes}
    pct_by_edge: dict[tuple[str, str], float | None] = {
        (l.parent_id, l.child_id): l.ownership_percent for l in artifact.links}

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'data-renderer-version="{escape(CORP_STRUCTURE_RENDERER_VERSION)}" '
        f'role="img" aria-label="Corporate structure diagram">')
    comp = artifact.provenance.company_display_name if artifact.provenance else ""
    parts.append(f'<title>Corporate structure — {escape(comp)}</title>')
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" '
                 f'fill="{escape(background)}"/>')

    # Connectors first (drawn under nodes).
    parts.extend(_render_connectors(artifact.links, nodes_by_id,
                                    visual_spec.get("ownership_links_style") or {},
                                    pct_by_edge))
    # Nodes.
    style = visual_spec.get("node_style") or {}
    colors = visual_spec.get("colors_by_role") or {}
    for node in artifact.nodes:
        parts.extend(_render_node(node, style, colors))
    # Header, legend, footer.
    parts.extend(_render_header(visual_spec.get("header") or {}, comp))
    parts.extend(_render_legend(visual_spec.get("legend") or {}))
    parts.extend(_render_footer(visual_spec.get("footer") or {}))
    parts.append("</svg>")
    return "".join(parts)


def _render_connectors(
    links: list[StructureLink], nodes_by_id: dict[str, StructureNode],
    link_style: dict[str, Any], pct_by_edge,
) -> list[str]:
    color = link_style.get("line_color_hex", "#141414")
    width = _fmt(link_style.get("line_width_px", 3))
    pct_fmt = (link_style.get("percentage_label") or {})
    pct_font = _fmt(pct_fmt.get("font_size_px", 19))
    pct_color = pct_fmt.get("font_color_hex", "#111111")

    # Group by parent, children in deterministic (declared) order.
    by_parent: dict[str, list[StructureLink]] = {}
    for link in links:
        by_parent.setdefault(link.parent_id, []).append(link)

    out: list[str] = []
    for parent_id in sorted(by_parent):  # sorted parents -> byte-stable
        children = by_parent[parent_id]
        parent = nodes_by_id[parent_id]
        parent_bottom = parent.top_y_px + parent.height_px
        parent_cx = parent.center_x_px

        if len(children) == 1:
            # SINGLE CHILD: one perfectly straight vertical connector; snap the
            # child centre to the parent centreline (single_child_alignment_rule).
            child = nodes_by_id[children[0].child_id]
            x = _fmt(parent_cx)
            out.append(
                f'<line x1="{x}" y1="{_fmt(parent_bottom)}" x2="{x}" '
                f'y2="{_fmt(child.top_y_px)}" stroke="{escape(color)}" '
                f'stroke-width="{width}"/>')
            out.extend(_pct_label(children[0], child, parent_cx, parent_bottom,
                                  pct_by_edge, pct_font, pct_color))
            continue

        # MULTIPLE CHILDREN: one horizontal bus with vertical drops.
        child_nodes = [nodes_by_id[c.child_id] for c in children]
        child_tops = [cn.top_y_px for cn in child_nodes]
        bus_y = (parent_bottom + min(child_tops)) / 2
        xs = [parent_cx] + [cn.center_x_px for cn in child_nodes]
        bus_x1, bus_x2 = min(xs), max(xs)
        # Parent drop to the bus.
        out.append(
            f'<line x1="{_fmt(parent_cx)}" y1="{_fmt(parent_bottom)}" '
            f'x2="{_fmt(parent_cx)}" y2="{_fmt(bus_y)}" stroke="{escape(color)}" '
            f'stroke-width="{width}"/>')
        # Horizontal bus.
        out.append(
            f'<line x1="{_fmt(bus_x1)}" y1="{_fmt(bus_y)}" x2="{_fmt(bus_x2)}" '
            f'y2="{_fmt(bus_y)}" stroke="{escape(color)}" stroke-width="{width}"/>')
        for link, child in zip(children, child_nodes):
            out.append(
                f'<line x1="{_fmt(child.center_x_px)}" y1="{_fmt(bus_y)}" '
                f'x2="{_fmt(child.center_x_px)}" y2="{_fmt(child.top_y_px)}" '
                f'stroke="{escape(color)}" stroke-width="{width}"/>')
            out.extend(_pct_label(link, child, child.center_x_px, bus_y,
                                  pct_by_edge, pct_font, pct_color))
    return out


def _pct_label(link: StructureLink, child: StructureNode, drop_x: float,
               drop_top_y: float, pct_by_edge, font: str, color: str) -> list[str]:
    pct = pct_by_edge.get((link.parent_id, link.child_id))
    if pct is None:
        return []
    label = f"{_fmt(pct)}%"
    # Beside the child-specific vertical drop, above the child box.
    y = (drop_top_y + child.top_y_px) / 2
    x = child.center_x_px + 8
    return [
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" font-size="{font}" fill="{escape(color)}" '
        f'text-anchor="start">{escape(label)}</text>']


def _render_node(node: StructureNode, style: dict[str, Any],
                 colors: dict[str, str]) -> list[str]:
    fill = _color_for_role(node.role, colors)
    radius = _fmt(style.get("corner_radius_px", 18))
    name_font = float(style.get("name_font_size_px", 19))
    role_font = _fmt(style.get("role_font_size_px", 17))
    text_color = style.get("text_color_hex", "#FFFFFF")
    x = node.center_x_px - node.width_px / 2
    y = node.top_y_px
    out = [
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(node.width_px)}" '
        f'height="{_fmt(node.height_px)}" rx="{radius}" ry="{radius}" '
        f'fill="{escape(fill)}"/>']

    lines = _wrap(node.display_name, node.width_px, name_font)
    # Reduce to 15px only when needed to prevent overflow (deterministic rule).
    if len(lines) > 3:
        name_font = 15.0
        lines = _wrap(node.display_name, node.width_px, name_font)
    name_font_s = _fmt(name_font)
    # Vertical centring of name block + role line below.
    line_h = name_font * 1.15
    role_text = f"({node.role})" if node.role else ""
    total_h = line_h * len(lines) + (float(role_font) * 1.25 if role_text else 0)
    start_y = node.top_y_px + node.height_px / 2 - total_h / 2 + name_font
    for i, line in enumerate(lines):
        ty = start_y + i * line_h
        out.append(
            f'<text x="{_fmt(node.center_x_px)}" y="{_fmt(ty)}" '
            f'font-size="{name_font_s}" fill="{escape(text_color)}" '
            f'font-weight="bold" text-anchor="middle">{escape(line)}</text>')
    if role_text:
        ty = start_y + len(lines) * line_h
        out.append(
            f'<text x="{_fmt(node.center_x_px)}" y="{_fmt(ty)}" '
            f'font-size="{role_font}" fill="{escape(text_color)}" '
            f'text-anchor="middle">{escape(role_text)}</text>')

    if node.is_guarantor:
        out.extend(_render_guarantor_shield(node, style.get("guarantor_shield") or {}))
    return out


def _render_guarantor_shield(node: StructureNode, shield: dict[str, Any]) -> list[str]:
    size = float(shield.get("size_px", 32))
    stroke = shield.get("stroke_hex", "#000000")
    # Lower-right corner, inside the node.
    right = node.center_x_px + node.width_px / 2
    bottom = node.top_y_px + node.height_px
    cx = right - size / 2 - 6
    top = bottom - size - 6
    half = size / 2
    # Shield outline (pointed bottom) + checkmark.
    path = (f"M {_fmt(cx - half)} {_fmt(top)} "
            f"L {_fmt(cx + half)} {_fmt(top)} "
            f"L {_fmt(cx + half)} {_fmt(top + half)} "
            f"Q {_fmt(cx + half)} {_fmt(top + size)} {_fmt(cx)} {_fmt(top + size)} "
            f"Q {_fmt(cx - half)} {_fmt(top + size)} {_fmt(cx - half)} {_fmt(top + half)} Z")
    check = (f"M {_fmt(cx - half * 0.45)} {_fmt(top + half * 0.9)} "
             f"L {_fmt(cx - half * 0.1)} {_fmt(top + half * 1.3)} "
             f"L {_fmt(cx + half * 0.55)} {_fmt(top + half * 0.5)}")
    return [
        f'<path d="{path}" fill="none" stroke="{escape(stroke)}" stroke-width="2" '
        f'data-guarantor="true"/>',
        f'<path d="{check}" fill="none" stroke="{escape(stroke)}" stroke-width="2"/>',
    ]


def _render_header(header: dict[str, Any], company_display_name: str) -> list[str]:
    if not header:
        return []
    out: list[str] = []
    title = header.get("title", "Corporate Structure")
    tp = header.get("title_position") or {}
    out.append(
        f'<text x="{_fmt(tp.get("x_px", 32))}" y="{_fmt(tp.get("y_px", 26) + header.get("title_font_size_px", 53) * 0.8)}" '
        f'font-size="{_fmt(header.get("title_font_size_px", 53))}" '
        f'fill="{escape(header.get("title_color_hex", "#142340"))}" '
        f'font-weight="bold" text-anchor="start">{escape(title)}</text>')
    subtitle_format = header.get("subtitle_format")
    if subtitle_format:
        subtitle = subtitle_format.replace("{company_display_name}", company_display_name)
        sp = header.get("subtitle_position") or {}
        out.append(
            f'<text x="{_fmt(sp.get("x_px", 34))}" y="{_fmt(sp.get("y_px", 92) + header.get("subtitle_font_size_px", 26) * 0.8)}" '
            f'font-size="{_fmt(header.get("subtitle_font_size_px", 26))}" '
            f'fill="{escape(header.get("subtitle_color_hex", "#142340"))}" '
            f'text-anchor="start">{escape(subtitle)}</text>')
    # No extra header disclaimer line is emitted (negative_requirements).
    return out


def _render_legend(legend: dict[str, Any]) -> list[str]:
    if not legend:
        return []
    x = float(legend.get("x_px", 1155))
    y = float(legend.get("y_px", 25))
    w = _fmt(legend.get("width_px", 255))
    h = _fmt(legend.get("height_px", 250))
    out = [
        f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{w}" height="{h}" '
        f'fill="{escape(legend.get("background_hex", "#F7F9FB"))}" '
        f'stroke="{escape(legend.get("border_hex", "#C8D4DF"))}"/>']
    entries = legend.get("entries") or []
    row_h = 34
    for i, entry in enumerate(entries):
        ey = y + 24 + i * row_h
        if entry.get("color_hex"):
            out.append(
                f'<rect x="{_fmt(x + 14)}" y="{_fmt(ey - 16)}" width="22" height="22" '
                f'rx="4" fill="{escape(entry["color_hex"])}"/>')
        else:
            # Guarantor shield symbol entry.
            out.append(
                f'<path d="M {_fmt(x + 16)} {_fmt(ey - 16)} L {_fmt(x + 36)} {_fmt(ey - 16)} '
                f'L {_fmt(x + 36)} {_fmt(ey - 6)} Q {_fmt(x + 36)} {_fmt(ey + 4)} {_fmt(x + 26)} {_fmt(ey + 4)} '
                f'Q {_fmt(x + 16)} {_fmt(ey + 4)} {_fmt(x + 16)} {_fmt(ey - 6)} Z" '
                f'fill="none" stroke="#000000" stroke-width="2"/>')
        out.append(
            f'<text x="{_fmt(x + 46)}" y="{_fmt(ey)}" font-size="16" fill="#142340" '
            f'text-anchor="start">{escape(str(entry.get("label", "")))}</text>')
    # No legend footnote or note outside the listed entries (negative_requirements).
    return out


def _render_footer(footer: dict[str, Any]) -> list[str]:
    if not footer or not footer.get("text"):
        return []
    return [
        f'<text x="{_fmt(footer.get("x_px", 30))}" y="{_fmt(footer.get("y_px", 1038))}" '
        f'font-size="{_fmt(footer.get("font_size_px", 16))}" '
        f'fill="{escape(footer.get("color_hex", "#6A6A6A"))}" '
        f'text-anchor="start">{escape(footer["text"])}</text>']


def export_png(svg: str, path: str) -> bool:
    """Optional PNG export. Returns False (no-op) when no rasteriser is available.

    PNG rendering is optional per the spec; the authoritative artifact is the
    deterministic SVG. No network call is made.
    """
    try:
        import cairosvg  # type: ignore
    except Exception:
        return False
    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=path)
    return True
