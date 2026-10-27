"""Strict deterministic ownership conversion; no model dependencies."""
from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
import json
import math
import re
import textwrap
from html import escape


def design():
    return json.loads(files(__package__).joinpath("design.json").read_text(encoding="utf-8"))


def validate_structure_input(document):
    """Validate submitted onboarding evidence without producing a visualization."""
    if not isinstance(document, dict):
        raise ValueError("Corporate input must be an object")
    if document.get("schema_version") != "1.0" or document.get(
        "document_type"
    ) != "corporate_structure_onboarding":
        raise ValueError("Expected corporate_structure_onboarding schema 1.0")
    company = document.get("company_name")
    status = document.get("data_status")
    if (not isinstance(company, str) or not company.strip()
            or re.search(r'[/\\\x00-\x1f]', company)):
        raise ValueError("company_name must be nonempty and safe for a filename")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("data_status is required; provenance cannot be inferred")
    roles = set(design()["visual_spec"]["colors_by_role"])
    seen: set[str] = set()

    def visit(raw, *, root=False, depth=0):
        if depth > 100:
            raise ValueError("Tree exceeds supported depth 100; split the input")
        if not isinstance(raw, dict):
            raise ValueError("Every tree node must be an object")
        for field in ("id", "name", "role"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise ValueError(f"Node {field} must be a nonempty string")
        if raw["id"] in seen:
            raise ValueError(f"Duplicate node ID or cycle: {raw['id']}")
        seen.add(raw["id"])
        if raw["role"] not in roles:
            raise ValueError(f"Unsupported role: {raw['role']}")
        if type(raw.get("is_guarantor")) is not bool:
            raise ValueError(f"{raw['id']}: is_guarantor must be a boolean")
        if root and "ownership_percent_from_parent" in raw:
            raise ValueError("Root cannot have ownership_percent_from_parent")
        if not root:
            value = raw.get("ownership_percent_from_parent")
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= 100):
                raise ValueError(
                    f"{raw['id']}: ownership percentage must be numeric 0..100"
                )
        if "record_type" in raw and (
                not isinstance(raw["record_type"], str)
                or not raw["record_type"].strip()):
            raise ValueError(f"{raw['id']}: record_type must be a nonempty string")
        children = raw.get("children", [])
        if not isinstance(children, list):
            raise ValueError(f"{raw['id']}: children must be an array")
        for child in children:
            visit(child, depth=depth + 1)

    visit(document.get("corporate_tree"), root=True)
    return document


