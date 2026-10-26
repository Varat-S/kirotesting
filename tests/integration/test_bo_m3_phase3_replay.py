"""M3 Phase 3: private real-response regressions and portable positive controls.

The genuine fixture remains in its ignored, read-only local database. Tests
using it skip when absent; authored synthetic positives run in every checkout.
No provider backend or network is used.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from app.models.base import create_engine_and_session
from app.schemas.agentic import EvidencePacket
from app.schemas.bo import Disposition
from app.schemas.snapshots import CanonicalEvidenceSnapshot
from app.services.agents.registry import default_registry
from app.services.bo.comparison import prepare_specialist
from app.services.bo.indicators import IndicatorSet
from app.services.bo.m3_diagnostics import load_recorded_m3_run
from app.services.bo.m3_grounding import strengthen_m3_assessment
from app.services.bo.m3_selection import select_m3_passages
from app.services.bo.m3_standalone import run_m3
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.bo.retrieval import RetrievalBudget
from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
from app.services.bo.validation_audit import validate_specialist_output
from app.services.pipeline.artifacts import verified_artifacts
from tests import bo_helpers as h
from tests.integration.test_bo_m3_standalone import _single_pass


PRIVATE_DB = Path("data/m3_phase2_verification_20261026_02.db")
MODEL_RUN = "937f882f-a9b6-480f-803d-ef972c677431"


@pytest.fixture
def recorded():
    if not PRIVATE_DB.exists():
        pytest.skip("Private Phase 2 recorded Gemini fixture is absent; no model call will be substituted.")
    before = hashlib.sha256(PRIVATE_DB.read_bytes()).hexdigest()
    engine, factory = create_engine_and_session("sqlite:///file:" + str(PRIVATE_DB.resolve()) + "?mode=ro&uri=true")
    with factory() as session:
        bundle = load_recorded_m3_run(session, MODEL_RUN)
    engine.dispose()
    with sqlite3.connect("file:" + str(PRIVATE_DB) + "?mode=ro", uri=True) as connection:
        packet = EvidencePacket.model_validate(json.loads(connection.execute("select payload from evidence_packets").fetchone()[0]))
        evidence = CanonicalEvidenceSnapshot.model_validate(json.loads(connection.execute(
            "select payload from snapshots where snapshot_type='canonical_evidence'").fetchone()[0]))
    request = bundle["recorded_request"]
    yield {"raw": json.loads(bundle["raw_response"]), "bundle": bundle, "packet": packet,
           "evidence": evidence, "texts": {e["evidence_id"]: e["text"] for e in request["inputs"]["evidence"]}}
    assert hashlib.sha256(PRIVATE_DB.read_bytes()).hexdigest() == before


def test_original_resolving_citations_no_longer_allow_four_unsupported_ok_values(recorded):
    raw = recorded["raw"]
    before = deepcopy(raw)
    result = validate_specialist_output(
        V3_SPEC_BY_CODE["M3"], default_registry("v3").get("m3_corporate_management"),
        recorded["packet"], raw, indicators=IndicatorSet(), passage_text=recorded["texts"],
        corpus=recorded["evidence"].narrative_evidence,
        value_sets=recorded["bundle"]["recorded_request"]["inputs"]["parameter_value_sets"],
        analysis_run_id="phase3-offline-only", agent_run_id="phase3-offline-only:single",
        prompt_id="m3-phase3-offline-regression")
    assert result.valid is True
    assert len(result.parameters) == 5
    assert all(p.status.value == "unavailable" and p.value is None for p in result.parameters)
    reasons = {c["parameter_id"]: c["reason"] for c in result.parameter_changes}
    assert "dependency_grade" in reasons["key_person_dependency_assessment"]
    assert "broad_dated_review" in reasons["governance_concern_assessment"]
    assert "received_or_existing_inquiry" in reasons["litigation_risk_assessment"]
    assert "broad_dated_review" in reasons["material_event_assessment"]
    assert raw == before  # proposals and original historical statuses stay intact


def test_recorded_unknown_entities_and_missing_structural_contract_stay_conservative(recorded):
    raw = recorded["raw"]["m3_assessment"]
    before = deepcopy(raw)
    facts = recorded["bundle"]["recorded_request"]["inputs"]["corporate_structure"]
    governed = validate_m3_assessment(raw, allowed_evidence_ids=set(recorded["texts"]),
                                     evidence_texts=recorded["texts"], structure_facts=facts)
    strengthened, reviews = strengthen_m3_assessment(
        governed, evidence_texts=recorded["texts"], structure_facts=facts)
    reasons = {i["reason"] for i in strengthened.rejected_items}
    assert {"relationship_references_unknown_entity", "unknown_entity"} <= reasons
    assert len(strengthened.legal_entities) == 2  # no missing endpoint is invented
    assert all(e.verification_status == "unverified" for e in strengthened.legal_entities)
    assert strengthened.cash_flow_locations[0].provenance_status == "unverified"
    assert strengthened.structural_credit_findings[0].analytical_dimension is None
    assert strengthened.structural_credit_findings[0].referenced_node_ids == []
    assert "m3.structural_support_contract" in {i.rule_id for i in strengthened.validation_issues}
    assert len(reviews) == 3
    assert all(r["application_search_performed"] for r in reviews)
    assert all(r["model_counterevidence_review_claim"] is False for r in reviews)
    assert any("succession plans may not" in sentence for r in reviews
               for sentences in r["matched_caveats"].values() for sentence in sentences)
    assert raw == before


def test_actual_item9a_management_and_auditor_passages_fit_existing_budget(recorded, db_session):
    evidence = recorded["evidence"]
    prep = prepare_specialist(db_session, case_id=evidence.case_id, evidence=evidence, metrics={},
                              spec=V3_SPEC_BY_CODE["M3"], sector="auto")
    controls = [p for p in prep.passages if "9A" in (p.get("section") or "")]
    assert len(controls) >= 2
    assert any("Opinion on Internal Control Over Financial Reporting" in p["text"] for p in controls)
    assert any("management concluded" in p["text"].lower() for p in controls)
    assert len(prep.passages) <= 16
    assert sum(len(p["text"]) for p in prep.passages) <= 36_000
    assert any("Information about our Executive Officers" in p["text"] for p in prep.passages)
    assert prep.m3_selection_summary["selected_passages"] == len(prep.passages)


def test_small_budget_is_enforced_and_gaps_are_explicit(db_session):
    prep = prepare_specialist(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(), metrics=h.metrics(),
                              spec=V3_SPEC_BY_CODE["M3"], budget=RetrievalBudget(
                                  max_passages_per_agent=2, max_chars_per_agent=800))
    assert len(prep.passages) <= 2
    assert sum(len(p["text"]) for p in prep.passages) <= 800
    assert prep.m3_selection_summary["queries_without_selected_results"]


def test_control_selection_is_portable_and_does_not_invent_unretrieved_results():
    from types import SimpleNamespace
    from app.services.bo.retrieval import RetrievalResult

    corpus = [{"evidence_id": eid, "text": text, "location": i} for i, (eid, text) in enumerate([
        ("definition", "Internal control is a process."),
        ("auditor", "Opinion on Internal Control Over Financial Reporting: effective."),
        ("management", "Management concluded that financial reporting controls were effective."),
        ("risk", "Managers may depart; succession plans may not succeed."),
        ("unretrieved", "This text was not returned by a search."),
    ])]
    attempts = [SimpleNamespace(query_id=name, purpose=purpose,
                               results=[{"evidence_id": eid} for eid in ids])
                for name, purpose, ids in [
                    ("m3.management_limits", "contradict", ["definition", "auditor", "management"]),
                    ("m3.management", "support", ["risk"])]]
    retrieval = RetrievalResult(attempts=attempts)
    summary = select_m3_passages(retrieval, corpus, RetrievalBudget(max_passages_per_agent=3))
    assert {p["evidence_id"] for p in retrieval.passages} == {"auditor", "management", "risk"}
    assert not summary["queries_without_selected_results"]


def _management_run(db_session, *, claim, mechanism, extra_passages, counter_ids=(), disposition="supported",
                    basis="outcome_evidence"):
    response = _single_pass()
    observation = response["m3_assessment"]["management_assessments"][0]
    observation.update(hypothesis=claim, credit_mechanism=mechanism,
                       supporting_evidence_ids=[{"$evidence": "Independent review"}],
                       contradicting_evidence_ids=list(counter_ids),
                       contradictory_evidence_investigated=True, disposition=disposition,
                       admissibility="qualified_narrative" if disposition == "qualified" else "established",
                       evidence_basis=basis, attribution="independently_observed")
    return run_m3(db_session, case_id=h.CASE,
                  evidence=h.evidence_snapshot({**h.PASSAGES, **extra_passages}), metrics=h.metrics(),
                  m3_responses={"single_pass": response})


def test_claimed_counterevidence_flag_cannot_hide_available_succession_caveat(db_session):
    result = _management_run(
        db_session, claim="Succession plans reduce transition risk.",
        mechanism="Leadership continuity reduces operating disruption and preserves cash generation.",
        basis="reported_experience", extra_passages={
            "support": ("Item 10", "Independent review reports that the company has adopted succession plans."),
            "adverse": ("Item 1A", "Our succession plans may not result in a successful transition.")})
    observation = result.governed_assessment["management_assessments"][0]
    assert observation["contradictory_evidence_investigated"] is True  # not changed to manufacture review
    assert observation["disposition"] == "unverified"
    assert observation["admissibility"] == "audit_only"
    assert "m3.claim_specific_counterevidence" in {i["rule_id"] for i in result.validation_issues}
    stage = next(s for s in verified_artifacts(db_session, h.CASE, h.evidence_snapshot().snapshot_version)
                 if s.name == result.validation_stages["validation_stage"])
    reviews = json.loads(stage.payload_json)["claim_specific_reviews"]
    review = next(r for r in reviews if r["item_id"] == observation["observation_id"])
    assert review["application_search_performed"] is True
    assert review["model_counterevidence_review_claim"] is True
    assert review["matched_caveats"]  # actual text, not just a claimed flag


def test_qualified_finding_that_explicitly_reconciles_caveat_remains_admissible(db_session):
    result = _management_run(
        db_session, claim="Succession plans may reduce transition risk, but successful transition remains uncertain.",
        mechanism="Plans may reduce operating disruption; transition effectiveness is unproven.",
        disposition="qualified", basis="reported_experience", counter_ids=[{"$evidence": "may not result"}],
        extra_passages={
            "support": ("Item 10", "Independent review reports that succession plans exist."),
            "adverse": ("Item 1A", "Our succession plans may not result in a successful transition.")})
    observation = result.governed_assessment["management_assessments"][0]
    assert observation["disposition"] == "qualified"
    assert observation["observation_id"] in result.m3_analysis["consolidated_conclusion"]["admitted_management_signal_ids"]


def test_genuinely_supported_management_outcome_remains_admitted(db_session):
    claim = "Management implemented a procurement program that improved operating cash available to creditors."
    result = _management_run(db_session, claim=claim,
        mechanism="Measured procurement efficiencies improved operating cash available for debt repayment.",
        extra_passages={"outcome": ("Item 7", "Independent review confirmed: " + claim +
            " Measured procurement efficiencies improved operating cash available for debt repayment.")})
    observation = result.governed_assessment["management_assessments"][0]
    assert observation["disposition"] in {"supported", "qualified"}
    assert result.validation_stages["evidence_supported_findings"]["management"] >= 1


def test_supported_structural_credit_positive_and_negative_controls_still_admitted(db_session):
    from tests.integration.test_bo_m3_structural_credit import _fixture, _load, FICTIONAL_STRUCTURE, FICTIONAL_PASSAGES

    result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(FICTIONAL_PASSAGES),
                    metrics=h.metrics(), m3_responses={"single_pass": _fixture("fictional_mixed")},
                    corporate_structure_document=_load(FICTIONAL_STRUCTURE))
    assert result.validation_stages["evidence_supported_findings"]["structural_credit"] >= 2
    assert {f["credit_direction"] for f in result.governed_assessment["structural_credit_findings"]} >= {"positive", "negative"}


def test_supplement_never_relaxes_an_existing_rejection(recorded):
    facts = recorded["bundle"]["recorded_request"]["inputs"]["corporate_structure"]
    governed = validate_m3_assessment(recorded["raw"]["m3_assessment"],
        allowed_evidence_ids=set(recorded["texts"]), evidence_texts=recorded["texts"], structure_facts=facts)
    governed.structural_credit_findings[0].disposition = Disposition.REJECTED
    strengthened, _ = strengthen_m3_assessment(governed, evidence_texts=recorded["texts"], structure_facts=facts)
    assert strengthened.structural_credit_findings[0].disposition == "rejected"
    assert strengthened.structural_credit_findings[0].admissibility == "audit_only"
