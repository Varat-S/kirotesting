"""Validator 8 deterministic-check tests (Module 4 specialist).

Validator 8 re-checks the Module 4 output deterministically: debt
reconciliation, subordination-ratio recomputation, maturity ordering,
legal-entity attribution and double-counting prevention. All assertions use the
HCA Healthcare FY2025 10-K fixture.
"""

from __future__ import annotations

import copy

from app.schemas.module4 import Module4Output
from app.services.modules.module4.debt_structure_validator import (
    CHECK_DOUBLE_COUNTING,
    CHECK_LEGAL_ENTITY,
    CHECK_MATURITY_ORDER,
    CHECK_RECONCILIATION,
    CHECK_SUBORDINATION_RATIO,
    DebtStructureValidator,
)
from tests.fixtures.hca_module4 import hca_module4_output


def _output(mutate=None) -> Module4Output:
    data = copy.deepcopy(hca_module4_output())
    if mutate is not None:
        mutate(data)
    return Module4Output.model_validate(data)


def test_clean_hca_output_passes_all_checks() -> None:
    report = DebtStructureValidator().validate(_output())
    assert report.passed is True
    assert len(report.checks) == 5
    assert all(c.passed for c in report.checks)


def test_reconciliation_detects_tranche_mismatch() -> None:
    """A tranche principal that no longer sums to the reported total fails."""

    def break_sum(data):
        data["deliverable_1"]["senior_notes_schedule"][0]["principal_amount_m"] += 500.0

    report = DebtStructureValidator().validate(_output(break_sum))
    assert report.passed is False
    assert report.check(CHECK_RECONCILIATION).passed is False
    # Observed figures expose the gap for audit.
    observed = report.check(CHECK_RECONCILIATION).observed
    assert abs(observed["delta_m"] - 500.0) < 1e-6


def test_subordination_ratio_recomputation_catches_wrong_ratio() -> None:
    def wrong_ratio(data):
        data["deliverable_1"]["creditor_priority_framework"][
            "structural_subordination_drag_ratio"
        ] = 0.50

    report = DebtStructureValidator().validate(_output(wrong_ratio))
    assert report.check(CHECK_SUBORDINATION_RATIO).passed is False


def test_maturity_order_catches_out_of_order_schedule() -> None:
    def shuffle(data):
        notes = data["deliverable_1"]["senior_notes_schedule"]
        notes[0], notes[1] = notes[1], notes[0]  # 2030 before 2028

    report = DebtStructureValidator().validate(_output(shuffle))
    maturity = report.check(CHECK_MATURITY_ORDER)
    assert maturity.passed is False
    assert maturity.observed["first_violation"] is not None


def test_legal_entity_requires_borrower_and_distinct_guarantor() -> None:
    def collapse_entity(data):
        tranche = data["deliverable_1"]["senior_notes_schedule"][0]
        # Same entity as sole borrower and sole credit support.
        tranche["legal_borrower"] = "HCA Inc."
        tranche["guarantors_credit_support"] = "Guaranteed by HCA Inc."

    report = DebtStructureValidator().validate(_output(collapse_entity))
    assert report.check(CHECK_LEGAL_ENTITY).passed is False


def test_legal_entity_accepts_opco_issuer_holdco_guarantor() -> None:
    """HCA Inc. issuer guaranteed by HCA Healthcare, Inc. is coherent."""
    report = DebtStructureValidator().validate(_output())
    assert report.check(CHECK_LEGAL_ENTITY).passed is True


def test_double_counting_flags_undrawn_revolver_in_total() -> None:
    """Folding the undrawn revolver headroom into the total must be caught."""

    def inflate(data):
        framework = data["deliverable_1"]["creditor_priority_framework"]
        revolver = data["deliverable_1"]["non_note_obligations_schedule"][2]
        # Erroneously treat the full $8,000M revolver limit as drawn and add it.
        headroom = revolver["available_facility_limit_m"] - revolver["principal_drawn_m"]
        # Commercial paper undrawn headroom is 4000-2207 = 1793; plus revolver
        # 8000 => 9793 total headroom; push reported total to the inflated figure.
        cp = data["deliverable_1"]["non_note_obligations_schedule"][0]
        cp_headroom = cp["available_facility_limit_m"] - cp["principal_drawn_m"]
        framework["consolidated_total_indebtedness_m"] += headroom + cp_headroom

    report = DebtStructureValidator().validate(_output(inflate))
    assert report.check(CHECK_DOUBLE_COUNTING).passed is False


def test_double_counting_excludes_intercompany_borrowings() -> None:
    """Intercompany borrowings must not enter the reconciled total."""

    def add_intercompany(data):
        data["deliverable_1"]["non_note_obligations_schedule"].append(
            {
                "obligation_facility": "Intercompany loan from subsidiary",
                "legal_borrower": "HCA Inc.",
                "principal_drawn_m": 1000.0,
                "available_facility_limit_m": None,
                "interest_rate_terms": "n/a",
                "maturity": "n/a",
                "collateral_security_status": "unsecured",
                "guarantors_credit_support": "n/a",
                "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
            }
        )

    # Reconciliation still passes because intercompany is excluded from the sum.
    report = DebtStructureValidator().validate(_output(add_intercompany))
    assert report.check(CHECK_RECONCILIATION).passed is True
    # And the intercompany figure is tracked as excluded.
    observed = report.check(CHECK_DOUBLE_COUNTING).observed
    assert observed["intercompany_m"] == 1000.0


def test_report_exposes_failures_list() -> None:
    def break_two(data):
        data["deliverable_1"]["senior_notes_schedule"][0]["principal_amount_m"] += 500.0
        data["deliverable_1"]["creditor_priority_framework"][
            "structural_subordination_drag_ratio"
        ] = 0.99

    report = DebtStructureValidator().validate(_output(break_two))
    failing_ids = {c.check_id for c in report.failures}
    assert CHECK_RECONCILIATION in failing_ids
    assert CHECK_SUBORDINATION_RATIO in failing_ids