def convert_structure(document):
    validate_structure_input(document)
    if not isinstance(document, dict):
        raise ValueError("Corporate input must be an object")
    if document.get("schema_version") != "1.0" or document.get("document_type") != "corporate_structure_onboarding":
        raise ValueError("Expected corporate_structure_onboarding schema 1.0")
    company = document.get("company_name")
    status = document.get("data_status")
    if not isinstance(company, str) or not company.strip() or re.search(r'[/\\\x00-\x1f]', company):
        raise ValueError("company_name must be nonempty and safe for a filename")
    if not isinstance(status, str) or not status.strip():
        raise ValueError("data_status is required; provenance cannot be inferred")
    template = design()
    nodes, links, children, seen = [], [], {}, set()

    def walk(raw, parent=None, depth=0):
        if depth > 100:
            raise ValueError("Tree exceeds supported depth 100; split the input")
        if not isinstance(raw, dict):
            raise ValueError("Every tree node must be an object")
        for field in ("id", "name", "role"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise ValueError(f"Node {field} must be a nonempty string")
        nid = raw["id"]
        if nid in seen:
            raise ValueError(f"Duplicate node ID or cycle: {nid}")
        seen.add(nid)
        if raw["role"] not in template["visual_spec"]["colors_by_role"]:
            raise ValueError(f"Unsupported role: {raw['role']}")
        if type(raw.get("is_guarantor")) is not bool:
            raise ValueError(f"{nid}: is_guarantor must be a boolean")
        node = {"id": nid, "display_name": raw["name"], "role": raw["role"],
                "is_guarantor": raw["is_guarantor"]}
        if "record_type" in raw:
            if not isinstance(raw["record_type"], str) or not raw["record_type"].strip():
                raise ValueError(f"{nid}: record_type must be a nonempty string")
            node["record_type"] = raw["record_type"]
        if parent is not None:
            percent = raw.get("ownership_percent_from_parent")
            if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
                raise ValueError(f"{nid}: ownership percentage must be numeric 0..100")
            links.append({"parent_id": parent, "child_id": nid, "ownership_percent": percent})
        elif "ownership_percent_from_parent" in raw:
            raise ValueError("Root cannot have ownership_percent_from_parent")
        nodes.append(node)
        kids = raw.get("children", [])
        if not isinstance(kids, list):
            raise ValueError(f"{nid}: children must be an array")
        children[nid] = []
        for child in kids:
            child_id = walk(child, nid, depth + 1)
            children[nid].append(child_id)
        return nid

    root = walk(document.get("corporate_tree"))
    by_id = {n["id"]: n for n in nodes}
    reference = template.pop("reference_boxes")
    reference_edges = template.pop("reference_edges")
    hca_layout = set(by_id) == set(reference) and [
        [e["parent_id"], e["child_id"]] for e in links] == reference_edges
    spec = template["visual_spec"]
    if hca_layout:
        for nid, box in reference.items():
            by_id[nid]["box"] = box
        # Reference visual order is breadth-first, preserving sibling order.
        nodes.sort(key=lambda n: (n["box"]["top_y_px"], n["box"]["center_x_px"]))
    else:
        weights = {}
        def weight(nid):
            weights[nid] = sum(weight(c) for c in children[nid]) or 1
            return weights[nid]
        width = max(1440, weight(root) * 260 + 80)
        max_depth = 0
        def place(nid, start, depth):
            nonlocal max_depth
            max_depth = max(depth, max_depth)
            by_id[nid]["box"] = {"center_x_px": start + weights[nid] * 130,
                                    "top_y_px": 300 + depth * 200,
                                    "width_px": 220, "height_px": 112}
            cursor = start
            for child in children[nid]:
                place(child, cursor, depth + 1)
                cursor += weights[child] * 260
        place(root, (width - weights[root] * 260) / 2, 0)
        height = max(1080, 300 + max_depth * 200 + 200)
        spec["canvas"].update(width_px=width, height_px=height,
                              aspect_ratio=f"{width}:{height}")
        spec["legend"]["x_px"] = width - 285
        spec["footer"]["y_px"] = height - 42
    connectors = []
    for link in links:
        p, c = (by_id[link[k]]["box"] for k in ("parent_id", "child_id"))
        x, y, cx, cy = p["center_x_px"], p["top_y_px"] + p["height_px"], c["center_x_px"], c["top_y_px"]
        if len(children[link["parent_id"]]) == 1:
            if cx != x:
                raise ValueError("Layout invariant failed: single-child alignment")
            points = [[x, y], [cx, cy]]
        else:
            bus = (y + cy) / 2
            points = [[x, y], [x, bus], [cx, bus], [cx, cy]]
        connectors.append({"parent_id": link["parent_id"], "child_id": link["child_id"],
                           "points": points, "label": {"x_px": cx + 8, "y_px": cy - 16}})
    percent_by_child = {e["child_id"]: e["ownership_percent"] for e in links}
    tree = []
    def lines(nid, prefix="", connector="", tail=True):
        n = by_id[nid]
        label = (f"{percent_by_child[nid]:g}% → " if nid != root else "")
        tree.append(prefix + connector + label + n["display_name"] + f" ({n['role']})" +
                    (" [GUARANTOR SHIELD]" if n["is_guarantor"] else ""))
        for i, child in enumerate(children[nid]):
            last = i == len(children[nid]) - 1
            lines(child, prefix + (("    " if tail else "│   ") if nid != root else ""),
                  "└── " if last else "├── ", last)
    lines(root)
    simulated = "simulat" in status.lower()
    template["simulation_disclaimer"] = (
        f"THIS {company.upper()} ORGANISATION CHART IS A SIMULATED EDUCATIONAL EXAMPLE. "
        "Listed legal entities, ownership percentages, borrower designation and guarantor assignments "
        "are authoritative educational case assumptions and must not be described as verified actual corporate structure."
        if simulated else "Submitted ownership information; not independently verified against legal records.")
    template["purpose"] = "Reproduce only the submitted corporate ownership structure and its provenance."
    if not simulated:
        spec["header"]["subtitle_format"] = "{company_display_name} (Submitted / Unverified)"
        spec["footer"]["text"] = "Submitted corporate information; not independently verified."
    key = "HCA" if company == "HCA Healthcare" else re.sub(r"\W+", "_", company).strip("_") or "company"
    template["how_to_use"] = [s.replace("companies.HCA", "companies." + key).replace("current HCA analysis case", "current " + company + " analysis case") for s in template["how_to_use"]]
    template["revision_notes"] = ["Deterministic conversion from submitted schema 1.0; exact IDs, roles, ownership, record types and guarantor flags retained.", "Reference visual design with explicit canvas expansion for larger trees."]
    template["companies"] = {key: {
        "company_display_name": company, "simulation_status": status, "root_node_id": root,
        "nodes": nodes, "ownership_links": links, "plain_text_tree": "\n".join(tree),
        "layout_note": "Reference HCA boxes" if hca_layout else "Disjoint 260 px leaf slots; 200 px levels; canvas expands without node compression.",
        "connector_paths": connectors,
    }}
    template["provenance"] = {"data_status": status, "source_sha256": sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "input_schema_version": "1.0", "converter_version": "m3-rebuild-1.0",
        "authority": "submitted case assumptions; no independent legal verification"}
    template["completion_status"] = "complete"
    validate_structure(template)
    return template


def validate_structure(artifact):
    if artifact.get("schema_version") != "2.0" or artifact.get("artifact_type") != "llm_reproducible_corporate_structure_diagrams":
        raise ValueError("Unsupported corporate visualization contract")
    for key in ("purpose", "simulation_disclaimer", "how_to_use", "visual_spec", "companies", "provenance"):
        if key not in artifact:
            raise ValueError(f"Missing visualization field: {key}")
    if not isinstance(artifact["companies"], dict) or len(artifact["companies"]) != 1:
        raise ValueError("Visualization must contain exactly one company")
    company = next(iter(artifact["companies"].values()))
    by_id = {}
    for node in company["nodes"]:
        if node["id"] in by_id or type(node["is_guarantor"]) is not bool:
            raise ValueError("Invalid visualization node ID or guarantor flag")
        if not node["display_name"] or node["role"] not in artifact["visual_spec"]["colors_by_role"]:
            raise ValueError("Unsupported visualization node name/role")
        by_id[node["id"]] = node
    if company["root_node_id"] not in by_id:
        raise ValueError("Unresolved root node")
    parents, children = {}, {}
    for link in company["ownership_links"]:
        p, c = link["parent_id"], link["child_id"]
        value = link["ownership_percent"]
        if p not in by_id or c not in by_id or c in parents or p == c or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Invalid ownership link/reference/percentage")
        parents[c] = p
        children.setdefault(p, []).append(c)
    root = company["root_node_id"]
    if root in parents or set(parents) != set(by_id) - {root}:
        raise ValueError("Visualization is not a rooted ownership tree")
    for nid in by_id:
        visited = set()
        while nid in parents:
            if nid in visited:
                raise ValueError("Ownership cycle")
            visited.add(nid)
            nid = parents[nid]
    expected_edges = [(l["parent_id"], l["child_id"]) for l in company["ownership_links"]]
    if [(p["parent_id"], p["child_id"]) for p in company["connector_paths"]] != expected_edges:
        raise ValueError("Connector references differ from ownership links")
    for path in company["connector_paths"]:
        p, c = by_id[path["parent_id"]]["box"], by_id[path["child_id"]]["box"]
        if path["points"][0] != [p["center_x_px"], p["top_y_px"] + p["height_px"]] or path["points"][-1] != [c["center_x_px"], c["top_y_px"]]:
            raise ValueError("Connector endpoint does not attach to its node")
        if len(children[path["parent_id"]]) == 1 and (len(path["points"]) != 2 or c["center_x_px"] != p["center_x_px"]):
            raise ValueError("Single-child connector must be straight and aligned")
    validate_geometry(artifact)
    return artifact


def validate_geometry(artifact):
    company = next(iter(artifact["companies"].values()))
    nodes = company["nodes"]
    width, height = (artifact["visual_spec"]["canvas"][k] for k in ("width_px", "height_px"))
    boxes = []
    for n in nodes:
        b = n["box"]
        x, y, w, h = b["center_x_px"], b["top_y_px"], b["width_px"], b["height_px"]
        rect = (x - w / 2, y, x + w / 2, y + h)
        if not (0 <= rect[0] < rect[2] <= width and 130 <= y < y + h < height - 60):
            raise ValueError(f"Node out of bounds: {n['id']}")
        for other in boxes:
            if rect[0] < other[2] and rect[2] > other[0] and rect[1] < other[3] and rect[3] > other[1]:
                raise ValueError("Overlapping nodes")
        boxes.append(rect)
    for connector in company["connector_paths"]:
        for a, b in zip(connector["points"], connector["points"][1:]):
            if a[0] != b[0] and a[1] != b[1]:
                raise ValueError("Non-orthogonal connector")
            for left, top, right, bottom in boxes:
                if a[0] == b[0] and left < a[0] < right and max(a[1], b[1]) > top and min(a[1], b[1]) < bottom:
                    raise ValueError("Connector crosses a node")
                if a[1] == b[1] and top < a[1] < bottom and max(a[0], b[0]) > left and min(a[0], b[0]) < right:
                    raise ValueError("Connector crosses a node")


    segments = []
    for connector in company["connector_paths"]:
        for a, b in zip(connector["points"], connector["points"][1:]):
            if a == b:
                continue
            for owner, c, d in segments:
                if owner == connector["parent_id"]:
                    continue
                if a[0] == b[0] and c[0] == d[0]:
                    crossing = a[0] == c[0] and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))
                elif a[1] == b[1] and c[1] == d[1]:
                    crossing = a[1] == c[1] and max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
                else:
                    v1, v2, h1, h2 = (a, b, c, d) if a[0] == b[0] else (c, d, a, b)
                    crossing = min(h1[0], h2[0]) <= v1[0] <= max(h1[0], h2[0]) and min(v1[1], v2[1]) <= h1[1] <= max(v1[1], v2[1])
                if crossing:
                    raise ValueError("Ownership connectors from different parents cross")
            segments.append((connector["parent_id"], a, b))


