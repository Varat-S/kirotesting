"""Validator 7 numerical/evidence verification (Priority 2).

Offline unit coverage of the four-state verification distinction the North Star
requires, exercised at the ``validate_m3_assessment`` seam and the deterministic
check helpers. No model is called.

The central correctness property: ABSENCE of support is NOT contradiction.

* VERIFIED              — claim supported by the cited evidence / canonical datum;
* UNSUPPORTED/UNVERIFIED — cited evidence does not substantiate (absent, no
                          conflicting value) -> disposition ``unverified``;
* CONTRADICTED          — cited evidence asserts a conflicting value/period on a
                          comparable basis -> disposition ``rejected``;
* REQUIRES_HUMAN_REVIEW — causal/legal reasoning numbers cannot settle ->
                          ``qualified``/``unverified`` + a human-review reason.

Unit confusions (pp vs %, dollars vs millions, quarterly vs annual, consolidated
vs subsidiary, revenue vs EBITDA growth) must NOT be silently treated as matches.
"""

from __future__ import annotations

import copy

from app.services.bo.m3_validation import (
    _canonical_financial_checks,
    _deterministic_claim_checks,
    _identify_canonical_metric,
    validate_m3_assessment,
)


ALLOWED = {"ev-1", "ev-2"}


def _management(**over):
    base = {
        "observation_id": "MO1", "management_id": "mgmt-1", "legal_name": "Dana Lee",
        "role": "CFO", "effective_from": None, "effective_to": None,
        "dimension": "competence", "evidence_basis": "outcome_evidence",
        "attribution": "independently_observed", "assurance_scope": None,
        "assurance_date": None,
        "hypothesis": "Net leverage declined during FY2024.",
        "credit_mechanism": "Lower leverage reduces refinancing and default risk.",
        "supporting_evidence_ids": ["ev-1"], "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported", "admissibility": "established",
        "confidence": "medium", "materiality": "high",
        "credit_direction": "neutral", "missing_information": [],
    }
    base.update(over)
    return base


def _govern(management, *, evidence_texts, canonical_metrics=None):
    proposal = {
        "borrower_identification": {"status": "unavailable",
                                    "limitation": "SO confirms the obligor."},
        "legal_entities": [],
        "management_assessments": [copy.deepcopy(management)],
        "governance_screening": [],
        "information_gaps": ["Completeness not established."],
    }
    governed = validate_m3_assessment(
        proposal, allowed_evidence_ids=ALLOWED,
        validated_borrower_entity_ids=set(),
        evidence_texts=evidence_texts, canonical_metrics=canonical_metrics)
    return governed.management_assessments[0], governed


# -- the central distinction: absence != contradiction -----------------------


def test_correctly_cited_value_is_verified():
    obs, _ = _govern(
        _management(hypothesis="Net leverage declined during FY2024."),
        evidence_texts={"ev-1": "Net leverage declined during FY2024."})
    assert obs.disposition.value == "supported"


def test_unsupported_numeric_assertion_is_unverified_not_rejected():
    # 30 absent; the cited text carries NO number -> UNSUPPORTED (unverified).
    obs, _ = _govern(
        _management(hypothesis="EBITDA margin was 30%."),
        evidence_texts={"ev-1": "The company remained profitable."})
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"
    assert any("unsupported" in r.casefold() for r in obs.validation_reasons)


def test_directly_contradicted_value_is_rejected():
    # A different value for the same class IS present -> CONTRADICTED (rejected).
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 51% in FY2024."),
        evidence_texts={"ev-1": "Revenue grew 48.9% in FY2024."})
    assert obs.disposition.value == "rejected"
    assert any("contradicted" in r.casefold() for r in obs.validation_reasons)


def test_wrong_fiscal_period_is_contradicted_when_a_period_is_present():
    obs, _ = _govern(
        _management(hypothesis="Leverage fell in FY2023."),
        evidence_texts={"ev-1": "Leverage fell in FY2024."})
    assert obs.disposition.value == "rejected"


