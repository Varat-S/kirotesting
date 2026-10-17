"""M2 — controlled offline comparison on the synthetic NFH case (specialist M1S2).

Scripted model responses (examples/bo_m2/nfh_m1s2) are EQUALLY WRONG in both
arms. These tests check the harness guarantees and pin the measured pipeline
behaviour as a regression; they say nothing about live model quality.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import AuditEvent, Case
from app.services.bo.comparison import prepare_specialist, run_comparison
from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
from tests import bo_helpers as h

SCRIPTS = "examples/bo_m2/nfh_m1s2"


@pytest.fixture()
def comparison(db_session):
    db_session.add(Case(case_id=h.CASE, status="open"))
    db_session.flush()
    prep = prepare_specialist(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(),
                              metrics=h.metrics(), spec=V3_SPEC_BY_CODE["M1S2"])
    return prep, run_comparison(db_session, prep, SCRIPTS)


def _outcomes(report, arm):
    return {o["scenario"].split("_", 1)[0]: o for o in report["arms"][arm]["scenarios"]}


def test_both_arms_see_the_same_sealed_packet_and_nothing_else(comparison):
    prep, report = comparison
    assert report["packet_evidence_ids"] == list(prep.packet.evidence_ids)
    for arm in ("single_pass", "investigation"):
        for o in report["arms"][arm]["scenarios"]:
            assert o["present"], (arm, o["scenario"])
    assert report["identical_inputs_fingerprint"] == prep.fingerprint()


def test_call_counts_and_payload_sizes_are_recorded(comparison):
    _, report = comparison
    single = report["arms"]["single_pass"]["usage"]
    investigation = report["arms"]["investigation"]["usage"]
    assert single["model_calls"] == 1
    assert investigation["model_calls"] == 4  # generate, plan, test, parameters
    assert investigation["request_chars"] > single["request_chars"]


def test_out_of_scope_hypothesis_is_recorded_not_dropped(comparison):
    _, report = comparison
    out = report["investigation"]["out_of_scope"]
    assert [o["dimension"] for o in out] == ["operating_model_cost_resilience"]


def test_step_12_gate_result_is_reported(comparison):
    _, report = comparison
    assert report["investigation"]["progression"] == "blocked_pending_evidence"
    assert report["investigation"]["material_gaps"] > 0


def test_each_arm_writes_its_own_validator_audit_event(comparison, db_session):
    events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "agent_output_validated")
    ).scalars().all()
    assert len(events) == 2
    assert {e.actor_id for e in events} == {"validator_2"}
    assert {e.after["spec_code"] for e in events} == {"M1S2"}


def test_measured_single_pass_outcomes(comparison):
    """Regression pin of the baseline arm (see BO_M2_COMPARISON_REPORT.md)."""
    _, report = comparison
    arm = report["arms"]["single_pass"]
    o = _outcomes(report, "single_pass")
    assert not o["S2"]["usable"] and "rate_agreement_to_volume_guarantee" in o["S2"]["review_flags"]
    assert not o["S4"]["usable"]
    assert o["S3"]["usable"] and o["S5"]["usable"] and o["S6"]["usable"]
    assert arm["controls_usable"] == 2
    assert arm["parameters"]["contract_termination_risk_assessment"]["value"] == "low"
    # Baseline gap: an admitted passage cited as outcome evidence is accepted
    # even though it contains no outcome.
    assert o["S5"]["mitigant_effectiveness"] == "demonstrated"


def test_measured_investigation_outcomes(comparison):
    """Regression pin of the investigation arm (see BO_M2_COMPARISON_REPORT.md)."""
    _, report = comparison
    arm = report["arms"]["investigation"]
    o = _outcomes(report, "investigation")
    for sid in ("S2", "S4", "S5", "S6"):
        assert not o[sid]["usable"], sid
    # The model's "measured outcome" label does not make a mitigant effective.
    assert o["S5"]["mitigant_effectiveness"] == "unproven"
    # Narrative premises cannot be deterministically corroborated: correct
    # reported facts end up unverified too (recall cost).
    assert arm["controls_usable"] == 0
    assert all(p["status"] == "unavailable" for p in arm["parameters"].values())


def test_known_gap_metric_link_grounds_an_irrelevant_premise(comparison):
    """KNOWN GAP (not desired behaviour): a premise linked to any approved
    metric is graded against the metric's own statement, so a correct number
    attached to an unrelated claim is corroborated. Fixing it must flip this test.
    """
    _, report = comparison
    s3 = _outcomes(report, "investigation")["S3"]
    assert s3["investigation"]["effective_status"] == "supported"
    assert s3["usable"]
