"""Offline acceptance of the complete four-module BO v3 pathway."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.models.orm import AgentRunRow, EvidencePacketRow, RiskScoreRow
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.roster_v3 import V3_PRIMARY_AGENT_IDS, V3_SPECS
from app.services.bo.runner import BusinessOverviewRunner
from tests import bo_helpers as h


HCA_DIR = Path("examples/healthcare/hca-2025")


def _fact(code, hid, claim, phrase, quote, *, direction="neutral"):
    return h.hyp(hid, claim, "fact", direction, "supported", support=[phrase],
                 quotes=[(phrase, quote)], materiality="high")


def _script():
    answers = {
        "m1_revenue_quality": {"parameters": [], "hypotheses": [_fact(
            "M1S1", "M1S1-H001", "Revenue comes from patient services at hospitals "
            "and outpatient facilities.", "Our sources of revenue",
            "Our sources of revenue are patient services provided at our facilities")]},
        "m1_customer_stickiness": {"parameters": [], "hypotheses": [_fact(
            "M1S2", "M1S2-H001", "Managed-care contracts set rates and do not "
            "guarantee minimum patient volumes.", "managed care contracts",
            "the contracts do not guarantee minimum volumes of patients",
            direction="negative")]},
        "m1_pricing_power": {"parameters": [], "hypotheses": [_fact(
            "M1S3", "M1S3-H001", "Revenue per equivalent admission increased from "
            "pricing, payer mix and case mix together.", "Revenue per equivalent",
            "Revenue per equivalent admission increased 4.1% due to pricing, payer "
            "mix and case mix acuity", direction="mixed")]},
        "m1_operating_model": {"parameters": [], "hypotheses": [_fact(
            "M1S4", "M1S4-H001", "A nursing shortage and rising contract labour "
            "costs pressure the operating model.", "nursing shortage",
            "We face a nursing shortage and rising contract labor costs",
            direction="negative")]},
        "m1_competitive_durability": {"parameters": [], "hypotheses": [_fact(
            "M1S5", "M1S5-H001", "The company competes with hospitals and ambulatory "
            "surgery centres.", "We compete with",
            "We compete with other hospitals, including not-for-profit systems, and "
            "with ambulatory surgery centers", direction="negative")]},
        "m2_industry": {"parameters": [], "hypotheses": [_fact(
            "M2", "M2-H001", "The industry is extensively regulated and exposed to "
            "legislative reimbursement reductions.", "extensive regulation",
            "We are subject to extensive regulation", direction="negative")]},
        "m3_corporate_management": {"parameters": [], "hypotheses": [_fact(
            "M3", "M3-H001", "The board oversees governance and succession planning.",
            "executive officers", "The board of directors oversees governance and "
            "succession planning", direction="positive")]},
        "m4_strategy_capital": {"parameters": [], "hypotheses": [_fact(
            "M4", "M4-H001", "Three hospital acquisitions were completed and their "
            "integration remains ongoing.", "Capital expenditures were focused",
            "We completed acquisitions of three hospitals during the year; integration "
            "of these facilities is ongoing", direction="mixed")]},
    }
    answers["m1_resilience_integration"] = {
        "parameters": [],
        "hypotheses": [{
            "hypothesis_id": "M1A6-H001",
            "claim": "Labour scarcity may intensify competitive pressure for clinical "
                     "staff and weaken operating resilience.",
            "claim_type": "inference", "direction": "negative",
            "disposition": "qualified", "confidence": "medium_low",
            "materiality": "high", "display_priority": "display",
            "supporting_evidence_ids": [
                {"$hypothesis_evidence": "M1S4-H001"},
                {"$hypothesis_evidence": "M1S5-H001"}],
            "component_hypothesis_ids": ["M1S4-H001", "M1S5-H001"],
            "interaction_kind": "compound_risk",
            "transmission_mechanism": "Competition for scarce clinical staff can "
                                      "raise costs and constrain service capacity.",
            "missing_information": ["Competitor staffing data"],
        }],
    }
    answers["orchestrate"] = {
        "overall_assessment": h.claim(
            "v3_overall", "assessment", "executive",
            "The business has essential service demand but material labour, regulatory "
            "and execution exposures.", ["M1S1-H001", "M1S4-H001", "M2-H001", "M4-H001"],
            materiality="high"),
        "strengths": [h.claim(
            "v3_strength", "strength", "M3", "The board oversees governance and "
            "succession planning.", ["M3-H001"], claim_type="fact")],
        "weaknesses": [h.claim(
            "v3_weakness", "weakness", "M1S4", "Labour scarcity pressures the "
            "operating model.", ["M1S4-H001"], claim_type="fact", materiality="high")],
        "key_drivers": [],
        "material_risks": [h.claim(
            "v3_compound", "risk", "M1A6", "Labour scarcity and competition can "
            "compound operating pressure.", ["M1S4-H001", "M1S5-H001"],
            materiality="high")],
        "open_questions": ["What are competitor staffing levels?"],
        "unresolved_contradictions": [],
        "follow_up": [],
    }
    answers["challenge"] = {"challenges": [], "overall_outcome": "accepted",
                            "summary": "No material defect identified."}
    return answers


def _run(db_session, tmp_path, *, script=None, sector=None):
    backend = ScriptedBoBackend(script or _script())
    runner = BusinessOverviewRunner(
        db_session, backend=backend, bo_roster="v3", sector=sector,
        data_root=tmp_path / "data", output_root=tmp_path / "out")
    return runner.run_prepared(
        h.CASE, h.evidence_snapshot(), h.metrics(), write_outputs=True), backend


def test_all_four_modules_agent6_synthesis_challenge_score_and_report(db_session,
                                                                       tmp_path):
    result, backend = _run(db_session, tmp_path)
    rows = db_session.query(AgentRunRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()
    executed = {row.agent_id for row in rows}
    assert set(V3_PRIMARY_AGENT_IDS) <= executed
    assert "m1_resilience_integration" in executed
    assert {"business_orchestrator", "business_challenge"} <= executed
    assert all(m["status"] == "complete"
               for m in result.memo["module_conclusions"].values())
    assert result.handoff["module_conclusions"].keys() == {"M1", "M2", "M3", "M4"}
    assert result.handoff["compound_business_risks"]
    assert result.handoff["canonical_references"]["analysis_run_id"] == \
        result.analysis_run_id
    assert result.memo["cross_module_risk_interactions"]
    assert result.memo["presentation"]["display"]
    packets = db_session.query(EvidencePacketRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()
    assert {packet.agent_id for packet in packets} == {
        spec.agent_id for spec in V3_SPECS}
    # Agent 6 is resealed after its validated component evidence is admitted.
    assert len(packets) >= len(V3_SPECS)
    scores = db_session.query(RiskScoreRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()
    assert scores[-1].detail["stage"] == "authoritative_post_challenge"
    assert scores[-1].status != "final"
    assert result.output_paths["business_overview.json"].exists()
    assert len(backend.requests) >= 11


def test_validated_m3_extension_flows_through_module_conclusion_and_handoff(
        db_session, tmp_path):
    script = _script()
    evidence = {"$evidence": "executive officers"}
    script["m3_corporate_management"]["m3_assessment"] = {
        "schema_version": "bo-m3-corporate-assessment-1.0",
        "borrower_identification": {
            "status": "unavailable", "entity_id": None, "evidence_ids": [],
            "limitation": "No proposed borrowing entity is identified.",
        },
        "legal_entities": [{
            "entity_id": "reporting-parent", "legal_name": "Example Parent",
            "jurisdiction": None, "classification": "reporting_parent",
            "verification_status": "verified", "evidence_ids": [evidence],
            "materiality_reason": "Reporting entity relevant to governance.",
        }],
        "ownership_relationships": [], "cash_flow_locations": [],
        "cash_transfer_relationships": [], "structural_credit_findings": [],
        "management_assessments": [{
            "observation_id": "MO1", "management_id": "board",
            "legal_name": "Board of Directors", "role": "Board", "effective_from": None,
            "effective_to": None, "dimension": "competence",
            "hypothesis": "The board oversees succession planning.",
            "credit_mechanism": "Succession oversight may reduce leadership continuity risk.",
            "supporting_evidence_ids": [evidence], "contradicting_evidence_ids": [],
            "supporting_evidence_investigated": True,
            "contradictory_evidence_investigated": True,
            "disposition": "qualified", "admissibility": "qualified_narrative",
            "confidence": "medium", "materiality": "medium", "missing_information": [],
        }],
        "governance_screening": [],
        "information_gaps": ["Management outcomes are not disclosed."],
        "downstream_dependencies": [],
    }
    result, _ = _run(db_session, tmp_path, script=script)
    module = result.handoff["module_conclusions"]["M3"]
    assert module["m3_assessment_status"] == "validated"
    assert module["m3_assessment"]["schema_version"] == \
        "bo-m3-corporate-assessment-1.0"
    assert module["m3_assessment"]["model_proposal"]["legal_entities"]


def test_incomplete_module_blocks_agent6_and_remains_visible(db_session, tmp_path):
    script = _script()
    script["m1_pricing_power"]["hypotheses"][0][
        "supporting_evidence_ids"] = ["invented"]
    result, _ = _run(db_session, tmp_path, script=script)
    m1 = result.memo["module_conclusions"]["M1"]
    assert m1["status"] == "incomplete"
    assert "m1_pricing_power" in m1["failed_or_missing_agents"]
    assert result.record["agents"]["m1_resilience_integration"]["status"] == "skipped"
    assert result.conclusion_status == "human_review_required"
    assert result.handoff["module_conclusions"]["M1"]["status"] == "incomplete"


def test_cross_module_contradiction_is_preserved_for_challenge(db_session, tmp_path):
    script = _script()
    finding = script["m2_industry"]["hypotheses"][0]
    finding["disposition"] = "qualified"
    finding["contradicting_evidence_ids"] = [h.ev("cannot predict")]
    result, _ = _run(db_session, tmp_path, script=script)
    m2 = result.memo["module_conclusions"]["M2"]["findings"][0]
    assert m2["disposition"] == "qualified"
    assert m2["contradicting_evidence_ids"]
    assert result.audit["challenger"]["rounds"][0]["status"] == "validated"


def test_material_challenge_triggers_one_bounded_v3_rerun(db_session, tmp_path):
    script = _script()
    first = copy.deepcopy(script["m1_competitive_durability"])
    script["m1_competitive_durability"] = [first, copy.deepcopy(first)]
    script["challenge"] = [{"challenges": [{
        "challenge_id": "v3-c1", "target": "v3_compound",
        "issue_type": "ignored_counterevidence", "severity": "material",
        "reason": "Recheck the interaction against competitive evidence.",
        "affected_agent_ids": ["m1_competitive_durability"],
        "requires_reanalysis": True,
        "requested_rerun_scope": ["m1_competitive_durability"],
    }]}, {"challenges": [], "overall_outcome": "accepted"}]
    result, backend = _run(db_session, tmp_path, script=script)
    assert result.record["remediation"]["round"] == 1
    assert backend.calls("m1_competitive_durability") == 2
    assert backend.calls("challenge") == 2
    assert len(result.record["challenge_rounds"]) == 2


@pytest.mark.skipif(not (HCA_DIR / "package.json").exists(),
                    reason="HCA source package unavailable")
def test_hca_primary_package_runs_all_v3_modules_offline(db_session, tmp_path):
    from app.services.pipeline.package import SourcePackage
    from app.services.pipeline.runner import CreditMemoPipeline

    prepared = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).prepare_case("HCA_BO_V3", package=SourcePackage.load(HCA_DIR / "package.json"))
    result = BusinessOverviewRunner(
        db_session, bo_roster="v3", data_root=tmp_path / "data",
        output_root=tmp_path / "out").run_prepared(
            "HCA_BO_V3", prepared.evidence_snapshot, prepared.metrics,
            write_outputs=False)
    executed = {row.agent_id for row in db_session.query(AgentRunRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()}
    assert set(V3_PRIMARY_AGENT_IDS) <= executed
    assert result.handoff["case_id"] == "HCA_BO_V3"
    assert any(module["status"] == "incomplete"
               for module in result.handoff["module_conclusions"].values())
    assert result.conclusion_status in {"unavailable", "human_review_required"}


def test_additional_sector_package_executes_without_fo(db_session, tmp_path):
    passages = {
        "air_revenue": ("Item 1", "Revenue comes from passenger travel, cargo and "
                        "a loyalty program; demand is seasonal and cyclical."),
        "air_customers": ("Item 1", "Corporate customer agreements set negotiated "
                          "fares but do not guarantee passenger volumes."),
        "air_costs": ("Item 1A", "Fuel prices, union labour costs and aircraft "
                      "supplier delays can increase operating costs."),
        "air_competition": ("Item 1", "We compete with network, low-cost and foreign "
                            "airlines on price, schedule and loyalty benefits."),
        "air_regulation": ("Item 1A", "Aviation is extensively regulated and severe "
                             "weather can disrupt operations."),
        "air_management": ("Item 10", "The board oversees management succession and "
                             "corporate governance."),
        "air_strategy": ("Item 7", "Management plans aircraft capital expenditure; "
                           "delivery and integration outcomes are not yet known."),
    }
    base = h.evidence_snapshot(passages)
    evidence = base.model_copy(update={
        "case_id": "COASTAL_AIR_BO",
        "entities": [{"entity_id": "CAB", "legal_name": "Coastal Air Lines",
                      "borrower_flag": True}],
    })
    result = BusinessOverviewRunner(
        db_session, bo_roster="v3", sector=None,
        data_root=tmp_path / "data", output_root=tmp_path / "out").run_prepared(
            "COASTAL_AIR_BO", evidence, {}, write_outputs=False)
    executed = {row.agent_id for row in db_session.query(AgentRunRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()}
    assert set(V3_PRIMARY_AGENT_IDS) <= executed
    assert result.handoff["case_id"] == "COASTAL_AIR_BO"
    assert all(module["status"] == "incomplete"
               for module in result.handoff["module_conclusions"].values())
