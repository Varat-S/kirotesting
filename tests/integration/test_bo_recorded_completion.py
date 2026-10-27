"""Acceptance coverage for recorded specialists -> governed BO completion."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.schemas.bo import AcceptedBusinessOverview
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


@pytest.fixture()
def recorded_path(db_session, tmp_path):
    """A current validator-governed recording, generated entirely offline."""
    from types import SimpleNamespace
    from app.services.bo.demo import run_demo
    from app.services.bo.offline import ScriptedBoBackend
    from tests.integration.test_bo_v3_complete import _script
    from tests import bo_helpers as h
    script = _script()
    script["m1_revenue_quality"]["hypotheses"][0]["direction"] = "positive"
    result = run_demo(
        db_session, h.CASE, package=None, prepared=SimpleNamespace(
            evidence_snapshot=h.evidence_snapshot(), metrics=h.metrics(),
            escalations=[], rejected_sources=[]),
        backend=ScriptedBoBackend(script), bo_roster="v3",
        data_root=tmp_path / "data", output_root=tmp_path / "out")
    return result["folder"] / "specialists_run.json"


@pytest.fixture()
def source(recorded_path):
    return load_recorded_specialists(recorded_path)


@pytest.fixture()
def completed(recorded_path, tmp_path):
    return complete_recorded_specialists(
        recorded_path, backend=ConservativeCompletionBackend(), execution_mode="offline",
        output_dir=tmp_path / "completed")


def test_current_recorded_counts_are_verified_not_hardcoded(source):
    counts = source.validation["computed_counts"]
    assert counts["agents_validated"] == 8
    assert counts["hypotheses_recorded"] == len(source.hypotheses)
    assert counts["hypotheses_usable"] == sum(h.usable for h in source.hypotheses.values())
    assert source.validation["mismatches"] == {}



def test_current_recording_completes_without_replaying_specialists(completed):
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
    assert handoff["canonical_references"]["reporting_company"] == "Northfield Health Systems"
    assert handoff["canonical_references"]["transaction_obligor_status"] == "unknown"


def test_rebuilt_missing_components_remain_independently_unavailable(source, completed):
    block = source.module_conclusions["M3"]
    assert block["status"] == "incomplete"
    assert {v["status"] for v in block["m3_rebuild"].values()} == {"not_attempted"}
    assert not any(h.startswith("M3-") for h in completed["handoff"]["accepted_finding_ids"])


def test_no_unproven_mitigant_is_promoted(completed):
    assert completed["handoff"]["validated_mitigants"] == []


def test_replay_is_deterministic_for_validation_scoring_and_hashes(completed, recorded_path):
    replay = FakeLLMBackend(model_id="recorded-replay")
    replay.register("analyze", completed["pre_challenge"]["synthesis"])
    replay.register("challenge", completed["challenge"]["output"])
    second = complete_recorded_specialists(
        recorded_path, backend=replay, execution_mode="replay")
    # Execution labels/models may differ; governed analytical hashes must not.
    assert second["handoff"]["content_hash"] == completed["handoff"]["content_hash"]
    assert second["post_challenge"]["score"] == completed["post_challenge"]["score"]
    assert second["validation"] == completed["validation"]


def test_material_challenge_only_plans_direct_bounded_specialists():
    findings = [{
        "challenge_id": "a", "severity": "material", "requires_reanalysis": True,
        "affected_agent_ids": ["m1_pricing_power", "m2_industry"],
        "requested_rerun_scope": ["m3_corporate_management", "business_orchestrator"],
    }]
    plan = remediation_plan(findings)
    assert plan["selected_specialists"] == ["m1_pricing_power", "m2_industry"]
    assert plan["budget_exceeded"] is False  # retired M3 is no longer a permitted rerun target
    assert len(plan["selected_specialists"]) == plan["max_specialist_calls"] == 2


def test_challenged_material_claim_is_removed_from_authoritative_handoff(completed, recorded_path):
    challenge = {
        "challenges": [{
            "challenge_id": "targeted", "target": "strength_1",
            "issue_type": "hypothesis_as_fact", "severity": "material",
            "reason": "The strength is overstated.",
            "affected_agent_ids": ["m1_revenue_quality"],
            "affected_parameter_ids": [], "evidence_ids": [],
            "requires_reanalysis": True,
            "requested_rerun_scope": ["m1_revenue_quality"],
        }],
        "overall_outcome": "rerun_required", "summary": "Targeted defect.",
    }
    backend = FakeLLMBackend()
    backend.register("analyze", completed["pre_challenge"]["synthesis"])
    backend.register("challenge", challenge)
    result = complete_recorded_specialists(recorded_path, backend=backend, execution_mode="offline")
    assert result["handoff"]["business_strengths"] == []
    assert result["post_challenge"]["changes"] == [{
        "claim_id": "strength_1", "before": "established_or_qualified",
        "after": "disputed", "reason": "material_or_high_challenge"}]
    assert result["remediation"]["selected_specialists"] == ["m1_revenue_quality"]
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
    from app.schemas.bo import Admissibility
    original = next(iter(source.hypotheses.values()))
    audit_only = original.model_copy(update={"confidence": "high", "admissibility": Admissibility.AUDIT_ONLY})
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


def test_source_artifact_is_not_modified(completed, recorded_path):
    import hashlib
    assert hashlib.sha256(recorded_path.read_bytes()).hexdigest() == completed["source"]["sha256"]


def test_historical_specialist_recording_is_explicitly_unsupported(tmp_path, source):
    payload = copy.deepcopy(source.payload)
    payload["agents"].append({"agent_id": "m3_corporate_management", "code": "M3"})
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RecordedCompletionError, match="Unsupported legacy M3"):
        load_recorded_specialists(path)
