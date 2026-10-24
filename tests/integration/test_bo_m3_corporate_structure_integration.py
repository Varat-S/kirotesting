"""Integration of the Module 3 corporate-structure deliverable with BO completion.

Exercises the completion seam (``build_m3_corporate_structure`` and
``_module_conclusions``) that threads a case-associated corporate-structure JSON
into the Module 3 three-section output. Offline only; no model or network call.

These tests assert the mandatory onboarding behaviour:
* the HCA fixture sitting in ``examples/`` is NOT auto-loaded;
* a 10-K / SEC Exhibit 21 does NOT trigger a replacement diagram;
* only an explicitly case-associated JSON yields a diagram;
* the module is marked PARTIAL (not silently complete) when the structure is
  absent but the management assessment proceeds.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.schemas.bo import (
    Admissibility,
    ClaimType,
    Direction,
    Disposition,
    HypothesisRecord,
)
from app.services.bo.completion import (
    _module_conclusions,
    build_m3_corporate_structure,
)


FIXTURE = Path("examples/bo_m3/Refined HCA Corp Structure.json")


def _hca_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _m3_agent(calls=None):
    return {"agent_id": "m3_corporate_management", "code": "M3",
            "status": "validated", "coverage": [], "calls": calls or []}


def _structure_call(payload, *, registered_case_id, case_id="HCA_CASE"):
    return {"inputs": {"corporate_structure_source": {
        "case_id": case_id, "payload": payload,
        "source_document_id": "corp-json-1", "document_hash": "h1",
        "registered_case_id": registered_case_id}}}


def test_case_associated_json_produces_diagram_through_completion():
    agents = [_m3_agent([_structure_call(_hca_payload(), registered_case_id="HCA_CASE")])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "available"
    assert len(art.nodes) == 14
    assert svg and 'data-guarantor="true"' in svg


def test_fixture_in_examples_is_not_auto_loaded_without_case_association():
    # No corporate_structure_source on the agent call -> no diagram, even though
    # the fixture exists in examples/.
    agents = [_m3_agent(calls=[{"inputs": {}}])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "unavailable"
    assert svg is None
    assert not art.nodes


def test_tenk_and_exhibit21_do_not_trigger_a_replacement_diagram():
    # A call that only carries narrative 10-K / Exhibit 21 evidence, no corp JSON.
    call = {"inputs": {"evidence": [
        {"evidence_id": "ex21", "text": "Exhibit 21 — List of Significant Subsidiaries"},
        {"evidence_id": "mdna", "text": "Item 7 MD&A narrative."}]}}
    agents = [_m3_agent(calls=[call])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "unavailable"
    assert svg is None


def test_wrong_case_association_withholds_diagram_through_completion():
    agents = [_m3_agent([_structure_call(_hca_payload(), registered_case_id="OTHER")])]
    art, svg = build_m3_corporate_structure(agents, case_id="HCA_CASE")
    assert art.status == "rejected"
    assert svg is None


def _hypotheses_stub():
    return {}


def _usable_m3_hypothesis():
    return HypothesisRecord(
        hypothesis_id="M3-H1", analysis_run_id="ar1",
        agent_id="m3_corporate_management", agent_run_id="run1", section="BO4",
        claim="Governance disclosures are consistent with reported controls.",
        claim_type=ClaimType.INFERENCE, direction=Direction.NEUTRAL,
        supporting_evidence_ids=[], disposition=Disposition.QUALIFIED,
        admissibility=Admissibility.QUALIFIED_NARRATIVE)


def test_module_conclusions_partial_when_structure_absent_but_b_present():
    governed = {
        "borrower_identification": {"status": "unavailable", "limitation": "SO confirms."},
        "management_assessments": [{"observation_id": "MO1", "credit_direction": "neutral"}],
        "governance_screening": [],
        "information_gaps": ["gap"],
    }
    agents = [{"agent_id": "m3_corporate_management", "code": "M3",
               "status": "validated", "coverage": [], "calls": [{"inputs": {}}]}]
    hyps = {"M3-H1": _usable_m3_hypothesis()}
    out = _module_conclusions(agents, hyps, governed, case_id="HCA_CASE")
    m3 = out["M3"]
    assert m3["corporate_structure_status"] == "unavailable"
    assert m3["m3_output"]["completion"]["status"] == "partial"
    # Not silently marked fully complete.
    assert m3["status"] == "partial"
    assert m3["m3_output"]["section_b_management_commentary"]["assessment_status"] == "assessed"


def test_module_conclusions_without_case_id_is_unavailable_not_crash():
    agents = [{"agent_id": "m3_corporate_management", "code": "M3",
               "status": "validated", "coverage": [], "calls": []}]
    out = _module_conclusions(agents, {}, None, case_id=None)
    assert out["M3"]["corporate_structure_status"] == "unavailable"
    assert out["M3"]["m3_output"]["section_a_corporate_structure"][
        "corporate_structure_status"] == "unavailable"
