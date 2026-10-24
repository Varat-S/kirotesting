"""Unit coverage of the compact corporate-structure context builder.

``build_structural_facts`` turns a validated :class:`CorporateStructureArtifact`
into the read-only, provenance-preserving representation injected into the
Module 3 specialist's input context. An unavailable artifact yields ``None`` so
no invented structural facts ever enter the context.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.bo.completion import (
    admit_corporate_structure_source,
    build_m3_corporate_structure,
)
from app.services.bo.m3_structure_context import build_structural_facts


SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")


def _hca_artifact():
    doc = json.loads(SIMPLIFIED.read_text(encoding="utf-8"))
    src = admit_corporate_structure_source(
        doc, case_id="CASE", source_document_id="corp-onboarding-1")
    agents = [{"agent_id": "m3_corporate_management", "code": "M3",
               "status": "validated", "coverage": [],
               "calls": [{"inputs": {"corporate_structure_source": src}}]}]
    artifact, _ = build_m3_corporate_structure(agents, case_id="CASE")
    return artifact


def test_build_structural_facts_exposes_stable_ids_and_observations():
    facts = build_structural_facts(_hca_artifact())
    assert facts is not None

    # Borrower, node ids, link ids and classifications are present and stable.
    assert facts["borrower"] == {"node_id": "hca_borrower", "role": "Borrower"}
    node_ids = {n["node_id"] for n in facts["nodes"]}
    assert {"hca_top", "hca_borrower", "hca_jv"} <= node_ids
    link_ids = {l["link_id"] for l in facts["links"]}
    assert "link:hca_intermediate->hca_borrower" in link_ids
    # Guarantor + grouping classifications carried through.
    assert set(facts["guarantor_node_ids"]) == {
        "hca_regional", "hca_hosp_ops", "hca_atlantic", "hca_east", "hca_west"}
    assert set(facts["grouping_node_ids"]) == {"hca_other", "hca_jv"}
    # Source provenance (doc id + hash + simulation status) is preserved.
    assert facts["source"]["source_document_id"] == "corp-onboarding-1"
    assert facts["source"]["document_hash"]
    assert facts["source"]["is_simulated"] is True
    # Deterministic observations are included (the facts the LLM must reference,
    # not re-derive).
    assert facts["deterministic_observations"]
    dims = {o["dimension"] for o in facts["deterministic_observations"]}
    assert "borrower_position" in dims
    # Partial-ownership link values are preserved exactly.
    by_link = {l["link_id"]: l["ownership_percent"] for l in facts["links"]}
    assert by_link["link:hca_other->hca_jv"] == 35.0


def test_build_structural_facts_none_for_unavailable_artifact():
    # No case-associated source -> unavailable artifact -> None (no structure).
    artifact, _ = build_m3_corporate_structure([], case_id="CASE")
    assert artifact.status != "available"
    assert build_structural_facts(artifact) is None
    assert build_structural_facts(None) is None
