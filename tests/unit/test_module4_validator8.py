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
    CHECK_RECONCILING_ITEMS,
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
    assert len(report.checks) == 6
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


# HCA FY2025 Note 8 as the filing's table is extracted (one cell per line).
NOTE8 = ("Senior unsecured notes payable through 2095 (effective interest rate of 5.1%)\n"
         "43,700\n41,116\nDebt issuance costs and discounts\n(436\n)\n(369\n)\n"
         "Total long-term debt (average life of 11.9 years, rates averaging 5.1%)\n"
         "44,285\n43,031\nTotal debt\n46,492\n43,031")
PASSAGES = {"ev_note8": NOTE8}


def _without_cost_line(data):
    d1 = data["deliverable_1"]
    d1["non_note_obligations_schedule"] = [
        o for o in d1["non_note_obligations_schedule"]
        if o["obligation_facility"] != "Debt issuance costs and discounts"]


def _with_item(**item):
    """The HCA output with its -436 line moved out of the schedule (into a reconciling item)."""

    def mutate(data):
        _without_cost_line(data)
        if item:
            data["deliverable_1"]["reconciling_items"] = [{
                "kind": "debt_issuance_costs_and_discounts",
                "label": "Debt issuance costs and discounts", "amount_m": -436.0,
                "quote": "Debt issuance costs and discounts (436)",
                "evidence_id": "ev_note8", **item}]
    return _output(mutate)


def test_a_reconciling_item_quoted_from_the_filing_closes_the_gap() -> None:
    report = DebtStructureValidator().validate(
        _with_item(evidence_id="ev_note8"), filing_passages=PASSAGES)
    assert report.passed is True, [c.detail for c in report.failures]
    recon = report.check(CHECK_RECONCILIATION)
    assert recon.observed["reconciling_items_m"] == -436.0 and recon.observed["gap_m"] == 0
    assert report.check(CHECK_RECONCILING_ITEMS).observed["items"][0]["verified"] is True


def test_without_the_item_the_gap_is_reported_but_not_blamed_on_undrawn_debt() -> None:
    report = DebtStructureValidator().validate(_with_item(), filing_passages=PASSAGES)
    recon = report.check(CHECK_RECONCILIATION)
    assert recon.passed is False and recon.observed["gap_m"] == -436.0
    # The gap is not undrawn headroom or intercompany debt, so check 5 does not fire.
    assert report.check(CHECK_DOUBLE_COUNTING).passed is True


def test_a_reconciling_item_not_in_the_filing_is_not_counted() -> None:
    for item, reason in [
        ({"quote": "Unamortised issuance costs of (436)"}, "does not appear verbatim"),
        ({"amount_m": -500.0}, "does not state the amount 500"),
        ({"amount_m": 436.0}, "must reduce the total"),
    ]:
        report = DebtStructureValidator().validate(_with_item(**item), filing_passages=PASSAGES)
        assert report.check(CHECK_RECONCILIATION).observed["reconciling_items_m"] == 0
        check = report.check(CHECK_RECONCILING_ITEMS)
        assert check.passed is False and reason in check.detail, check.detail


def test_reconciling_items_need_the_filing_text() -> None:
    report = DebtStructureValidator().validate(_with_item(evidence_id="ev_note8"))
    check = report.check(CHECK_RECONCILING_ITEMS)
    assert check.passed is False and "no filing passages" in check.detail


def test_large_reconciling_items_are_held_for_review() -> None:
    passages = {"ev_note8": NOTE8.replace("(436", "(1,436")}

    def mutate(data):
        _without_cost_line(data)
        data["deliverable_1"]["senior_notes_schedule"][-2]["principal_amount_m"] += 1000.0
        data["deliverable_1"]["reconciling_items"] = [{
            "kind": "debt_issuance_costs_and_discounts",
            "label": "Debt issuance costs and discounts", "amount_m": -1436.0,
            "quote": "Debt issuance costs and discounts (1,436)", "evidence_id": "ev_note8"}]

    report = DebtStructureValidator().validate(_output(mutate), filing_passages=passages)
    assert report.check(CHECK_RECONCILIATION).passed is True
    check = report.check(CHECK_RECONCILING_ITEMS)
    assert check.passed is False and "review above 2%" in check.detail
