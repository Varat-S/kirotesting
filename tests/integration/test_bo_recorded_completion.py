"""Acceptance coverage for recorded specialists -> governed BO completion."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.schemas.bo import AcceptedBusinessOverview, MitigantAssessment
from app.services.bo.completion import (
    ConservativeCompletionBackend,
    RecordedCompletionError,
    complete_recorded_specialists,
    load_recorded_specialists,
    remediation_plan,
)
from app.services.bo.reporting import handoff_content_hash
from app.services.bo.synthesis import validate_orchestrator_output
from app.services.llm.client import FakeLLMBackend


R5 = Path("output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/") / (
    "ar_fb0bba6709e94a14/specialists_run.json")


def _require_recorded_r5() -> None:
    if not R5.is_file():
        pytest.skip("Genuine HCA R5 artifact is not present in this checkout")


@pytest.fixture(scope="module")
def source():
    _require_recorded_r5()
    return load_recorded_specialists(R5)


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    _require_recorded_r5()
    return complete_recorded_specialists(
        R5, backend=ConservativeCompletionBackend(), execution_mode="offline",
        output_dir=tmp_path_factory.mktemp("bo-recorded-completion"),
    )


def test_genuine_r5_counts_are_verified_not_hardcoded(source):
    assert source.validation["computed_counts"] == {
        "agents_validated": 9,
        "hypotheses_recorded": 28,
        "hypotheses_usable": 26,
        "parameters_accepted": 19,
        "parameters_held_back": 3,
        "coverage_checks": 10,
        "coverage_addressed": 9,
    }
    assert source.payload["usage"]["total_tokens"] == 256_359


def test_actual_r5_completes_without_replaying_specialists(completed):
    assert [c["stage"] for c in completed["model_calls"]] == [
        "orchestrate", "challenge"]
    assert completed["incremental_usage"]["calls"] == 2
    assert completed["remediation"]["specialist_calls_executed"] == 0
    assert completed["validation"]["synthesis"]["status"] == "valid"
    assert completed["validation"]["challenge"]["status"] == "valid"


def test_handoff_is_schema_valid_tamper_evident_and_pending(completed):
    handoff = completed["handoff"]
    AcceptedBusinessOverview.model_validate(handoff)
    assert handoff_content_hash(handoff) == handoff["content_hash"]
    assert handoff["human_review_status"] == "pending"
    assert handoff["conclusion_status"] == "human_review_required"
    assert handoff["unresolved_challenges"]


def test_reporting_parent_is_not_promoted_to_unknown_transaction_borrower(completed):
    handoff = completed["handoff"]
    assert handoff["borrower_name"] is None
    assert handoff["borrower_entity_id"] is None
    assert handoff["canonical_references"]["reporting_company"] == "HCA Healthcare, Inc."
    assert handoff["canonical_references"]["transaction_obligor_status"] == "unknown"


def test_rejected_or_audit_only_m3_does_not_enter_authoritative_fields(source, completed):
    assert "M3-H005" in source.module_conclusions["M3"]["audit_only_hypothesis_ids"]
    assert "M3-H005" not in completed["handoff"]["accepted_finding_ids"]
    assert source.governed_m3["borrower_identification"]["status"] == "unavailable"


def test_unproven_mitigants_remain_unproven(completed):
    assert completed["handoff"]["validated_mitigants"] == []
    assert {m["hypothesis_id"] for m in completed["handoff"]["unproven_mitigants"]} \
        == {"M1S4-H002", "M1S4-H004"}


def test_replay_is_deterministic_for_validation_scoring_and_hashes(completed):
    replay = FakeLLMBackend(model_id="recorded-replay")
    replay.register("analyze", completed["pre_challenge"]["synthesis"])
    replay.register("challenge", completed["challenge"]["output"])
    second = complete_recorded_specialists(
        R5, backend=replay, execution_mode="replay")
    # Execution labels/models may differ; governed analytical hashes must not.
    assert second["handoff"]["content_hash"] == completed["handoff"]["content_hash"]
    assert second["validation"] == completed["validation"]


def test_material_challenge_only_plans_direct_bounded_specialists():
    findings = [{
        "challenge_id": "a", "severity": "material", "requires_reanalysis": True,
        "affected_agent_ids": ["m1_pricing_power", "m2_industry"],
        "requested_rerun_scope": ["m4_strategy_capital", "business_orchestrator"],
    }]
    plan = remediation_plan(findings)
    assert plan["selected_specialists"] == ["m1_pricing_power", "m2_industry"]
    assert plan["budget_exceeded"] is True
    assert len(plan["selected_specialists"]) == plan["max_specialist_calls"] == 2


def test_challenged_material_claim_is_removed_from_authoritative_handoff(completed):
    challenge = {
        "challenges": [{
            "challenge_id": "targeted", "target": "strength_1",
            "issue_type": "hypothesis_as_fact", "severity": "material",
            "reason": "The strength is overstated.",
            "affected_agent_ids": ["m3_corporate_management"],
            "affected_parameter_ids": [], "evidence_ids": [],
            "requires_reanalysis": True,
            "requested_rerun_scope": ["m3_corporate_management"],
        }],
        "overall_outcome": "rerun_required", "summary": "Targeted defect.",
    }
    backend = FakeLLMBackend()
    backend.register("analyze", completed["pre_challenge"]["synthesis"])
    backend.register("challenge", challenge)
    result = complete_recorded_specialists(R5, backend=backend, execution_mode="offline")
    assert result["handoff"]["business_strengths"] == []
    assert result["post_challenge"]["changes"] == [{
        "claim_id": "strength_1", "before": "established_or_qualified",
        "after": "disputed", "reason": "material_or_high_challenge"}]
    assert result["remediation"]["selected_specialists"] == ["m3_corporate_management"]
    assert result["remediation"]["specialist_calls_executed"] == 0
    assert result["handoff"]["unresolved_challenges"][0]["affected_hypothesis_ids"]


def test_unknown_evidence_fails_import(tmp_path, source):
    payload = copy.deepcopy(source.payload)
    payload["agents"][0]["hypotheses"][0]["supporting_evidence_ids"].append("missing-evidence")
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RecordedCompletionError, match="absent from the recorded corpus"):
        load_recorded_specialists(path)


def test_unsupported_high_confidence_claim_cannot_become_established(source):
    audit_only = source.hypotheses["M3-H005"].model_copy(update={"confidence": "high"})
    hypotheses = dict(source.hypotheses)
    hypotheses[audit_only.hypothesis_id] = audit_only
    parsed = {
        "overall_assessment": {
            "claim_id": "unsupported", "category": "assessment",
            "text": "The transaction borrower has unrestricted access to group cash.",
            "section": "executive", "claim_type": "fact",
            "hypothesis_ids": [audit_only.hypothesis_id],
            "parameter_result_ids": [], "evidence_ids": audit_only.evidence_ids(),
            "materiality": "high", "uncertainty": None, "quoted_values": [],
        }
    }
    issues = validate_orchestrator_output(
        parsed, hypotheses=hypotheses, reconciliation=source.reconciliation,
        indicators=source.indicators,
        accepted_parameter_ids={p.parameter_result_id for p in source.parameters},
    )
    assert any(issue.startswith("inadmissible_hypothesis:") for issue in issues)
    assert any(issue.startswith("no_validated_basis:") for issue in issues)


@pytest.mark.parametrize("with_mitigant", [True, False])
def test_claim_may_cite_its_mitigants_outcome_evidence(source, with_mitigant):
    # Live HCA run: a claim cited a mitigant's outcome evidence, which the
    # hypothesis relies on although it is neither support nor contradiction.
    usable = [h for h in source.hypotheses.values() if h.usable]
    base = usable[0]
    outcome = next(e for h in usable for e in h.evidence_ids()
                   if e not in base.evidence_ids())
    mitigant = MitigantAssessment(capability="Annual rate increases", existence="verified",
                                  outcome_evidence_ids=[outcome])
    hypotheses = dict(source.hypotheses)
    hypotheses[base.hypothesis_id] = base.model_copy(
        update={"mitigant": mitigant if with_mitigant else None})
    parsed = {
        "overall_assessment": {
            "claim_id": "mitigated", "category": "assessment",
            "text": "Rate renewals offset cost inflation.",
            "section": "executive", "claim_type": "inference",
            "hypothesis_ids": [base.hypothesis_id],
            "parameter_result_ids": [], "evidence_ids": [*base.evidence_ids(), outcome],
            "materiality": "high", "uncertainty": "Inferred.", "quoted_values": [],
        }
    }
    issues = validate_orchestrator_output(
        parsed, hypotheses=hypotheses, reconciliation=source.reconciliation,
        indicators=source.indicators,
        accepted_parameter_ids={p.parameter_result_id for p in source.parameters},
    )
    mismatched = any(i.startswith("claim_evidence_mismatch:") for i in issues)
    assert mismatched is not with_mitigant


def test_cross_specialist_contradiction_remains_visible(tmp_path, source):
    payload = copy.deepcopy(source.payload)
    first = payload["agents"][0]["hypotheses"][0]
    second = payload["agents"][1]["hypotheses"][0]
    first["direction"] = "positive"
    second["claim"] = first["claim"]
    second["direction"] = "negative"
    second["supporting_evidence_ids"] = list(first["supporting_evidence_ids"])
    second["evidence_quotes"] = list(first["evidence_quotes"])
    path = tmp_path / "contradiction.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_recorded_specialists(path)
    assert any({first["hypothesis_id"], second["hypothesis_id"]}
               == set(item["hypothesis_ids"])
               for item in loaded.reconciliation.contradictions)


def test_source_artifact_is_not_modified(completed):
    import hashlib
    assert hashlib.sha256(R5.read_bytes()).hexdigest() == completed["source"]["sha256"]
