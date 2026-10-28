"""Acceptance: BOTH Module 4 specialists execute inside the orchestrated BO run.

Covers the full path:

    prepared evidence -> BO orchestrator -> m4_strategy_capital specialist
    -> m4 debt-structure specialist -> both validators -> combined governed M4
    output -> cross-module synthesis -> challenger -> final governance/handoff.

Flag ``enable_m4_debt_specialist=True`` turns on the orchestrated debt specialist
(default off preserves the established behaviour). Offline, deterministic, no
network and no paid model.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone

import pytest

from app.schemas.snapshots import CanonicalEvidenceSnapshot
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from tests import bo_helpers as h
from tests.fixtures.hca_module4 import hca_module4_output


# Debt-bearing evidence so the orchestrated debt specialist has admitted
# source material to select (it relies on admitted evidence only).
_DEBT_PASSAGES = dict(h.PASSAGES)
_DEBT_PASSAGES["ev_debt_notes"] = (
    "Item 8", "Our long-term debt consists of senior unsecured notes with "
    "principal of $43,700 million issued by the operating company and guaranteed "
    "by the parent. Total indebtedness was $46,492 million at year end.")
_DEBT_PASSAGES["ev_debt_maturity"] = (
    "Item 7", "The senior notes have staggered maturities; the revolving credit "
    "facility is undrawn. Refinancing of maturing tranches occurred during the year.")


def _debt_evidence() -> CanonicalEvidenceSnapshot:
    base = h.evidence_snapshot(_DEBT_PASSAGES)
    # Add structured debt facts the specialist can select as admitted evidence.
    return base.model_copy(update={
        "facts": [
            {"fact_id": "fact_total_debt", "concept": "TotalIndebtedness",
             "value": 46492.0, "unit": "USD_million", "period": h.PERIOD},
            {"fact_id": "fact_senior_notes", "concept": "SeniorNotesPrincipal",
             "value": 43700.0, "unit": "USD_million", "period": h.PERIOD},
        ],
    })


def _script_with_debt(debt_output=None):
    """The v3 script plus a dedicated ``m4_debt`` response for the debt specialist."""
    from tests.integration.test_bo_v3_complete import _script as v3_script
    script = v3_script()
    script["m4_debt"] = debt_output or hca_module4_output()
    return script


def _run(db_session, tmp_path, *, script=None, enable=True, enforce=True):
    backend = ScriptedBoBackend(script or _script_with_debt())
    runner = BusinessOverviewRunner(
        db_session, backend=backend, bo_roster="v3",
        data_root=tmp_path / "data", output_root=tmp_path / "out",
        enable_m4_debt_specialist=enable, enforce_validators=enforce)
    result = runner.run_prepared(
        h.CASE, _debt_evidence(), h.metrics(), write_outputs=True)
    return result, backend


def test_both_m4_specialists_reach_governed_output_and_synthesis(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path)

    m4 = result.memo["module_conclusions"]["M4"]
    # Strategy specialist's findings are present (roster-v3 m4_strategy_capital).
    assert "debt_structure_refinancing" in m4["capabilities"]
    assert "strategy_capital" in m4["capabilities"]
    # Debt specialist contribution survives into the governed M4 output.
    debt = m4["m4_debt_assessment"]
    assert debt is not None
    assert debt["status"] == "validated"
    assert debt["validated"] is True
    assert debt["validator_id"] == "validator_8_debt_structure"
    assert debt["assessment"]["deliverable_1"]["senior_notes_schedule"]
    assert debt["validator_report"]["passed"] is True
    # The debt specialist was invoked exactly once (no duplicate model calls).
    assert backend.calls("m4_debt") == 1

    # It flows through to synthesis + challenger + governance.
    assert result.handoff["module_conclusions"]["M4"]["m4_debt_assessment"]["validated"]
    assert result.audit["challenger"]["rounds"]  # challenger ran
    # A clean debt result does not block the run.
    assert result.conclusion_status in {"accepted", "accepted_with_qualification"}


def test_flag_off_preserves_single_specialist_behaviour(db_session, tmp_path):
    from tests.integration.test_bo_v3_complete import _script as v3_script
    result, _ = _run(db_session, tmp_path, script=v3_script(), enable=False)
    m4 = result.memo["module_conclusions"]["M4"]
    # With the flag off there is no debt assessment and M4 keeps its prior shape.
    assert "m4_debt_assessment" not in m4
    assert "capabilities" not in m4


def test_debt_validator_failure_escalates_and_forces_human_review(db_session, tmp_path):
    broken = copy.deepcopy(hca_module4_output())
    # Break reconciliation so Validator 8 fails.
    broken["deliverable_1"]["senior_notes_schedule"][0]["principal_amount_m"] += 5000.0
    result, _ = _run(db_session, tmp_path,
                     script=_script_with_debt(debt_output=broken))

    m4 = result.memo["module_conclusions"]["M4"]
    debt = m4["m4_debt_assessment"]
    assert debt["status"] == "failed"
    assert debt["validated"] is False
    assert debt["validator_report"]["passed"] is False
    assert debt["escalation_id"]  # a mandatory escalation was raised
    assert debt["validator_issues"]
    # The failed debt result makes M4 incomplete -> human review is forced.
    assert m4["status"] == "incomplete"
    assert result.conclusion_status == "human_review_required"
    # The mandatory escalation remains unresolved and visible downstream.
    assert result.handoff["module_conclusions"]["M4"]["m4_debt_status"] == "failed"


def test_validators_off_reports_validator8_without_escalating(db_session, tmp_path):
    broken = copy.deepcopy(hca_module4_output())
    broken["deliverable_1"]["senior_notes_schedule"][0]["principal_amount_m"] += 5000.0
    result, backend = _run(db_session, tmp_path,
                           script=_script_with_debt(debt_output=broken), enforce=False)

    m4 = result.memo["module_conclusions"]["M4"]
    debt = m4["m4_debt_assessment"]
    assert debt["status"] == "advisory"
    assert debt["validated"] is False and debt["validator_report"]["passed"] is False
    assert debt["validator_issues"] and debt["escalation_id"] is None
    assert debt["reconciliation_followup"] is None  # no validator-driven follow-up call
    assert debt["assessment"]["deliverable_1"]["senior_notes_schedule"]
    assert m4["status"] == "complete"
    assert any("not enforced" in note for note in m4["missing_information"])
    # An ungoverned draft is always left for a reviewer, and says so everywhere.
    assert result.conclusion_status == "human_review_required"
    assert result.memo["run"]["validators_enforced"] is False
    assert result.audit["run"]["validators_enforced"] is False
    assert result.handoff["validators_enforced"] is False


def test_validators_off_still_drafts_the_full_memo(db_session, tmp_path):
    result, _ = _run(db_session, tmp_path, enforce=False)
    assert result.memo["sections"] and result.handoff["business_topic_conclusion_id"]
    assert result.audit["orchestrator"]["history"][-1]["status"] in (
        "accepted", "accepted_unvalidated")
    engine = [h for h in result.audit["hypotheses"]["records"]
              if h["validator_version"].startswith("bo-hypothesis-validator")]
    assert engine and all(h["validator_version"].endswith("+advisory") for h in engine)


def test_debt_unavailable_when_no_admitted_debt_evidence(db_session, tmp_path):
    from tests.integration.test_bo_v3_complete import _script as v3_script
    script = v3_script()
    script["m4_debt"] = hca_module4_output()
    backend = ScriptedBoBackend(script)
    runner = BusinessOverviewRunner(
        db_session, backend=backend, bo_roster="v3",
        data_root=tmp_path / "data", output_root=tmp_path / "out",
        enable_m4_debt_specialist=True)
    # Plain evidence (no debt passages / facts) -> debt specialist reports
    # unavailable rather than fabricating an analysis, and never calls the model.
    result = runner.run_prepared(h.CASE, h.evidence_snapshot(), h.metrics(),
                                 write_outputs=False)
    m4 = result.memo["module_conclusions"]["M4"]
    assert m4["m4_debt_assessment"]["status"] == "unavailable"
    assert m4["m4_debt_assessment"]["model_invoked"] is False
    assert backend.calls("m4_debt") == 0
    assert m4["status"] == "incomplete"
