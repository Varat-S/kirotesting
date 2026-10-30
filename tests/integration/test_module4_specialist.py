"""Module 4 specialist integration tests (HCA FY2025 10-K).

Drives the full specialist path end-to-end against the deterministic fake LLM
backend: registered prompt -> schema-validated output -> Validator 8 -> optional
mandatory escalation. No network, fully reproducible.
"""

from __future__ import annotations

import copy

import pytest

from app.prompts.registry import PromptRegistry
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.llm.client import FakeLLMBackend, LLMClient
from app.services.modules.module4 import (
    DebtStructureValidator,
    Module4Specialist,
)
from app.services.modules.module4.specialist import (
    RULE_VALIDATOR8_FAILURE,
    Module4Inputs,
    Module4RejectedError,
)
from tests.fixtures.hca_module4 import hca_module4_output


def _specialist(db_session, case_id="HCA-FY2025"):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    engine = EscalationEngine(db_session, audit=audit, case_id=case_id)
    specialist = Module4Specialist(
        client, validator=DebtStructureValidator(), escalation_engine=engine, case_id=case_id
    )
    return specialist, backend, engine


def _inputs() -> Module4Inputs:
    return Module4Inputs(
        sec_filing_snippets=[{"fact_id": "note8_total_debt", "page": "F-18"}],
        structured_financials=[{"fact_id": "total_indebtedness_2025"}],
        debt_schedule_facts=[{"fact_id": "senior_notes_43700"}],
    )


def test_specialist_produces_four_validated_deliverables(db_session) -> None:
    specialist, backend, engine = _specialist(db_session)
    # Module 4 is driven via the existing ``analyze`` method, so the fake
    # backend is scripted under that method key.
    backend.register("analyze", hca_module4_output())

    result = specialist.run(_inputs())

    out = result.output
    # All four deliverables present.
    assert out.deliverable_1.senior_notes_schedule
    assert out.deliverable_2.refinancing_log
    assert out.deliverable_3.capital_deployment_reconciliation
    assert out.deliverable_4.executive_credit_summary_box.target_entity_and_period

    # Validator 8 passed every deterministic check -> no escalation.
    assert result.validated is True
    assert result.validator8.passed is True
    assert result.escalation_id is None
    assert not engine.has_unresolved_mandatory("HCA-FY2025")


def test_reconciliation_matches_reported_total(db_session) -> None:
    """D1 tranches reconcile to HCA's reported $46,492M total indebtedness."""
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", hca_module4_output())

    result = specialist.run(_inputs())
    recon = result.validator8.check("V8-01-RECONCILIATION")
    assert recon.passed is True
    assert recon.observed["reported_total_m"] == 46492.0
    assert abs(recon.observed["delta_m"]) <= 1.0


