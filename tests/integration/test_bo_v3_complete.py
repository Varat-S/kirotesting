"""Offline acceptance of the complete four-module BO v3 pathway."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.models.orm import AgentRunRow, EvidencePacketRow, RiskScoreRow
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.roster_v3 import V3_PRIMARY_AGENT_IDS, V3_SPECS
from app.services.bo.runner import M3_PACKET_AGENT_ID, BusinessOverviewRunner
from tests import bo_helpers as h


HCA_DIR = Path("examples/healthcare/hca-2025")


def _fact(code, hid, claim, phrase, quote, *, direction="neutral"):
    return h.hyp(hid, claim, "fact", direction, "supported", support=[phrase],
                 quotes=[(phrase, quote)], materiality="high")


def _script():
    answers = {
        "m1_revenue_quality": {"parameters": [], "hypotheses": [_fact(
            "M1S1", "M1S1-H001", "Managed-care agreements set negotiated rates.",
            "managed care contracts",
            "Our managed care contracts are generally multi-year agreements that set negotiated rates")]},
        "m1_customer_stickiness": {"parameters": [], "hypotheses": [_fact(
            "M1S2", "M1S2-H001", "Admissions were stable during the year.",
            "admissions were stable", "admissions were stable during the year",
            direction="positive")]},
        "m1_pricing_power": {"parameters": [], "hypotheses": [_fact(
            "M1S3", "M1S3-H001", "Either party may terminate a managed-care agreement on notice.",
            "managed care contracts", "Either party may terminate an agreement on notice",
            direction="negative")]},
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
            "The business has negotiated rates but material labour, regulatory "
            "and execution exposures.", ["M1S1-H001", "M1S4-H001", "M2-H001", "M4-H001"],
            materiality="high"),
        "strengths": [h.claim(
            "v3_strength", "strength", "M1S1", "Managed-care agreements set "
            "negotiated rates.", ["M1S1-H001"], claim_type="fact")],
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


def test_all_four_modules_agent6_synthesis_challenge_and_report(db_session,
                                                                       tmp_path):
    result, backend = _run(db_session, tmp_path)
    rows = db_session.query(AgentRunRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()
    executed = {row.agent_id for row in rows}
    assert set(V3_PRIMARY_AGENT_IDS) <= executed
    assert "m1_resilience_integration" in executed
    assert {"business_orchestrator", "business_challenge"} <= executed
    modules = result.memo["module_conclusions"]
    assert all(m["status"] == "complete" for code, m in modules.items() if code != "M3")
    # Module 3 is its own workflow: with no declared sources it records why it did not run.
    assert modules["M3"]["workflow"] == "m3-rebuild" and modules["M3"]["status"] == "incomplete"
    assert result.handoff["module_conclusions"].keys() == {"M1", "M2", "M3", "M4"}
    assert result.handoff["compound_business_risks"]
    assert result.handoff["canonical_references"]["analysis_run_id"] == \
        result.analysis_run_id
    assert result.memo["cross_module_risk_interactions"]
    assert result.memo["presentation"]["display"]
    packets = db_session.query(EvidencePacketRow).filter_by(
        analysis_run_id=result.analysis_run_id).all()
    assert {packet.agent_id for packet in packets} == {
        spec.agent_id for spec in V3_SPECS} | {M3_PACKET_AGENT_ID}
    # Agent 6 is resealed after its validated component evidence is admitted.
    assert len(packets) >= len(V3_SPECS)
    assert db_session.query(RiskScoreRow).filter_by(
        analysis_run_id=result.analysis_run_id).count() == 0
    assert result.output_paths["business_overview.json"].exists()
    assert len(backend.requests) >= 10


def test_m1_sections_list_validated_findings_the_narrative_skipped(db_session, tmp_path):
    from app.services.bo.reporting import _m1_sections_with_further_findings, render_memo_html

    result, _ = _run(db_session, tmp_path)
    memo = result.memo
    sections = [s for s in memo["sections"] if s["code"].startswith("M1")]
    further = {s["code"]: [f["hypothesis_id"] for f in s["further_findings"]] for s in sections}
    used = {h for s in memo["sections"] for c in s["claims"] for h in c["hypothesis_ids"]}
    used |= {h for side in memo["strengths_vulnerabilities"].values() for c in side
             for h in c["hypothesis_ids"]}
    used |= set((memo["executive"]["provisional_view"] or {}).get("hypothesis_ids") or [])
    # Every M1 finding is either in the narrative or listed under its own section.
    expected = {s["code"]: [f["hypothesis_id"] for f in memo["module_conclusions"]["M1"]["findings"]
                            if f["section"] == s["code"] and f["hypothesis_id"] not in used]
                for s in sections}
    assert further == expected
    # No validated M1 finding is lost: each is in the narrative or under its own section.
    listed = {h for ids in further.values() for h in ids}
    assert {f["hypothesis_id"] for f in memo["module_conclusions"]["M1"]["findings"]} <= used | listed
    html = render_memo_html(memo)
    assert "Admissions were stable during the year" in html
    assert all(s["notice"] is None for s in sections if s["further_findings"])
    # A memo recorded before this change derives the same findings at render time.
    legacy = copy.deepcopy(memo)
    for section in legacy["sections"]:
        section.pop("further_findings", None)
    derived = {s["code"]: [f["hypothesis_id"] for f in s["further_findings"]]
               for s in _m1_sections_with_further_findings(legacy)["sections"]
               if s["code"].startswith("M1")}
    assert derived == further


def test_incomplete_module_blocks_agent6_and_remains_visible(db_session, tmp_path):
    script = _script()
    # An invalid disposition still rejects the agent (an unknown citation
    # alone is now removed on its own).
    script["m1_pricing_power"]["hypotheses"][0]["disposition"] = "invented"
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


def test_finding_inserted_for_section_coverage_is_not_a_double_count(db_session, tmp_path):
    # Live HCA runs: the model lists a negative finding only under weaknesses,
    # coverage copies it into its own section's material risks, and the
    # synthesis must not then be rejected for double counting that copy.
    script = _script()
    script["orchestrate"]["weaknesses"].append(h.claim(
        "v3_weak_reg", "weakness", "M2", "The industry is extensively regulated and "
        "exposed to legislative reimbursement reductions.", ["M2-H001"],
        claim_type="fact", materiality="high"))
    result, backend = _run(db_session, tmp_path, script=script)
    history = result.audit["orchestrator"]["history"]
    assert [item["status"] for item in history] == ["accepted"], history
    assert backend.calls("orchestrate") == 1
    risks = {c["claim_id"]: c for c in result.audit["orchestrator"]["claims"]}
    assert "COV-M2-H001" in risks and "v3_weak_reg" in risks
