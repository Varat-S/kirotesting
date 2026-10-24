"""Simplified v1.0 corporate-structure onboarding -> rich v2.0 refined JSON.

This is the heart of the Business Overview V6 Module 3 refactor. A company
supplies a *simplified* onboarding document (``schema_version`` "1.0",
``document_type`` "corporate_structure_onboarding") that is a nested
``corporate_tree`` of entities with ids, names, roles, guarantor flags,
ownership percentages and optional ``record_type == "aggregate_group"`` grouping
markers. It carries **no** colours, coordinates, canvas or formatting.

This module:

1. parses + validates that simplified input into a canonical internal graph
   (``build_onboarding_graph``);
2. deterministically lays the graph out and formats it into the rich v2.0
   presentation-ready JSON using the reusable, versioned presentation template
   (``transform_to_refined_v2``) — the generated JSON conforms to the same
   general schema as the sample final-output under ``examples/bo_m3/``;
3. reconciles the generated v2.0 output back to the validated v1.0 input so an
   auditor can confirm no fact was fabricated, dropped or altered
   (``reconcile_roundtrip``).

Design rules honoured (Credit Memo North Star + Module 3 spec):

* Evidence first / no fabrication: every node, link, role, ownership percentage
  and guarantor flag in the v2.0 output comes *exactly* from the v1.0 input.
  Nothing invents missing data; aggregate grouping labels ("Other Subsidiaries",
  "JV / Minority Investments") are flagged and never treated as independently
  established legal persons.
* Separate generic styling from company facts: all styling comes from the
  reusable template; nothing about any specific company is hardcoded.
* Determinism / reproducibility: identical input yields byte-identical output
  (stable traversal order, canonical coordinate rounding, no timestamps/random
  ids). Layout is derived from hierarchy depth, sibling order, node dimensions
  and available space.
* Never mutate the source: the input document is deep-copied and read only.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.services.bo.corporate_diagram_template import (
    CORP_DIAGRAM_TEMPLATE_VERSION,
    HOW_TO_USE_TEMPLATE,
    SIMULATION_DISCLAIMER_TEMPLATE,
    SIMULATION_STATUS_TEMPLATE,
    visual_spec_template,
)
from app.schemas.bo_m3_structure import (
    CORP_STRUCTURE_TRANSFORM_VERSION,
    SUPPORTED_ARTIFACT_TYPES,
    SUPPORTED_ONBOARDING_DOCUMENT_TYPES,
    SUPPORTED_ONBOARDING_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSIONS,
)


# Role / name hints identifying an aggregate grouping label rather than a single
# named legal person. The explicit ``record_type == "aggregate_group"`` marker is
# authoritative; these hints are a secondary, conservative fallback so an
# unmarked "Other Subsidiaries" is still not treated as a legal person.
_GROUPING_ROLE_HINTS = ("other", "jv", "joint venture", "minority", "various")
_GROUPING_NAME_HINTS = ("other subsidiaries", "jv", "joint venture", "minority",
                        "various subsidiaries", "miscellaneous")


class OnboardingStructureError(ValueError):
    """Raised only for programmer errors; validation problems are returned."""


@dataclass(frozen=True)
class OnboardingNode:
    """A validated node from the simplified onboarding tree."""

    node_id: str
    name: str
    role: str
    is_guarantor: bool
    is_grouping: bool
    ownership_percent_from_parent: float | None
    parent_id: str | None
    sibling_index: int  # position among siblings (array order)
    depth: int  # 0 = root


@dataclass
class OnboardingGraph:
    """Canonical internal corporate graph parsed from the v1.0 input."""

    company_name: str
    data_status: str | None
    root_id: str
    nodes: list[OnboardingNode] = field(default_factory=list)
    # Preserved, unmutated copy of the original input document.
    original_document: dict[str, Any] = field(default_factory=dict)

    @property
    def nodes_by_id(self) -> dict[str, OnboardingNode]:
        return {n.node_id: n for n in self.nodes}

    def children_of(self, node_id: str) -> list[OnboardingNode]:
        return sorted(
            (n for n in self.nodes if n.parent_id == node_id),
            key=lambda n: n.sibling_index,
        )


def _is_grouping(role: str, name: str, record_type: str | None) -> bool:
    if record_type == "aggregate_group":
        return True
    role_l = (role or "").casefold()
    name_l = (name or "").casefold()
    return (any(h in role_l for h in _GROUPING_ROLE_HINTS)
            or any(h in name_l for h in _GROUPING_NAME_HINTS))


def _issue(issues: list[dict], rule_id: str, item_id: str | None, action: str,
           reason: str) -> None:
    issues.append({"rule_id": rule_id, "item_id": item_id, "action": action,
                   "reason": reason})


def build_onboarding_graph(
    document: dict[str, Any],
) -> tuple[OnboardingGraph | None, list[dict]]:
    """Parse + validate a simplified v1.0 onboarding document.

    Returns ``(graph, issues)``. ``graph`` is ``None`` when a fatal validation
    problem is found; ``issues`` always records the precise reason(s). The input
    document is never mutated (it is deep-copied before traversal).
    """

    issues: list[dict] = []
    if not isinstance(document, dict):
        _issue(issues, "m3.onboarding_malformed", None, "rejected",
               "Onboarding corporate-structure source is not a JSON object.")
        return None, issues

    document = deepcopy(document)
    schema_version = document.get("schema_version")
    document_type = document.get("document_type")
    if schema_version not in SUPPORTED_ONBOARDING_SCHEMA_VERSIONS:
        _issue(issues, "m3.onboarding_schema_version", None, "rejected",
               f"Unsupported onboarding schema_version {schema_version!r}; "
               f"supported: {list(SUPPORTED_ONBOARDING_SCHEMA_VERSIONS)}.")
        return None, issues
    if document_type not in SUPPORTED_ONBOARDING_DOCUMENT_TYPES:
        _issue(issues, "m3.onboarding_document_type", None, "rejected",
               f"Unsupported onboarding document_type {document_type!r}; "
               f"supported: {list(SUPPORTED_ONBOARDING_DOCUMENT_TYPES)}.")
        return None, issues

    company_name = document.get("company_name")
    if not isinstance(company_name, str) or not company_name.strip():
        _issue(issues, "m3.onboarding_company_name", None, "rejected",
               "Onboarding document is missing a company_name.")
        return None, issues

    tree = document.get("corporate_tree")
    if not isinstance(tree, dict) or not tree:
        _issue(issues, "m3.onboarding_corporate_tree", None, "rejected",
               "Onboarding document has no corporate_tree root object.")
        return None, issues

    # Recursive traversal in strict array (sibling) order; detects duplicate ids,
    # missing mandatory fields, invalid ownership percentages, invalid guarantor
    # flags and structural cycles (a node reachable twice).
    nodes: list[OnboardingNode] = []
    seen_ids: set[str] = set()
    fatal = False

    def walk(raw: Any, parent_id: str | None, sibling_index: int, depth: int,
             ancestors: frozenset[str]) -> None:
        nonlocal fatal
        if not isinstance(raw, dict):
            _issue(issues, "m3.onboarding_node_object",
                   parent_id, "rejected",
                   "A corporate_tree node is not a JSON object.")
            fatal = True
            return
        nid = raw.get("id")
        if not isinstance(nid, str) or not nid.strip():
            _issue(issues, "m3.onboarding_node_id", None, "rejected",
                   "A corporate_tree node is missing a non-empty string id.")
            fatal = True
            return
        if nid in seen_ids:
            _issue(issues, "m3.onboarding_node_id_unique", nid, "rejected",
                   "Duplicate entity id in corporate_tree.")
            fatal = True
            return
        if nid in ancestors:
            _issue(issues, "m3.onboarding_acyclic", nid, "rejected",
                   "corporate_tree contains a cycle (a node is its own ancestor).")
            fatal = True
            return
        seen_ids.add(nid)

        name = raw.get("name")
        role = raw.get("role")
        if not isinstance(name, str) or not name.strip():
            _issue(issues, "m3.onboarding_node_name", nid, "rejected",
                   "Entity is missing a non-empty name.")
            fatal = True
        if not isinstance(role, str) or not role.strip():
            _issue(issues, "m3.onboarding_node_role", nid, "rejected",
                   "Entity is missing a non-empty role.")
            fatal = True

        guarantor = raw.get("is_guarantor", False)
        if not isinstance(guarantor, bool):
            _issue(issues, "m3.onboarding_guarantor_flag", nid, "rejected",
                   "is_guarantor must be a boolean.")
            fatal = True
            guarantor = False

        pct = raw.get("ownership_percent_from_parent")
        if parent_id is None:
            # Root carries no ownership_percent_from_parent.
            if pct is not None:
                _issue(issues, "m3.onboarding_root_ownership", nid, "downgraded",
                       "Root node must not declare ownership_percent_from_parent; "
                       "ignored.")
                pct = None
        else:
            if pct is None or isinstance(pct, bool) or not isinstance(pct, (int, float)):
                _issue(issues, "m3.onboarding_ownership_present", nid, "rejected",
                       "Non-root entity is missing a numeric "
                       "ownership_percent_from_parent.")
                fatal = True
            elif not (0 <= float(pct) <= 100):
                _issue(issues, "m3.onboarding_ownership_bounds", nid, "rejected",
                       f"ownership_percent_from_parent {pct} is outside 0-100.")
                fatal = True

        record_type = raw.get("record_type")
        is_grouping = _is_grouping(role or "", name or "", record_type)

        nodes.append(OnboardingNode(
            node_id=nid, name=name if isinstance(name, str) else "",
            role=role if isinstance(role, str) else "",
            is_guarantor=bool(guarantor), is_grouping=is_grouping,
            ownership_percent_from_parent=(float(pct) if isinstance(pct, (int, float))
                                           and not isinstance(pct, bool) else None),
            parent_id=parent_id, sibling_index=sibling_index, depth=depth))

        children = raw.get("children")
        if children is None:
            return
        if not isinstance(children, list):
            _issue(issues, "m3.onboarding_children_list", nid, "rejected",
                   "children must be a list when present.")
            fatal = True
            return
        for i, child in enumerate(children):
            walk(child, nid, i, depth + 1, ancestors | {nid})

    root_id = tree.get("id") if isinstance(tree, dict) else None
    walk(tree, None, 0, 0, frozenset())

    if fatal or not nodes:
        return None, issues
    if not isinstance(root_id, str) or not root_id:
        _issue(issues, "m3.onboarding_root", None, "rejected",
               "corporate_tree has no single identifiable root.")
        return None, issues

    graph = OnboardingGraph(
        company_name=company_name.strip(),
        data_status=document.get("data_status"),
        root_id=root_id,
        nodes=nodes,
        original_document=document,
    )
    return graph, issues


# --- Deterministic layout ----------------------------------------------------

# Layout constants (generic, not company-specific). The transform derives node
# positions from hierarchy depth, sibling order, node dimensions and available
# space, matching the general look of the sample without copying the example's
# exact coordinates.
_CANVAS_WIDTH = 1440
_CANVAS_HEIGHT = 1080
_MARGIN_LEFT = 32
_MARGIN_RIGHT = 32
_FIRST_ROW_TOP = 180
_ROW_PITCH = 180      # vertical distance between successive depth levels
_NODE_WIDTH = 220
_NODE_HEIGHT = 100
_MIN_GAP = 24         # minimum horizontal gap between sibling subtrees


def _subtree_width(graph: OnboardingGraph, node_id: str,
                   widths: dict[str, float]) -> float:
    """Compute the horizontal space a subtree needs (deterministic, bottom-up)."""
    children = graph.children_of(node_id)
    if not children:
        widths[node_id] = _NODE_WIDTH
        return _NODE_WIDTH
    total = 0.0
    for i, child in enumerate(children):
        if i:
            total += _MIN_GAP
        total += _subtree_width(graph, child.node_id, widths)
    widths[node_id] = max(_NODE_WIDTH, total)
    return widths[node_id]


def _assign_positions(graph: OnboardingGraph) -> dict[str, dict[str, float]]:
    """Assign deterministic box coordinates per node.

    Single child -> centred directly below its parent (child center_x == parent
    center_x, no bend). Multiple children -> packed left-to-right within the
    parent's subtree band, then the parent is centred over them. Positions derive
    from hierarchy depth (y) and packed subtree widths (x).
    """
    widths: dict[str, float] = {}
    total = _subtree_width(graph, graph.root_id, widths)
    available = _CANVAS_WIDTH - _MARGIN_LEFT - _MARGIN_RIGHT
    # Centre the whole tree on the canvas; never run off the left margin.
    start_left = max(_MARGIN_LEFT, _MARGIN_LEFT + (available - total) / 2.0)

    boxes: dict[str, dict[str, float]] = {}

    def place(node_id: str, left: float) -> float:
        """Place the subtree rooted at ``node_id`` starting at x==``left``.

        Returns the node's assigned center_x. Children are placed first so the
        parent can be centred over them.
        """
        children = graph.children_of(node_id)
        node = graph.nodes_by_id[node_id]
        top_y = _FIRST_ROW_TOP + node.depth * _ROW_PITCH
        if not children:
            center_x = left + _NODE_WIDTH / 2.0
            boxes[node_id] = {"center_x_px": center_x, "top_y_px": float(top_y),
                              "width_px": float(_NODE_WIDTH),
                              "height_px": float(_NODE_HEIGHT)}
            return center_x
        if len(children) == 1:
            # Single child: align the child centreline to the parent. Place the
            # child subtree, then snap the parent to the child's centre.
            child_center = place(children[0].node_id, left)
            boxes[node_id] = {"center_x_px": child_center, "top_y_px": float(top_y),
                              "width_px": float(_NODE_WIDTH),
                              "height_px": float(_NODE_HEIGHT)}
            return child_center
        cursor = left
        child_centers: list[float] = []
        for i, child in enumerate(children):
            if i:
                cursor += _MIN_GAP
            cx = place(child.node_id, cursor)
            child_centers.append(cx)
            cursor += widths[child.node_id]
        center_x = (child_centers[0] + child_centers[-1]) / 2.0
        boxes[node_id] = {"center_x_px": center_x, "top_y_px": float(top_y),
                          "width_px": float(_NODE_WIDTH),
                          "height_px": float(_NODE_HEIGHT)}
        return center_x

    place(graph.root_id, start_left)

    # Horizontal fit: if the packed tree is wider than the available canvas band,
    # scale all node centres and widths uniformly about the left extent so
    # nothing overflows the margins or overlaps. A single global scale factor
    # keeps it deterministic and preserves single-child alignment (equal centres
    # scale to equal centres).
    left_edges = [b["center_x_px"] - b["width_px"] / 2.0 for b in boxes.values()]
    right_edges = [b["center_x_px"] + b["width_px"] / 2.0 for b in boxes.values()]
    extent_left = min(left_edges)
    extent = max(right_edges) - extent_left
    if extent > available and extent > 0:
        scale = available / extent
        for b in boxes.values():
            new_left = _MARGIN_LEFT + (b["center_x_px"] - b["width_px"] / 2.0
                                       - extent_left) * scale
            b["width_px"] = b["width_px"] * scale
            b["center_x_px"] = new_left + b["width_px"] / 2.0
    return boxes


def _round_px(value: float) -> float | int:
    """Canonical coordinate rounding for byte-stable output."""
    r = round(float(value), 2)
    return int(r) if r == int(r) else r


# --- v1.0 -> v2.0 transform --------------------------------------------------

def _plain_text_tree(graph: OnboardingGraph) -> str:
    """Render the ASCII hierarchy (deterministic) with guarantor markers."""
    lines: list[str] = []

    def render(node: OnboardingNode, prefix: str, is_last: bool,
               is_root: bool) -> None:
        shield = " [GUARANTOR SHIELD]" if node.is_guarantor else ""
        if is_root:
            lines.append(f"{node.name} ({node.role})")
            child_prefix = ""
        else:
            connector = "└── " if is_last else "├── "
            pct = node.ownership_percent_from_parent
            pct_s = f"{_round_px(pct)}% → " if pct is not None else ""
            lines.append(f"{prefix}{connector}{pct_s}{node.name} ({node.role}){shield}")
            child_prefix = prefix + ("    " if is_last else "│   ")
        children = graph.children_of(node.node_id)
        for i, child in enumerate(children):
            render(child, child_prefix, i == len(children) - 1, False)

    render(graph.nodes_by_id[graph.root_id], "", True, True)
    return "\n".join(lines)


def _bfs_order(graph: OnboardingGraph) -> list[OnboardingNode]:
    """Deterministic breadth-first node order (depth, then sibling order)."""
    order: list[OnboardingNode] = []
    frontier = [graph.nodes_by_id[graph.root_id]]
    while frontier:
        order.extend(frontier)
        nxt: list[OnboardingNode] = []
        for node in frontier:
            nxt.extend(graph.children_of(node.node_id))
        frontier = nxt
    return order


def transform_to_refined_v2(
    graph: OnboardingGraph, *, company_key: str | None = None,
) -> dict[str, Any]:
    """Deterministically build the rich v2.0 refined JSON from a validated graph.

    Structural facts (ids, names, roles, guarantor flags, ownership percentages,
    parent-child relationships, grouping info) are preserved exactly. Presentation
    comes entirely from the reusable, versioned template; layout is computed
    deterministically. The company key defaults to a slug of the company name but
    is never used for case selection.
    """

    boxes = _assign_positions(graph)
    ordered = _bfs_order(graph)

    simulated = _is_simulated(graph)
    company_display_name = graph.company_name
    key = company_key or _company_key(company_display_name)

    nodes_out: list[dict[str, Any]] = []
    for node in ordered:
        box = boxes[node.node_id]
        nodes_out.append({
            "id": node.node_id,
            "display_name": node.name,
            "role": node.role,
            "is_guarantor": node.is_guarantor,
            "box": {
                "center_x_px": _round_px(box["center_x_px"]),
                "top_y_px": _round_px(box["top_y_px"]),
                "width_px": _round_px(box["width_px"]),
                "height_px": _round_px(box["height_px"]),
            },
        })

    links_out: list[dict[str, Any]] = []
    for node in ordered:
        if node.parent_id is None:
            continue
        pct = node.ownership_percent_from_parent
        links_out.append({
            "parent_id": node.parent_id,
            "child_id": node.node_id,
            "ownership_percent": (_round_px(pct) if pct is not None else None),
        })

    visual_spec = visual_spec_template()

    company_entry: dict[str, Any] = {
        "company_display_name": company_display_name,
        "root_node_id": graph.root_id,
        "nodes": nodes_out,
        "ownership_links": links_out,
        "plain_text_tree": _plain_text_tree(graph),
        "layout_note": (
            "Positions generated deterministically from hierarchy depth and "
            "sibling order on a 1440x1080 canvas. Direct subsidiaries share a "
            "horizontal row; each deeper level occupies the next row. Single "
            "children are aligned directly beneath their parent."
        ),
    }
    if simulated:
        company_entry["simulation_status"] = SIMULATION_STATUS_TEMPLATE

    refined: dict[str, Any] = {
        "schema_version": SUPPORTED_SCHEMA_VERSIONS[0],
        "artifact_type": SUPPORTED_ARTIFACT_TYPES[0],
        "purpose": (
            "Reproduce one company's corporate-structure diagram from its "
            "simplified onboarding corporate tree, using only the supplied "
            "entity names, ownership stakes and guarantor markings. Single-"
            "company example of the reusable onboarding JSON format; no debt "
            "tables or lender commentary."
        ),
        "how_to_use": list(HOW_TO_USE_TEMPLATE),
        "visual_spec": visual_spec,
        "companies": {key: company_entry},
        "generated_by": {
            "transform_version": CORP_STRUCTURE_TRANSFORM_VERSION,
            "presentation_template_version": CORP_DIAGRAM_TEMPLATE_VERSION,
        },
    }
    if simulated:
        refined["simulation_disclaimer"] = SIMULATION_DISCLAIMER_TEMPLATE.format(
            company_display_name=company_display_name,
            company_display_name_upper=company_display_name.upper(),
        )
    return refined


def _is_simulated(graph: OnboardingGraph) -> bool:
    status = (graph.data_status or "").casefold()
    return "simulat" in status or "educational" in status or "illustrat" in status


def _company_key(company_display_name: str) -> str:
    """Deterministic, company-agnostic key derived from the display name.

    Used only as the single ``companies`` map key; never used to select or
    authorise a case (case association is carried by trusted metadata elsewhere).
    """
    token = "".join(ch if ch.isalnum() else "_" for ch in company_display_name)
    token = "_".join(part for part in token.split("_") if part)
    return token or "company"


# --- Round-trip reconciliation ----------------------------------------------

def reconcile_roundtrip(
    graph: OnboardingGraph, refined: dict[str, Any],
) -> dict[str, Any]:
    """Deterministically reconcile the v2.0 output to the validated v1.0 input.

    Confirms every entity, ownership link and guarantor marking in the refined
    output corresponds exactly to the onboarding graph and that nothing was
    fabricated, dropped or altered. Returns a structured record with an overall
    ``consistent`` flag and any discrepancies (empty when fully reconciled).
    """

    companies = refined.get("companies") or {}
    company = next(iter(companies.values()), {})
    out_nodes = company.get("nodes") or []
    out_links = company.get("ownership_links") or []

    discrepancies: list[dict[str, str]] = []

    in_nodes = {n.node_id: n for n in graph.nodes}
    out_node_map = {n["id"]: n for n in out_nodes}

    # Entity set must match exactly.
    missing = sorted(set(in_nodes) - set(out_node_map))
    extra = sorted(set(out_node_map) - set(in_nodes))
    for nid in missing:
        discrepancies.append({"kind": "node_missing_in_output", "id": nid})
    for nid in extra:
        discrepancies.append({"kind": "node_fabricated_in_output", "id": nid})

    guarantors_in = sorted(n.node_id for n in graph.nodes if n.is_guarantor)
    guarantors_out = sorted(n["id"] for n in out_nodes if n.get("is_guarantor"))

    for nid, src in in_nodes.items():
        out = out_node_map.get(nid)
        if out is None:
            continue
        if out.get("display_name") != src.name:
            discrepancies.append({"kind": "name_mismatch", "id": nid})
        if out.get("role") != src.role:
            discrepancies.append({"kind": "role_mismatch", "id": nid})
        if bool(out.get("is_guarantor")) != src.is_guarantor:
            discrepancies.append({"kind": "guarantor_mismatch", "id": nid})

    # Ownership links: parent->child plus percentage must match the input.
    in_links = {(n.parent_id, n.node_id): n.ownership_percent_from_parent
                for n in graph.nodes if n.parent_id is not None}
    out_links_map = {(l.get("parent_id"), l.get("child_id")): l.get("ownership_percent")
                     for l in out_links}
    for edge, pct in in_links.items():
        if edge not in out_links_map:
            discrepancies.append({"kind": "link_missing_in_output",
                                  "id": f"{edge[0]}->{edge[1]}"})
        elif _norm_pct(out_links_map[edge]) != _norm_pct(pct):
            discrepancies.append({"kind": "ownership_percent_mismatch",
                                  "id": f"{edge[0]}->{edge[1]}"})
    for edge in out_links_map:
        if edge not in in_links:
            discrepancies.append({"kind": "link_fabricated_in_output",
                                  "id": f"{edge[0]}->{edge[1]}"})

    return {
        "consistent": not discrepancies,
        "transform_version": CORP_STRUCTURE_TRANSFORM_VERSION,
        "input_entity_count": len(in_nodes),
        "output_entity_count": len(out_node_map),
        "input_link_count": len(in_links),
        "output_link_count": len(out_links_map),
        "input_guarantor_ids": guarantors_in,
        "output_guarantor_ids": guarantors_out,
        "grouping_node_ids": sorted(n.node_id for n in graph.nodes if n.is_grouping),
        "discrepancies": discrepancies,
    }


def _norm_pct(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), 4)
