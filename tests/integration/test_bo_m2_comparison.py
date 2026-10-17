"""M2 — controlled offline comparison on the synthetic NFH case (specialist M1S2).

Scripted model responses (examples/bo_m2/nfh_m1s2) are EQUALLY WRONG in both
arms. These tests check the harness guarantees and pin the measured pipeline
behaviour as a regression; they say nothing about live model quality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.orm import AuditEvent, Case
from app.services.bo.comparison import (
    prepare_specialist,
    run_comparison,
    run_investigation,
)
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
    # Still accepted by the single-pass validators (lexical checks only).
    assert o["S3"]["usable"] and o["S5"]["usable"] and o["S6"]["usable"]
    assert arm["controls_usable"] == 2
    assert arm["parameters"]["contract_termination_risk_assessment"]["value"] == "low"
    # Fix R4: an admitted passage with no measured change is not outcome evidence.
    assert o["S5"]["mitigant_effectiveness"] == "unproven"
    assert "mitigant_outcome_not_measured" in o["S5"]["review_flags"]


def test_measured_investigation_outcomes(comparison):
    """Regression pin of the investigation arm (see BO_M2_COMPARISON_REPORT.md)."""
    _, report = comparison
    arm = report["arms"]["investigation"]
    o = _outcomes(report, "investigation")
    for sid in ("S2", "S3", "S4", "S5", "S6"):
        assert not o[sid]["usable"], sid
    assert o["S5"]["mitigant_effectiveness"] == "unproven"
    # Fix R1: correct reported facts restated from verbatim quotes stay usable.
    assert o["S1"]["disposition"] == "supported" and o["S1"]["usable"]
    # Fix R3: a contradictory-search passage the model never weighed caps S7.
    assert o["S7"]["disposition"] == "qualified"
    assert "unassessed_contradictory_candidates" in o["S7"]["investigation"]["adapter_notes"]
    assert arm["parameters"] == {
        "customer_dependence_assessment": {"status": "ok",
                                           "value": "concentrated_payer_categories"},
        "contract_termination_risk_assessment": {"status": "unavailable", "value": None},
        "switching_cost_assessment": {"status": "unavailable", "value": None}}


# -- adversarial variants of the investigation script ---------------------------


def _variant(db_session, mutate):
    db_session.add(Case(case_id=h.CASE, status="open"))
    db_session.flush()
    prep = prepare_specialist(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(),
                              metrics=h.metrics(), spec=V3_SPEC_BY_CODE["M1S2"])
    stages = {k: v for k, v in json.loads(
        (Path(SCRIPTS) / "investigation.json").read_text()).items() if not k.startswith("_")}
    mutate(stages, prep)
    arm = run_investigation(db_session, prep, stages)
    return {r.hypothesis_id: r for r in arm.validation.records}


def _result(stages, hid):
    return next(r for r in stages["test"]["results"] if r["hypothesis_id"] == hid)


def test_r2_metric_link_with_the_right_concept_still_cannot_carry_an_inference(db_session):
    """S3 again, but the model copies the metric's concept correctly."""
    def mutate(stages, prep):
        trace = next(t for t in prep.indicators.traces
                     if t.parameter_id == "revenue_share.statement_geographical.texas")
        _result(stages, "h3")["mechanism_premises"][0]["claimed_concept"] = trace.label
    records = _variant(db_session, mutate)
    s3 = records["M1S2-H003"]
    assert s3.investigation["effective_status"] == "unverified"
    assert any("inferential step is not declared" in r
               for r in s3.investigation["downgrade_reasons"])
    assert not s3.usable


def test_r1_a_wrong_inference_relabelled_as_a_reported_fact_is_not_usable(db_session):
    """S2 again, but the model calls 'guarantees volumes' a reported fact."""
    def mutate(stages, prep):
        h2 = next(x for x in stages["generate"]["hypotheses"] if x["hypothesis_id"] == "h2")
        h2["classification"] = "reported_fact"
    records = _variant(db_session, mutate)
    s2 = records["M1S2-H002"]
    assert s2.investigation["effective_status"] == "unverified"
    assert not s2.usable


def test_r1_dropping_a_negation_breaks_verbatim_support(db_session):
    """S7 again, but the premise drops 'do not' from the quoted sentence."""
    def mutate(stages, prep):
        _result(stages, "h7")["mechanism_premises"][0]["premise"] = (
            "Agreements guarantee minimum volumes and are terminable on notice")
    records = _variant(db_session, mutate)
    assert records["M1S2-H007"].investigation["effective_status"] == "unverified"


@pytest.mark.parametrize("statement, quote, passage, expected", [
    ("Medicare and Medicaid accounted for 45% of revenues",
     "Medicare and Medicaid accounted for 45% of our revenues.",
     "Medicare and Medicaid accounted for 45% of our revenues. Other text.", True),
    ("Medicare and Medicaid accounted for 54% of revenues",
     "Medicare and Medicaid accounted for 45% of our revenues.",
     "Medicare and Medicaid accounted for 45% of our revenues.", False),
    ("Medicare and Medicaid accounted for 45% of revenues",
     "Medicare and Medicaid accounted for 45% of all revenues.",
     "Medicare and Medicaid accounted for 45% of our revenues.", False),  # not verbatim
    ("Contracts guarantee minimum volumes",
     "the contracts do not guarantee minimum volumes of patients",
     "the contracts do not guarantee minimum volumes of patients", False),
    ("Contracts guarantee minimum volumes and stable revenue",
     "the contracts guarantee minimum volumes of patients",
     "the contracts guarantee minimum volumes of patients", False),  # added word
])
def test_verbatim_fact_support(statement, quote, passage, expected):
    from app.services.analysis.grounding import verbatim_fact_support

    assert verbatim_fact_support(statement, quote, passage) is expected
