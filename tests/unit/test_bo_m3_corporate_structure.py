"""Offline tests for the Module 3 corporate-structure deliverable.

Covers the generic single-company JSON adapter, Validator 7 structural checks,
the deterministic SVG renderer and the three-section output assembly (including
partial completion when the corporate-structure JSON is absent). No live model
or network call is made. HCA is only the development fixture; the generalisation
test uses a small synthetic company with the same schema.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from app.schemas.bo_m3_structure import NO_JSON_MESSAGE
from app.services.bo.corporate_structure import (
    CaseCorporateStructureSource,
    build_corporate_structure_artifact,
)
from app.services.bo.corporate_structure_render import render_corporate_structure_svg
from app.services.bo.m3_output import assemble_m3_output


FIXTURE = Path("examples/bo_m3/Refined HCA Corp Structure.json")


def _hca_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _hca_source(case_id="HCA_CASE", **overrides) -> CaseCorporateStructureSource:
    kwargs = dict(
        case_id=case_id, payload=_hca_payload(), source_document_id="corp-json-1",
        document_hash="hash-abc", registered_case_id=case_id)
    kwargs.update(overrides)
    return CaseCorporateStructureSource(**kwargs)


def _synthetic_payload() -> dict:
    """A different fictional company, same schema, different ids/percentages."""
    return {
        "schema_version": "2.0",
        "artifact_type": "llm_reproducible_corporate_structure_diagrams",
        "simulation_disclaimer": "SIMULATED EXAMPLE for a fictional company.",
        "visual_spec": {
            "canvas": {"width_px": 1000, "height_px": 700, "background_hex": "#FFFFFF"},
            "header": {"title": "Corporate Structure", "title_position": {"x_px": 20, "y_px": 20},
                       "title_font_size_px": 40, "title_color_hex": "#142340",
                       "subtitle_format": "{company_display_name} (Simulated)",
                       "subtitle_position": {"x_px": 20, "y_px": 70},
                       "subtitle_font_size_px": 20, "subtitle_color_hex": "#142340"},
            "colors_by_role": {"Parent Co": "#74150F", "Borrower": "#C25A13",
                               "OpCo": "#D99337", "Other": "#A7A7A7"},
            "node_style": {"corner_radius_px": 12, "name_font_size_px": 16,
                           "role_font_size_px": 14, "text_color_hex": "#FFFFFF",
                           "guarantor_shield": {"size_px": 24, "stroke_hex": "#000000"}},
            "ownership_links_style": {"line_color_hex": "#141414", "line_width_px": 3,
                                      "percentage_label": {"font_size_px": 16,
                                                           "font_color_hex": "#111111"}},
            "legend": {"x_px": 800, "y_px": 20, "width_px": 180, "height_px": 160,
                       "entries": [{"label": "Parent Co", "color_hex": "#74150F"},
                                   {"label": "Guarantor", "symbol": "shield"}]},
            "footer": {"text": "Illustrative only — simulated.", "x_px": 20,
                       "y_px": 670, "font_size_px": 14, "color_hex": "#6A6A6A"},
            "negative_requirements": ["No extra header disclaimer line."],
        },
        "companies": {
            "ACME": {
                "company_display_name": "Acme Fictional Group",
                "simulation_status": "Fictional; illustrative only.",
                "root_node_id": "acme_parent",
                "nodes": [
                    {"id": "acme_parent", "display_name": "Acme Parent Inc.",
                     "role": "Parent Co", "is_guarantor": False,
                     "box": {"center_x_px": 500, "top_y_px": 120, "width_px": 260, "height_px": 80}},
                    {"id": "acme_borrower", "display_name": "Acme Borrower LLC",
                     "role": "Borrower", "is_guarantor": True,
                     "box": {"center_x_px": 500, "top_y_px": 280, "width_px": 260, "height_px": 80}},
                    {"id": "acme_op1", "display_name": "Acme Operations One LLC",
                     "role": "OpCo", "is_guarantor": False,
                     "box": {"center_x_px": 350, "top_y_px": 460, "width_px": 200, "height_px": 90}},
                    {"id": "acme_op2", "display_name": "Acme Operations Two LLC",
                     "role": "OpCo", "is_guarantor": True,
                     "box": {"center_x_px": 650, "top_y_px": 460, "width_px": 200, "height_px": 90}},
                ],
                "ownership_links": [
                    {"parent_id": "acme_parent", "child_id": "acme_borrower", "ownership_percent": 100},
                    {"parent_id": "acme_borrower", "child_id": "acme_op1", "ownership_percent": 60},
                    {"parent_id": "acme_borrower", "child_id": "acme_op2", "ownership_percent": 100},
                ],
                "plain_text_tree": "Acme Parent Inc.\n  Acme Borrower LLC\n    Acme Operations One LLC\n    Acme Operations Two LLC",
                "layout_note": "two levels",
            }
        },
    }


# --- 7.1 Primary HCA test ----------------------------------------------------

def test_hca_structure_validates_counts_and_names():
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    assert art.status == "available"
    assert len(art.nodes) == 14
    assert len(art.links) == 13
    assert len(art.guarantor_node_ids) == 5
    payload = _hca_payload()
    expected_names = {n["id"]: n["display_name"]
                      for n in payload["companies"]["HCA"]["nodes"]}
    for node in art.nodes:
        assert node.display_name == expected_names[node.node_id]
    # Percentages attach exactly to the declared links, and only from links.
    link_pct = {(l.parent_id, l.child_id): l.ownership_percent for l in art.links}
    for raw in payload["companies"]["HCA"]["ownership_links"]:
        assert link_pct[(raw["parent_id"], raw["child_id"])] == raw["ownership_percent"]


def test_hca_render_guarantor_shields_only_where_specified():
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, _hca_payload()["visual_spec"])
    assert svg.count('data-guarantor="true"') == 5


def test_hca_render_colours_and_simulation_footnote_present():
    payload = _hca_payload()
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, payload["visual_spec"])
    # Role colours come from visual_spec.
    assert "#74150F" in svg  # Parent Co
    assert "#D99337" in svg  # OpCo
    # Simulation footnote retained.
    assert payload["visual_spec"]["footer"]["text"] in svg


def test_hca_removed_header_disclaimer_and_legend_note_do_not_reappear():
    payload = _hca_payload()
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, payload["visual_spec"])
    # No legend note beyond the listed entries, no extra header disclaimer line.
    assert "legend footnote" not in svg.casefold()
    assert "header disclaimer" not in svg.casefold()
    # Only the listed legend entries appear as labels.
    legend_labels = {e.get("label") for e in payload["visual_spec"]["legend"]["entries"]}
    for label in legend_labels:
        assert label in svg


def test_hca_regional_to_atlantic_connector_is_perfectly_vertical():
    payload = _hca_payload()
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, payload["visual_spec"])
    # hca_regional (center 145, bottom 747) -> hca_atlantic (center 145, top 860):
    # a single straight vertical line with x1 == x2 == 145 and no bus.
    verticals = [m.groups() for m in re.finditer(
        r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"', svg)]
    match = [v for v in verticals if v[0] == "145" and v[2] == "145"
             and float(v[1]) >= 747 and float(v[3]) <= 860]
    assert match, "expected a perfectly vertical single-child connector at x=145"
    for x1, _, x2, _ in match:
        assert x1 == x2


def test_hca_rendering_is_byte_stable():
    payload = _hca_payload()
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    first = render_corporate_structure_svg(art, payload["visual_spec"])
    second = render_corporate_structure_svg(
        build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE"),
        payload["visual_spec"])
    assert first == second


def test_hca_grouping_nodes_flagged_not_treated_as_legal_entities():
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    assert set(art.grouping_node_ids) == {"hca_other", "hca_jv"}
    assert any("grouping" in lim.casefold() for lim in art.limitations)


def test_hca_simulation_treatment_preserved():
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    assert art.is_simulated
    assert art.provenance.simulation_disclaimer
    assert any("simulated" in lim.casefold() for lim in art.limitations)
    # Guarantor markings carry an enforceability qualification in signals.
    guar = next(s for s in art.structural_signals
                if s.dimension == "illustrative_guarantor_coverage")
    assert any("enforceable" in q.casefold() for q in guar.qualifications)


# --- 7.2 Generalisation test -------------------------------------------------

def test_synthetic_company_parses_and_renders_without_code_change():
    payload = _synthetic_payload()
    src = CaseCorporateStructureSource(
        case_id="ACME_CASE", payload=payload, source_document_id="acme-json",
        registered_case_id="ACME_CASE")
    art = build_corporate_structure_artifact(src, expected_case_id="ACME_CASE")
    assert art.status == "available"
    assert len(art.nodes) == 4
    assert len(art.links) == 3
    assert art.guarantor_node_ids == ["acme_borrower", "acme_op2"]
    svg = render_corporate_structure_svg(art, payload["visual_spec"])
    assert svg.count('data-guarantor="true"') == 2
    assert "Acme Parent Inc." in svg
    # Byte-stable for the synthetic company too.
    assert svg == render_corporate_structure_svg(art, payload["visual_spec"])


# --- 7.3 No-JSON / rejection tests ------------------------------------------

def test_no_source_yields_mandated_unavailable_message():
    art = build_corporate_structure_artifact(None, expected_case_id="CASE")
    assert art.status == "unavailable"
    assert art.message == NO_JSON_MESSAGE
    assert art.nodes == [] and art.links == []


def test_source_not_associated_with_case_is_rejected():
    src = _hca_source(case_id="HCA_CASE", registered_case_id="OTHER_CASE")
    art = build_corporate_structure_artifact(src, expected_case_id="HCA_CASE")
    assert art.status == "rejected"
    assert art.message == NO_JSON_MESSAGE
    assert not art.nodes


def test_source_for_different_current_case_is_rejected():
    src = _hca_source(case_id="HCA_CASE", registered_case_id="HCA_CASE")
    art = build_corporate_structure_artifact(src, expected_case_id="DIFFERENT_CASE")
    assert art.status == "rejected"
    assert not art.nodes


def test_malformed_json_is_rejected():
    src = CaseCorporateStructureSource(
        case_id="C", payload={"schema_version": "2.0", "artifact_type": "x"},
        registered_case_id="C")
    art = build_corporate_structure_artifact(src, expected_case_id="C")
    assert art.status == "rejected"
    assert not art.nodes


def test_multi_company_file_is_rejected():
    payload = _hca_payload()
    payload["companies"]["EXTRA"] = payload["companies"]["HCA"]
    src = CaseCorporateStructureSource(case_id="C", payload=payload, registered_case_id="C")
    art = build_corporate_structure_artifact(src, expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.corp_structure_single_company"
               for i in art.validation_issues)


def test_out_of_range_percentage_is_rejected():
    payload = _hca_payload()
    payload["companies"]["HCA"]["ownership_links"][0]["ownership_percent"] = 150
    src = CaseCorporateStructureSource(case_id="C", payload=payload, registered_case_id="C")
    art = build_corporate_structure_artifact(src, expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.ownership_percentage_bounds"
               for i in art.validation_issues)


def test_invalid_node_reference_is_rejected():
    payload = _hca_payload()
    payload["companies"]["HCA"]["ownership_links"][0]["child_id"] = "does_not_exist"
    src = CaseCorporateStructureSource(case_id="C", payload=payload, registered_case_id="C")
    art = build_corporate_structure_artifact(src, expected_case_id="C")
    assert art.status == "rejected"


def test_cycle_is_rejected():
    payload = _synthetic_payload()
    # Introduce a cycle: op1 -> parent.
    payload["companies"]["ACME"]["ownership_links"].append(
        {"parent_id": "acme_op1", "child_id": "acme_parent", "ownership_percent": 100})
    src = CaseCorporateStructureSource(case_id="C", payload=payload, registered_case_id="C")
    art = build_corporate_structure_artifact(src, expected_case_id="C")
    assert art.status == "rejected"


def test_unsupported_schema_version_is_rejected():
    payload = _hca_payload()
    payload["schema_version"] = "9.9"
    src = CaseCorporateStructureSource(case_id="C", payload=payload, registered_case_id="C")
    art = build_corporate_structure_artifact(src, expected_case_id="C")
    assert art.status == "rejected"


# --- Output assembly: partial completion when JSON absent --------------------

def _governed_m3_stub(with_management=True):
    return {
        "borrower_identification": {"status": "unavailable", "entity_id": None,
                                    "evidence_ids": [], "limitation": "SO to confirm."},
        "management_assessments": ([{
            "observation_id": "MO1", "management_id": "m1", "legal_name": "A. Doe",
            "role": "CEO", "dimension": "competence", "credit_direction": "neutral",
            "disposition": "qualified", "admissibility": "qualified_narrative",
        }] if with_management else []),
        "governance_screening": [],
        "information_gaps": ["Completeness not established."],
    }


def test_output_section_a_unavailable_but_b_assessed_is_partial():
    art = build_corporate_structure_artifact(None, expected_case_id="CASE")
    output = assemble_m3_output(structure=art, governed_m3=_governed_m3_stub())
    assert output["section_a_corporate_structure"]["corporate_structure_status"] == "unavailable"
    assert output["section_a_corporate_structure"]["display_message"] == NO_JSON_MESSAGE
    assert output["section_b_management_commentary"]["assessment_status"] == "assessed"
    assert output["completion"]["status"] == "partial"
    assert output["section_c_conclusion"]["forced_outcome"] is False


def test_output_both_available_is_complete():
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, _hca_payload()["visual_spec"])
    output = assemble_m3_output(structure=art, governed_m3=_governed_m3_stub(),
                               structure_svg=svg)
    assert output["section_a_corporate_structure"]["corporate_structure_status"] == "available"
    assert output["section_a_corporate_structure"]["diagram"]["node_count"] == 14
    assert output["completion"]["status"] == "complete"


def test_senior_management_proceeds_when_structure_absent():
    art = build_corporate_structure_artifact(None, expected_case_id="CASE")
    output = assemble_m3_output(structure=art, governed_m3=_governed_m3_stub())
    # Management signals survive even though the diagram is withheld.
    assert output["section_b_management_commentary"]["management_signals"]


def test_structural_signals_keep_direction_and_availability_separate():
    art = build_corporate_structure_artifact(_hca_source(), expected_case_id="HCA_CASE")
    dists = [s for s in art.structural_signals
             if s.dimension == "subsidiary_distribution_dependency"]
    assert dists and dists[0].assessment_availability == "insufficient_evidence"
    # Insufficient evidence is distinct from a neutral-but-sufficient signal.
    complexity = next(s for s in art.structural_signals
                      if s.dimension == "structural_complexity")
    assert complexity.assessment_availability == "sufficient"
    assert complexity.credit_direction.value == "neutral"
