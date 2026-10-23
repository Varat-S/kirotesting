"""Offline tests for the Module 3 V6 simplified onboarding -> refined transform.

Covers:
* the simplified v1.0 onboarding parser + Validator-7 structural checks;
* the deterministic v1.0 -> v2.0 transform and round-trip reconciliation;
* the primary HCA transformation vs the supplied reference final output;
* generalisation to a different fictional company with no code change;
* failure handling (malformed, duplicate ids, invalid %, invalid guarantor
  flag, contradictory / cyclic relationships, missing mandatory fields);
* the two Architecture V6 deliverables (m3_corporate_structure / m3_analysis).

HCA is the development fixture only. No live model or network call is made.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.core.hashing import canonical_json
from app.schemas.bo_m3_structure import NO_JSON_MESSAGE
from app.services.bo.corporate_structure import (
    CaseCorporateStructureSource,
    build_corporate_structure_artifact,
)
from app.services.bo.corporate_structure_onboarding import (
    build_onboarding_graph,
    reconcile_roundtrip,
    transform_to_refined_v2,
)
from app.services.bo.corporate_structure_render import render_corporate_structure_svg
from app.services.bo.m3_artifacts import build_m3_deliverables
from app.services.bo.m3_output import assemble_m3_output


SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")
REFERENCE = Path("examples/bo_m3/Sample HCA Corp Structure Final Output.json")


def _simplified() -> dict:
    return json.loads(SIMPLIFIED.read_text())


def _reference() -> dict:
    return json.loads(REFERENCE.read_text())


def _source(payload=None, *, case_id="HCA_CASE", registered="HCA_CASE", **kw):
    return CaseCorporateStructureSource(
        case_id=case_id, payload=payload if payload is not None else _simplified(),
        source_document_id="corp-onboarding-1", document_hash="h1",
        registered_case_id=registered, **kw)


# --- Mandatory file-access verification (fixtures readable + correct kinds) ---

def test_reference_fixtures_have_expected_kinds():
    simp = _simplified()
    assert simp["schema_version"] == "1.0"
    assert simp["document_type"] == "corporate_structure_onboarding"
    assert "corporate_tree" in simp and "nodes" not in simp
    ref = _reference()
    assert ref["schema_version"] == "2.0"
    assert ref["artifact_type"] == "llm_reproducible_corporate_structure_diagrams"
    assert "companies" in ref and "corporate_tree" not in ref


# --- Primary HCA transformation ---------------------------------------------

def test_hca_onboarding_transforms_to_valid_v2_with_exact_facts():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    assert art.status == "available"
    assert len(art.nodes) == 14
    assert len(art.links) == 13
    assert len(art.guarantor_node_ids) == 5
    # Guarantor set matches the input exactly.
    assert set(art.guarantor_node_ids) == {
        "hca_regional", "hca_atlantic", "hca_hosp_ops", "hca_east", "hca_west"}
    # Aggregate grouping nodes flagged, not treated as legal persons.
    assert set(art.grouping_node_ids) == {"hca_other", "hca_jv"}


def test_hca_transform_matches_reference_structural_facts():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    refined = art.refined_structure
    company = next(iter(refined["companies"].values()))
    ref_company = _reference()["companies"]["HCA"]

    gen_nodes = {n["id"]: n for n in company["nodes"]}
    ref_nodes = {n["id"]: n for n in ref_company["nodes"]}
    assert set(gen_nodes) == set(ref_nodes)
    for nid, ref_node in ref_nodes.items():
        assert gen_nodes[nid]["display_name"] == ref_node["display_name"]
        assert gen_nodes[nid]["role"] == ref_node["role"]
        assert gen_nodes[nid]["is_guarantor"] == ref_node["is_guarantor"]

    gen_links = {(l["parent_id"], l["child_id"]): l["ownership_percent"]
                 for l in company["ownership_links"]}
    ref_links = {(l["parent_id"], l["child_id"]): l["ownership_percent"]
                 for l in ref_company["ownership_links"]}
    assert gen_links == ref_links
    assert company["root_node_id"] == ref_company["root_node_id"]
    # The plain-text tree reproduces the reference exactly (same ordering + shields).
    assert company["plain_text_tree"] == ref_company["plain_text_tree"]


def test_hca_transform_generates_presentation_specs():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    refined = art.refined_structure
    spec = refined["visual_spec"]
    # Styling, connector instructions, legend present.
    assert spec["canvas"]["width_px"] == 1440
    assert spec["colors_by_role"]["Parent Co"] == "#74150F"
    assert spec["node_style"]["guarantor_shield"]["display_when"]
    assert spec["ownership_links_style"]["single_child_rule"]
    assert spec["ownership_links_style"]["bus_exception_rule"]
    assert spec["legend"]["entries"]
    assert spec["negative_requirements"]
    # Simulation footnote / disclaimer retained for a simulated source.
    assert "simulation_disclaimer" in refined
    assert spec["footer"]["text"]


def test_hca_transform_is_byte_deterministic():
    a = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    b = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    assert canonical_json(a.refined_structure) == canonical_json(b.refined_structure)


def test_hca_transform_roundtrip_reconciles_exactly():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    rt = art.roundtrip_reconciliation
    assert rt is not None and rt["consistent"] is True
    assert rt["discrepancies"] == []
    assert rt["input_entity_count"] == 14 == rt["output_entity_count"]
    assert rt["input_link_count"] == 13 == rt["output_link_count"]
    assert rt["input_guarantor_ids"] == rt["output_guarantor_ids"]


def test_hca_transformed_structure_renders_via_existing_renderer():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, art.refined_structure["visual_spec"])
    assert svg.count('data-guarantor="true"') == 5
    assert "#74150F" in svg  # Parent Co colour from the template


def test_single_child_aligned_below_parent_no_bend():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    boxes = {n["id"]: n["box"]
             for n in next(iter(art.refined_structure["companies"].values()))["nodes"]}
    # Chain of single-child relationships shares one centreline.
    assert boxes["hca_top"]["center_x_px"] == boxes["hca_intermediate"]["center_x_px"]
    assert boxes["hca_intermediate"]["center_x_px"] == boxes["hca_borrower"]["center_x_px"]
    # hca_regional has a single child hca_atlantic -> same centre.
    assert boxes["hca_regional"]["center_x_px"] == boxes["hca_atlantic"]["center_x_px"]


def test_layout_fits_canvas_and_does_not_overlap():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    nodes = next(iter(art.refined_structure["companies"].values()))["nodes"]
    canvas_w = art.refined_structure["visual_spec"]["canvas"]["width_px"]
    for n in nodes:
        box = n["box"]
        left = box["center_x_px"] - box["width_px"] / 2
        right = box["center_x_px"] + box["width_px"] / 2
        assert left >= 0 and right <= canvas_w
    # Siblings on the same row (same top_y) never overlap horizontally.
    by_row: dict[float, list[dict]] = {}
    for n in nodes:
        by_row.setdefault(n["box"]["top_y_px"], []).append(n["box"])
    for row in by_row.values():
        spans = sorted((b["center_x_px"] - b["width_px"] / 2,
                        b["center_x_px"] + b["width_px"] / 2) for b in row)
        for i in range(len(spans) - 1):
            assert spans[i][1] <= spans[i + 1][0] + 1e-6


def test_aggregate_groups_not_legal_persons_in_deliverable():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    assert any("grouping" in lim.casefold() for lim in art.limitations)


# --- Generalisation: a different fictional company, no code change ----------

def _synthetic_onboarding() -> dict:
    return {
        "schema_version": "1.0",
        "document_type": "corporate_structure_onboarding",
        "company_name": "Nimbus Logistics Group",
        "data_status": "simulated_educational_example",
        "corporate_tree": {
            "id": "nim_top", "name": "Nimbus Holdings Ltd", "role": "Parent Co",
            "is_guarantor": False,
            "children": [
                {"id": "nim_borrower", "name": "Nimbus Opco Ltd", "role": "Borrower",
                 "is_guarantor": True, "ownership_percent_from_parent": 100,
                 "children": [
                     {"id": "nim_a", "name": "Nimbus Freight A Ltd", "role": "OpCo",
                      "is_guarantor": False, "ownership_percent_from_parent": 70},
                     {"id": "nim_b", "name": "Nimbus Freight B Ltd", "role": "OpCo",
                      "is_guarantor": True, "ownership_percent_from_parent": 100},
                 ]},
            ],
        },
    }


def test_synthetic_company_transforms_without_company_specific_code():
    src = CaseCorporateStructureSource(
        case_id="NIM", payload=_synthetic_onboarding(), registered_case_id="NIM")
    art = build_corporate_structure_artifact(src, expected_case_id="NIM")
    assert art.status == "available"
    assert len(art.nodes) == 4
    assert len(art.links) == 3
    assert set(art.guarantor_node_ids) == {"nim_borrower", "nim_b"}
    assert art.roundtrip_reconciliation["consistent"]
    # Company-agnostic key derived from display name, not hardcoded.
    key = next(iter(art.refined_structure["companies"]))
    assert key == "Nimbus_Logistics_Group"
    svg = render_corporate_structure_svg(art, art.refined_structure["visual_spec"])
    assert "Nimbus Holdings Ltd" in svg
    assert svg.count('data-guarantor="true"') == 2


def test_graph_builder_preserves_sibling_order():
    graph, issues = build_onboarding_graph(_simplified())
    assert graph is not None and not [i for i in issues if i["action"] == "rejected"]
    children = graph.children_of("hca_borrower")
    assert [c.node_id for c in children] == [
        "hca_regional", "hca_hosp_ops", "hca_shared", "hca_other"]


# --- Failure handling --------------------------------------------------------

def test_no_source_yields_mandated_unavailable():
    art = build_corporate_structure_artifact(None, expected_case_id="HCA_CASE")
    assert art.status == "unavailable"
    assert art.message == NO_JSON_MESSAGE
    assert not art.nodes and art.refined_structure is None


def test_wrong_case_association_rejected():
    art = build_corporate_structure_artifact(
        _source(registered="OTHER"), expected_case_id="HCA_CASE")
    assert art.status == "rejected"
    assert art.message == NO_JSON_MESSAGE
    assert not art.nodes


def test_cross_case_leakage_rejected():
    art = build_corporate_structure_artifact(
        _source(case_id="HCA_CASE", registered="HCA_CASE"),
        expected_case_id="DIFFERENT")
    assert art.status == "rejected"
    assert not art.nodes


def test_malformed_onboarding_rejected():
    bad = {"schema_version": "1.0", "document_type": "corporate_structure_onboarding"}
    art = build_corporate_structure_artifact(
        _source(payload=bad, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.onboarding_company_name"
               or i["rule_id"] == "m3.onboarding_corporate_tree"
               for i in art.validation_issues)


def test_duplicate_entity_id_rejected():
    payload = _simplified()
    # Duplicate the borrower id onto a descendant.
    payload["corporate_tree"]["children"][0]["children"][0]["id"] = "hca_top"
    art = build_corporate_structure_artifact(
        _source(payload=payload, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.onboarding_node_id_unique"
               for i in art.validation_issues)


def test_out_of_range_ownership_rejected():
    payload = _simplified()
    payload["corporate_tree"]["children"][0]["ownership_percent_from_parent"] = 150
    art = build_corporate_structure_artifact(
        _source(payload=payload, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.onboarding_ownership_bounds"
               for i in art.validation_issues)


def test_invalid_guarantor_flag_rejected():
    payload = _simplified()
    payload["corporate_tree"]["children"][0]["is_guarantor"] = "yes"
    art = build_corporate_structure_artifact(
        _source(payload=payload, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.onboarding_guarantor_flag"
               for i in art.validation_issues)


def test_missing_mandatory_role_rejected():
    payload = _simplified()
    del payload["corporate_tree"]["children"][0]["role"]
    art = build_corporate_structure_artifact(
        _source(payload=payload, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.onboarding_node_role"
               for i in art.validation_issues)


def test_missing_ownership_on_nonroot_rejected():
    payload = _simplified()
    del payload["corporate_tree"]["children"][0]["ownership_percent_from_parent"]
    art = build_corporate_structure_artifact(
        _source(payload=payload, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] == "m3.onboarding_ownership_present"
               for i in art.validation_issues)


def test_cycle_rejected():
    # A node referencing an ancestor id creates a duplicate/cyclic reference.
    payload = {
        "schema_version": "1.0",
        "document_type": "corporate_structure_onboarding",
        "company_name": "Cyclic Co",
        "data_status": "simulated_educational_example",
        "corporate_tree": {
            "id": "root", "name": "Root", "role": "Parent Co", "is_guarantor": False,
            "children": [
                {"id": "a", "name": "A", "role": "OpCo", "is_guarantor": False,
                 "ownership_percent_from_parent": 100,
                 "children": [
                     {"id": "root", "name": "Root again", "role": "OpCo",
                      "is_guarantor": False, "ownership_percent_from_parent": 100},
                 ]},
            ],
        },
    }
    art = build_corporate_structure_artifact(
        _source(payload=payload, case_id="C", registered="C"), expected_case_id="C")
    assert art.status == "rejected"
    assert any(i["rule_id"] in {"m3.onboarding_acyclic", "m3.onboarding_node_id_unique"}
               for i in art.validation_issues)


def test_v2_source_still_accepted_backward_compatible():
    # A legacy rich v2.0 document must still validate directly (no transform).
    v2 = json.loads(Path("examples/bo_m3/Refined HCA Corp Structure.json").read_text())
    src = CaseCorporateStructureSource(
        case_id="HCA_CASE", payload=v2, registered_case_id="HCA_CASE")
    art = build_corporate_structure_artifact(src, expected_case_id="HCA_CASE")
    assert art.status == "available"
    assert len(art.nodes) == 14
    # No transform ran, so no onboarding provenance.
    assert art.provenance.transform_version is None
    assert art.roundtrip_reconciliation is None


# --- Two-output deliverables -------------------------------------------------

def _governed_stub(with_management=True):
    return {
        "borrower_identification": {"status": "unavailable", "entity_id": None,
                                    "evidence_ids": [], "limitation": "SO to confirm."},
        "management_assessments": ([{
            "observation_id": "MO1", "credit_direction": "neutral",
            "disposition": "qualified"}] if with_management else []),
        "governance_screening": [],
        "information_gaps": ["Completeness not established."],
        "validation_issues": [], "rejected_items": [],
    }


def test_deliverables_structure_and_analysis_linked_when_available():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    svg = render_corporate_structure_svg(art, art.refined_structure["visual_spec"])
    m3_output = assemble_m3_output(structure=art, governed_m3=_governed_stub(),
                                   structure_svg=svg)
    deliv = build_m3_deliverables(art, _governed_stub(), m3_output)
    a = deliv["m3_corporate_structure"]
    b = deliv["m3_analysis"]
    assert a is not None
    assert a["artifact_kind"] == "m3_corporate_structure"
    assert a["refined_structure"]["schema_version"] == "2.0"
    assert a["roundtrip_reconciliation"]["consistent"]
    # Deliverable B references A by stable id and carries management + conclusion.
    assert b["corporate_structure_reference"]["artifact_id"] == a["artifact_id"]
    assert b["senior_management_assessment"]["management_signals"]
    assert b["consolidated_conclusion"]["forced_outcome"] is False
    assert b["requires_human_review"] is True


def test_deliverable_a_absent_when_structure_unavailable_b_still_emitted():
    art = build_corporate_structure_artifact(None, expected_case_id="HCA_CASE")
    m3_output = assemble_m3_output(structure=art, governed_m3=_governed_stub())
    deliv = build_m3_deliverables(art, _governed_stub(), m3_output)
    # Fail-closed: no corporate-structure deliverable is fabricated.
    assert deliv["m3_corporate_structure"] is None
    b = deliv["m3_analysis"]
    assert b["corporate_structure_status"] == "unavailable"
    assert b["corporate_structure_reference"]["unavailable_message"] == NO_JSON_MESSAGE
    # Senior-management assessment still proceeds.
    assert b["senior_management_assessment"]["assessment_status"] == "assessed"


def test_deliverable_b_keeps_direction_and_verification_separate():
    art = build_corporate_structure_artifact(_source(), expected_case_id="HCA_CASE")
    m3_output = assemble_m3_output(structure=art, governed_m3=_governed_stub())
    deliv = build_m3_deliverables(art, _governed_stub(), m3_output)
    b = deliv["m3_analysis"]
    # Insufficient-evidence structural signal stays distinct from neutral.
    dists = [o for o in b["structural_observations"]
             if o["dimension"] == "subsidiary_distribution_dependency"]
    assert dists and dists[0]["assessment_availability"] == "insufficient_evidence"