def structure_filename(artifact):
    company = next(iter(artifact["companies"].values()))["company_display_name"]
    return f"Visualized {company} Corporate structure.json"


def render_svg(artifact):
    """Render from the final contract alone; SVG has no external resources."""
    spec = artifact["visual_spec"]
    company = next(iter(artifact["companies"].values()))
    canvas = spec["canvas"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas["width_px"]}" height="{canvas["height_px"]}" viewBox="0 0 {canvas["width_px"]} {canvas["height_px"]}">',
             '<rect width="100%" height="100%" fill="white"/>']
    def text(x, y, value, size=19, color="#142340", anchor="start", bold=False):
        parts.append(f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{"bold" if bold else "normal"}">{escape(str(value))}</text>')
    header = spec["header"]
    text(32, 68, header["title"], 53, bold=True)
    text(34, 116, header["subtitle_format"].format(company_display_name=company["company_display_name"]), 26)
    for path, link in zip(company["connector_paths"], company["ownership_links"]):
        pts = " ".join(f"{x},{y}" for x, y in path["points"])
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#141414" stroke-width="3"/>')
        text(path["label"]["x_px"], path["label"]["y_px"], f'{link["ownership_percent"]:g}%', 19, "#111111")
    def shield(x, y, size=25):
        parts.append(f'<path d="M {x},{y} l {size/2},-5 l {size/2},5 v 12 q 0,10 -{size/2},17 q -{size/2},-7 -{size/2},-17 Z" fill="none" stroke="black" stroke-width="2"/>')
        parts.append(f'<path d="M {x+5},{y+10} l 5,5 l 10,-12" fill="none" stroke="black" stroke-width="2"/>')
    for node in company["nodes"]:
        b = node["box"]
        x, y, w, h = (b[k] for k in ("center_x_px", "top_y_px", "width_px", "height_px"))
        parts.append(f'<rect x="{x-w/2}" y="{y}" width="{w}" height="{h}" rx="18" fill="{spec["colors_by_role"][node["role"]]}"/>')
        # Deterministic conservative text fitting; explicit failure beats overflow.
        size = 19
        while True:
            wrapped = textwrap.wrap(node["display_name"], width=max(8, int((w-22)/(size*.59))), break_long_words=True)
            if len(wrapped) <= 3 or size == 15:
                break
            size -= 1
        if len(wrapped) > 3:
            raise ValueError(f"Name exceeds readable layout: {node['id']}; shorten display name or enlarge node policy")
        group_height = len(wrapped) * (size + 2) + 25
        first_baseline = y + (h - group_height) / 2 + size
        for i, line in enumerate(wrapped):
            text(x, first_baseline + i*(size+2), line, size, "white", "middle", True)
        text(x, first_baseline + len(wrapped)*(size+2) + 3, f'({node["role"]})', 17, "white", "middle")
        if node["is_guarantor"]:
            shield(x + w/2 - 31, y + h - 35)
    legend = spec["legend"]
    lx, ly = legend["x_px"], legend["y_px"]
    parts.append(f'<rect x="{lx}" y="{ly}" width="255" height="250" rx="8" fill="#F7F9FB" stroke="#C8D4DF"/>')
    for i, entry in enumerate(legend["entries"]):
        yy = ly + 30 + i*37
        if "color_hex" in entry:
            parts.append(f'<rect x="{lx+15}" y="{yy-17}" width="23" height="23" rx="4" fill="{entry["color_hex"]}"/>')
        else:
            shield(lx+15, yy-16, 23)
        text(lx+47, yy, entry["label"], 17)
    foot = spec["footer"]
    text(foot["x_px"], foot["y_px"], foot["text"], foot["font_size_px"], foot["color_hex"])
    return "\n".join(parts + ["</svg>"])