def test_legal_borrower_distinct_from_holdco_guarantor(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", hca_module4_output())

    result = specialist.run(_inputs())
    notes = result.output.deliverable_1.senior_notes_schedule
    # Issuer is the OpCo (HCA Inc.); guarantor is the HoldCo parent.
    assert all(t.legal_borrower == "HCA Inc." for t in notes)
    assert all("HCA Healthcare, Inc." in t.guarantors_credit_support for t in notes)


def test_validator8_failure_raises_mandatory_escalation(db_session) -> None:
    """Broken reconciliation -> Validator 8 fails -> mandatory escalation."""
    specialist, backend, engine = _specialist(db_session)
    broken = copy.deepcopy(hca_module4_output())
    broken["deliverable_1"]["senior_notes_schedule"][0]["principal_amount_m"] += 2000.0
    backend.register("analyze", broken)

    result = specialist.run(_inputs())

    assert result.validated is False
    assert result.escalation_id is not None
    open_mandatory = engine.open_escalations(mandatory_only=True)
    assert any(e.rule_id == RULE_VALIDATOR8_FAILURE for e in open_mandatory)
    assert engine.has_unresolved_mandatory("HCA-FY2025")


def test_schema_invalid_output_is_rejected(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", {"deliverable_1": "not an object"})
    with pytest.raises(Module4RejectedError):
        specialist.run(_inputs())


def test_module4_does_not_consume_other_module_outputs(db_session) -> None:
    """Module 4 is independent: its input payload exposes no module 1/2/3 keys."""
    payload = _inputs().to_payload()
    assert set(payload) == {
        "sec_filing_snippets",
        "structured_financials",
        "debt_schedule_facts",
        "missing_critical_evidence",
    }
    # No cross-module synthesis inputs leak in.
    assert not any("module" in key for key in payload)


NOTE8 = ("Senior unsecured notes payable through 2095 (effective interest rate of 5.1%)\n"
         "43,700\n41,116\nDebt issuance costs and discounts\n(436\n)\n(369\n)\n"
         "Total debt\n46,492\n43,031")


def _filing_inputs() -> Module4Inputs:
    return Module4Inputs(sec_filing_snippets=[{"fact_id": "ev_note8", "text": NOTE8}],
                         structured_financials=[{"fact_id": "total_indebtedness_2025"}])


def _unreconciled():
    """HCA output listing the notes at face value with no issuance-cost line (a $436M gap)."""
    data = copy.deepcopy(hca_module4_output())
    d1 = data["deliverable_1"]
    d1["non_note_obligations_schedule"] = [
        o for o in d1["non_note_obligations_schedule"]
        if o["obligation_facility"] != "Debt issuance costs and discounts"]
    return data


def _item(**overrides):
    return {"kind": "debt_issuance_costs_and_discounts", "label": "Debt issuance costs and discounts",
            "amount_m": -436.0, "quote": "Debt issuance costs and discounts (436)",
            "evidence_id": "ev_note8", **overrides}


def test_a_reconciliation_gap_is_explained_from_the_filing(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", _unreconciled())
    backend.register("analyze", {"reconciling_items": [_item()]}, key="m4_debt_reconcile")

    result = specialist.run(_filing_inputs())

    assert result.validated is True, [c.detail for c in result.validator8.failures]
    assert result.escalation_id is None
    assert [i.amount_m for i in result.output.deliverable_1.reconciling_items] == [-436.0]
    followup = result.reconciliation_followup
    assert followup["status"] == "answered" and followup["gap_m"] == -436.0
    assert followup["reconciled"] is True and followup["gap_after_m"] == 0


def test_an_explanation_not_in_the_filing_is_refused(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", _unreconciled())
    backend.register("analyze", {"reconciling_items": [_item(quote="Other adjustments to debt (436)")]},
                     key="m4_debt_reconcile")

    result = specialist.run(_filing_inputs())

    assert result.validated is False and result.escalation_id is not None
    failed = {c.check_id for c in result.validator8.failures}
    assert {"V8-01-RECONCILIATION", "V8-06-RECONCILING-ITEMS"} <= failed
    assert result.reconciliation_followup["reconciled"] is False


def test_a_failed_follow_up_keeps_the_first_answer(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", _unreconciled())  # no follow-up response scripted

    result = specialist.run(_filing_inputs())

    assert result.validated is False
    assert result.reconciliation_followup["status"] == "error"
    assert result.output.deliverable_1.reconciling_items == []


def test_no_follow_up_when_the_schedule_reconciles(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    backend.register("analyze", hca_module4_output())

    result = specialist.run(_filing_inputs())

    assert result.validated is True and result.reconciliation_followup is None


def test_items_are_dropped_when_the_instruments_already_reconcile(db_session) -> None:
    """EHC FY2025: the total is the face amount, so a listed discount would double-count."""
    specialist, backend, _ = _specialist(db_session)
    data = copy.deepcopy(hca_module4_output())
    data["deliverable_1"]["reconciling_items"] = [
        _item(label="Unamortized debt discount and fees", amount_m=-27.4,
              quote="Unamortized debt discount and fees")]
    backend.register("analyze", data)

    result = specialist.run(_filing_inputs())

    assert result.validated is True, [c.detail for c in result.validator8.failures]
    assert result.output.deliverable_1.reconciling_items == []
    assert [i["amount_m"] for i in result.dropped_reconciling_items] == [-27.4]
    assert result.reconciliation_followup is None


def test_items_that_close_the_gap_are_kept(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    data = _unreconciled()
    data["deliverable_1"]["reconciling_items"] = [_item()]
    backend.register("analyze", data)

    result = specialist.run(_filing_inputs())

    assert result.validated is True, [c.detail for c in result.validator8.failures]
    assert [i.amount_m for i in result.output.deliverable_1.reconciling_items] == [-436.0]
    assert result.dropped_reconciling_items == []


def test_senior_notes_are_listed_in_maturity_order(db_session) -> None:
    specialist, backend, _ = _specialist(db_session)
    shuffled = copy.deepcopy(hca_module4_output())
    shuffled["deliverable_1"]["senior_notes_schedule"].reverse()
    backend.register("analyze", shuffled)

    result = specialist.run(_inputs())

    years = [t.maturity_year for t in result.output.deliverable_1.senior_notes_schedule]
    assert years == sorted(years) and result.validated is True
