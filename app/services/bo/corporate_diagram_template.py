"""Reusable, versioned corporate-diagram presentation template.

This module holds the *generic* presentation specification for a corporate-
structure diagram — colours by role, node styling, connector rules, legend,
footnote and negative rendering requirements. It is derived from the supplied
sample final-output under ``examples/bo_m3/`` but contains **no company-specific
facts**: no entity names, no ids, no hierarchy, no per-node coordinates and no
company display name. Those are supplied at transform time from the validated
onboarding graph.

Separating the template (generic styling) from the facts (company graph) means
the same template renders any company whose simplified onboarding JSON follows
the v1.0 contract; no particular company's structure is baked in.

The template is versioned so an audit record can tie a produced diagram to the
exact presentation code that generated it. Changing any styling value here is a
version bump.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


# Bump when any value in CORP_DIAGRAM_TEMPLATE changes so diagrams stay auditable.
CORP_DIAGRAM_TEMPLATE_VERSION = "bo-m3-corp-diagram-template-2.0.0"


# The generic visual specification. Mirrors the sample final-output's visual_spec
# verbatim for styling, but is company-agnostic: it holds only reusable rules.
# ``subtitle_format`` and ``colors_by_role`` are generic; the company display
# name and the concrete nodes/links are injected by the transformer.
_VISUAL_SPEC_TEMPLATE: dict[str, Any] = {
    "canvas": {
        "width_px": 1440,
        "height_px": 1080,
        "aspect_ratio": "4:3",
        "background_hex": "#FFFFFF",
    },
    "header": {
        "title": "Corporate Structure",
        "title_position": {"x_px": 32, "y_px": 26},
        "title_font_size_px": 53,
        "title_color_hex": "#142340",
        "subtitle_format": "{company_display_name} (Illustrative / Simulated)",
        "subtitle_position": {"x_px": 34, "y_px": 92},
        "subtitle_font_size_px": 26,
        "subtitle_color_hex": "#142340",
    },
    "colors_by_role": {
        "Parent Co": "#74150F",
        "Parent / Borrower": "#74150F",
        "HoldCo": "#B34B08",
        "Borrower": "#C25A13",
        "OpCo": "#D99337",
        "Other": "#A7A7A7",
        "JV": "#A7A7A7",
    },
    "node_style": {
        "shape": "rounded_rectangle",
        "corner_radius_px": 18,
        "fill": "look up colors_by_role using node.role",
        "name_font": "Arial or Aptos Bold",
        "name_font_size_px": 19,
        "role_font_size_px": 17,
        "text_color_hex": "#FFFFFF",
        "horizontal_alignment": "center",
        "vertical_alignment": "center",
        "name_wrapping": (
            "Wrap long names into 2-3 centered lines within their rectangle. "
            "Reduce size to 15 px only when needed to prevent overflow."
        ),
        "role_format": "({role})",
        "role_name_override": (
            "If role is Parent / Borrower, show (Parent / Borrower) exactly."
        ),
        "guarantor_shield": {
            "display_when": "node.is_guarantor is true",
            "shape": "outlined shield with checkmark",
            "stroke_hex": "#000000",
            "fill": "none",
            "position": "inside node near lower-right corner",
            "size_px": 32,
        },
    },
    "ownership_links_style": {
        "style": (
            "black orthogonal (right-angle) connector from bottom center of "
            "parent node to top center of child node"
        ),
        "line_color_hex": "#141414",
        "line_width_px": 3,
        "sibling_grouping": (
            "For multiple children, draw one horizontal bus per parent with "
            "individual vertical drops to each child; all drops attach to the "
            "same parent."
        ),
        "bus_y_rule": (
            "Bus lies approximately midway between the parent bottom and child "
            "top; do not overlap any node."
        ),
        "percentage_label": {
            "format": "{ownership_percent}%",
            "font_size_px": 19,
            "font_color_hex": "#111111",
            "location": "beside child-specific vertical drop, above child box",
            "applies_to": "one specified parent-child link only",
        },
        "avoid": (
            "Crossed lines, arrowheads, percent labels disconnected from their "
            "links, ambiguous step-parent relationships."
        ),
        "single_child_rule": (
            "If a parent has exactly one child, do not draw a horizontal bus. "
            "Draw one perfectly straight vertical connector from the parent "
            "bottom center to the child top center, with no elbow, no jog and "
            "no lateral offset."
        ),
        "single_child_alignment_rule": (
            "For any one-to-one parent-child relationship, the child node "
            "center_x_px must equal the parent node center_x_px. If supplied "
            "coordinates differ slightly, snap the child to the parent centerline."
        ),
        "bus_exception_rule": (
            "Use a horizontal bus only when a parent has two or more children."
        ),
    },
    "legend": {
        "x_px": 1155,
        "y_px": 25,
        "width_px": 255,
        "height_px": 250,
        "background_hex": "#F7F9FB",
        "border_hex": "#C8D4DF",
        "entries": [
            {"label": "Parent Co", "color_hex": "#74150F"},
            {"label": "HoldCo", "color_hex": "#B34B08"},
            {"label": "Borrower", "color_hex": "#C25A13"},
            {"label": "Operating Co", "color_hex": "#D99337"},
            {"label": "Other / JV", "color_hex": "#A7A7A7"},
            {"label": "Illustrative guarantor",
             "symbol": "outlined shield with checkmark"},
        ],
    },
    "footer": {
        "text": (
            "Illustrative only — simulated legal entities, ownership stakes and "
            "guarantor assignments; not actual corporate information."
        ),
        "x_px": 30,
        "y_px": 1038,
        "font_size_px": 16,
        "color_hex": "#6A6A6A",
    },
    "negative_requirements": [
        "No JPMorgan or any other bank name.",
        "No loan arrows or proposed-loan callouts.",
        "No debt obligations list.",
        "No credit commentary or bottom callout panels.",
        "No financial metrics, data tables, or invented numbers.",
        "No spell-check underlines or image watermarks.",
        "No extra header disclaimer line below the subtitle.",
        "No legend footnote or legend note outside the listed legend entries.",
        "For single-child relationships, no bent, stepped or diagonal-looking "
        "connectors.",
    ],
}


# The default simulation disclaimer applied when the onboarding document declares
# a simulated/educational data_status. It is generic; company specifics are not
# embedded. Real-case documents that are not flagged simulated receive no
# fabricated disclaimer (the transformer leaves it unset).
SIMULATION_DISCLAIMER_TEMPLATE = (
    "THIS {company_display_name_upper} ORGANISATION CHART IS A SIMULATED "
    "EDUCATIONAL EXAMPLE. Listed legal entities, ownership percentages, borrower "
    "designation and guarantor assignments are hypothetical and must not be "
    "described as {company_display_name}'s verified actual corporate structure."
)

SIMULATION_STATUS_TEMPLATE = (
    "Real company/brand used as inspiration; ownership hierarchy, subsidiaries "
    "and guarantee markings are assumed and may not match reality."
)

# Generic reproduction guidance (company-agnostic). Kept verbatim-style from the
# sample's how_to_use, with the single-file / explicit-upload constraints that
# enforce the no-auto-load rule.
HOW_TO_USE_TEMPLATE = [
    "This file contains one case only: a single company entry under companies. "
    "Other companies must be supplied through their own separate single-company "
    "corporate-structure JSON files using this same schema.",
    "Select the sole company entry and draw the structure from its nodes, "
    "ownership_links and visual_spec; do not infer or invent missing nodes or "
    "relationships.",
    "Use display_name exactly, including punctuation and capitalization, and "
    "show role below each company name.",
    "Render percentage labels only from ownership_links, and never infer "
    "unstated ownership.",
    "Draw a shield when and only when is_guarantor=true.",
    "Draw only the corporate structure, title, subtitle, legend and simulation "
    "footnote. No debt table, financial analysis, proposed loan, bank branding "
    "or other panels.",
    "If the rendering system does not support precise pixel positioning, "
    "preserve hierarchy, left-to-right sibling order, node grouping, percentage "
    "labels and shield markers.",
    "Never use this example unless it has been explicitly uploaded or associated "
    "with the current analysis case; do not use it to infer any other company "
    "structure.",
]


def visual_spec_template() -> dict[str, Any]:
    """Return a deep copy of the generic visual-spec template (never mutate)."""
    return deepcopy(_VISUAL_SPEC_TEMPLATE)


def role_color(role: str) -> str | None:
    """Look up the template colour for a role (case-insensitive fallback)."""
    colors = _VISUAL_SPEC_TEMPLATE["colors_by_role"]
    if role in colors:
        return colors[role]
    lowered = {k.casefold(): v for k, v in colors.items()}
    return lowered.get((role or "").casefold())