def test_absent_fiscal_period_with_no_period_present_is_unsupported():
    obs, _ = _govern(
        _management(hypothesis="Leverage fell in FY2023."),
        evidence_texts={"ev-1": "Leverage fell over the period."})
    assert obs.disposition.value == "unverified"


# -- unit / basis confusions must not be silent matches ----------------------


def test_percentage_point_vs_percentage_change_flagged_via_canonical():
    # Canonical operating_margin = 0.12 (12%). A claim mixing "%" and
    # "percentage points" cannot be reconciled deterministically -> not verified.
    canonical = {"operating_margin": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Operating margin improved 2% (2 percentage points) in FY2024.",
                    credit_mechanism="Margin expansion supports debt service."),
        evidence_texts={"ev-1": "Operating margin improved in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value != "supported"


def test_dollars_vs_millions_do_not_spuriously_match():
    # The claim says 3 (billions) but cited evidence has 3000 (millions): the
    # bare tokens differ and a conflicting number is present -> not a silent pass.
    checks = _deterministic_claim_checks(
        "Net debt is $3 billion.", ["ev-1"],
        {"ev-1": "Net debt was 3000 in millions."})
    failed = [c for c in checks if not c.passed]
    assert failed
    assert any(c.contradiction for c in failed)


def test_quarterly_vs_annual_period_distinguished():
    # Claim asserts Q3 2024; evidence asserts FY2024 -> conflicting period class.
    checks = _deterministic_claim_checks(
        "Revenue rose in Q3 2024.", ["ev-1"], {"ev-1": "Revenue rose in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


def test_consolidated_vs_subsidiary_value_contradiction():
    # A claim naming net_debt at a specific value conflicts with the canonical
    # consolidated value -> contradicted (basis/scope guarded by the canonical map).
    canonical = {"net_debt": {
        "value": 5000.0, "raw_value": 5000.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Net debt is 100 at the subsidiary.",
                    credit_mechanism="Low subsidiary debt is read as low group leverage."),
        evidence_texts={"ev-1": "Group net debt was 5000."},
        canonical_metrics=canonical)
    assert obs.disposition.value == "rejected"


def test_revenue_growth_vs_ebitda_growth_not_conflated():
    # The alias map resolves 'revenue grew' to revenue_growth, never EBITDA, so a
    # claim about EBITDA growth does not borrow the revenue canonical datum.
    assert _identify_canonical_metric("Revenue grew 10% in FY2024.") == "revenue_growth"
    assert _identify_canonical_metric("EBITDA grew 10% in FY2024.") is None


# -- causal / legal attribution is capped at human review --------------------


def test_numerically_correct_outcome_with_unsupported_causal_attribution():
    # The figure matches canonical revenue_growth (12%), but the CAUSAL
    # attribution ("because of management") cannot be settled by numbers ->
    # capped at human review (qualified), never supported.
    canonical = {"revenue_growth": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    obs, governed = _govern(
        _management(
            hypothesis="Revenue grew 12% in FY2024 because of current management's strategy.",
            credit_mechanism="The growth is attributed to management execution."),
        evidence_texts={"ev-1": "Revenue grew 12% in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value != "supported"
    assert any(i.rule_id == "m3.requires_human_review"
               for i in governed.validation_issues)


def test_rounding_and_tolerance_allows_close_values():
    # Canonical 12.0%; claim "12%" is within tolerance -> no numeric failure.
    canonical = {"revenue_growth": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Revenue grew 12% in FY2024.", canonical_metrics=canonical)
    assert all(c.passed for c in checks)


def test_missing_canonical_data_falls_back_without_fabrication():
    # No canonical datum for the claimed metric -> the canonical layer returns
    # no checks (the textual gate still applies); nothing is fabricated.
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Interest coverage was 4.0x in FY2024.", canonical_metrics={})
    assert checks == ()
