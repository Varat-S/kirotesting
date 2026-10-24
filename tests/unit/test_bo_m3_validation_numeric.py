"""Validator 7 numerical/evidence verification (Priority 2).

Offline unit coverage of the four-state verification distinction the North Star
requires, exercised at the ``validate_m3_assessment`` seam and the deterministic
check helpers. No model is called.

The central correctness property (the "central rule"): a financial claim is
CONTRADICTED only when admitted evidence establishes a genuinely conflicting
assertion about the SAME metric, reporting entity/scope, period, accounting
basis and comparable units. A different number appearing somewhere in the cited
evidence is NOT sufficient proof of contradiction; matching values are NOT
sufficient for support if they refer to unrelated metrics.

* VERIFIED              — claim supported by the cited evidence / canonical datum;
* UNSUPPORTED/UNVERIFIED — cited evidence does not substantiate (absent, no
                          comparable conflicting value) -> disposition
                          ``unverified``;
* CONTRADICTED          — cited evidence asserts a conflicting value/period on a
                          demonstrably comparable basis -> disposition
                          ``rejected``;
* REQUIRES_HUMAN_REVIEW — causal/legal reasoning numbers cannot settle ->
                          ``qualified``/``unverified`` + a human-review reason.

Unit/basis confusions (pp vs %, dollars vs millions, quarterly vs annual,
consolidated vs subsidiary, revenue vs EBITDA growth, currency mismatches) must
NOT be silently treated as matches OR as contradictions.
"""

from __future__ import annotations

import copy

from app.services.bo.m3_validation import (
    _canonical_financial_checks,
    _comparable_basis,
    _deterministic_claim_checks,
    _identify_canonical_metric,
    _metric_bound_values,
    _period_value_tokens,
    _scope_comparable,
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


# -- Scenario 1: revenue growth claim vs an UNRELATED cash-flow figure --------


def test_revenue_growth_claim_vs_cash_flow_figure_is_unsupported_not_contradicted():
    # Problem A: the passage reports $250M operating cash flow, a DIFFERENT
    # metric. A different number for a different metric is NOT a conflict.
    obs, governed = _govern(
        _management(hypothesis="Revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={"ev-1": "Operating cash flow was $250 million in FY2024."})
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"
    # The recorded reason must NOT classify it as contradicted.
    assert not any("contradicted" in r.casefold() for r in obs.validation_reasons)


# -- Scenario 2: genuine same-basis contradiction (8% vs 12%) -----------------


def test_revenue_growth_8pct_vs_12pct_same_basis_is_contradicted():
    # Same metric (revenue growth), same company, same FY2024 period -> a
    # genuine CONTRADICTION. The fix must NOT weaken this.
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={"ev-1": "Revenue grew 8% in FY2024."})
    assert obs.disposition.value == "rejected"
    assert any("contradicted" in r.casefold() for r in obs.validation_reasons)


def test_directly_contradicted_value_is_rejected():
    # A different value for the SAME metric and period IS present -> CONTRADICTED.
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 51% in FY2024."),
        evidence_texts={"ev-1": "Revenue grew 48.9% in FY2024."})
    assert obs.disposition.value == "rejected"
    assert any("contradicted" in r.casefold() for r in obs.validation_reasons)


# -- Scenario 3: revenue-growth claim vs EBITDA-growth figure -----------------


def test_revenue_growth_vs_ebitda_growth_figure_is_unsupported():
    # The passage reports 12% EBITDA growth, a DIFFERENT metric than the claimed
    # revenue growth. The matching NUMBER (12) must NOT verify, and the different
    # metric must NOT contradict -> UNSUPPORTED.
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={"ev-1": "EBITDA grew 12% in FY2024."})
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"


# -- Scenario 4: subsidiary net debt vs consolidated net debt -----------------


def test_subsidiary_net_debt_vs_consolidated_is_not_auto_contradicted():
    # Problem B: a subsidiary-level claim cannot be contradicted by a
    # consolidated/group figure; the scopes are not equivalent -> UNSUPPORTED.
    canonical = {"net_debt": {
        "value": 5000.0, "raw_value": 5000.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Net debt was 100 at the subsidiary level in FY2024.",
                    credit_mechanism="Low subsidiary debt indicates headroom."),
        evidence_texts={"ev-1": "Consolidated group net debt was 5000 in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value in {"unverified", "qualified"}
    assert obs.disposition.value != "rejected"


def test_subsidiary_claim_contradicted_only_by_subsidiary_level_evidence():
    # When admitted evidence establishes a DIFFERENT subsidiary-level amount for
    # the same metric and period, a contradiction IS recorded (genuine case).
    checks = _deterministic_claim_checks(
        "Net debt was 100 at the subsidiary level in FY2024.", ["ev-1"],
        {"ev-1": "Net debt at the subsidiary level was 400 in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


# -- Scenario 5: FY2023 claim vs unrelated FY2024 results ---------------------


def test_fy2023_claim_vs_fy2024_citation_is_unsupported_not_contradicted():
    # A citation covering a DIFFERENT fiscal period does not disprove a claim
    # about another period -> UNSUPPORTED, never auto-contradicted.
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 10% in FY2023."),
        evidence_texts={"ev-1": "Revenue grew 10% in FY2024."})
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"


def test_absent_fiscal_period_with_no_period_present_is_unsupported():
    obs, _ = _govern(
        _management(hypothesis="Leverage fell in FY2023."),
        evidence_texts={"ev-1": "Leverage fell over the period."})
    assert obs.disposition.value == "unverified"


def test_different_period_alone_is_not_contradiction():
    # No number is claimed, only a period. A different period in the citation is
    # UNSUPPORTED, not a contradiction (corrects an earlier over-strict test).
    checks = _deterministic_claim_checks(
        "Leverage fell in FY2023.", ["ev-1"], {"ev-1": "Leverage fell in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


# -- Scenario 6: FY2024 revenue claim with directly conflicting FY2024 value --


def test_fy2024_revenue_claim_with_conflicting_fy2024_value_is_contradicted():
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 20% in FY2024."),
        evidence_texts={"ev-1": "Revenue grew 5% in FY2024."})
    assert obs.disposition.value == "rejected"
    assert any("contradicted" in r.casefold() for r in obs.validation_reasons)


# -- Scenario 7: currency mismatch with no established conversion --------------


def test_currency_mismatch_without_conversion_is_not_contradicted():
    # Claim in USD millions, canonical in SGD -> no established conversion, so a
    # value difference is UNVERIFIED, not a contradiction.
    canonical = {"net_debt": {
        "value": 5000.0, "raw_value": 5000.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "SGD", "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Consolidated net debt was 100 million USD in FY2024.",
                    credit_mechanism="Leverage read-across."),
        evidence_texts={"ev-1": "Consolidated net debt was 5000 million SGD in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value in {"unverified", "qualified"}
    assert obs.disposition.value != "rejected"


def test_deterministic_currency_mismatch_is_not_contradiction():
    checks = _deterministic_claim_checks(
        "Net debt was 100 million USD in FY2024.", ["ev-1"],
        {"ev-1": "Net debt was 140 million SGD in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


# -- Scenario 8: percentage points vs percentage ------------------------------


def test_percentage_point_vs_percentage_change_flagged_via_canonical():
    # A claim mixing "%" and "percentage points" cannot be reconciled
    # deterministically -> not verified (not an automatic match).
    canonical = {"operating_margin": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Operating margin improved 2% (2 percentage points) in FY2024.",
                    credit_mechanism="Margin expansion supports debt service."),
        evidence_texts={"ev-1": "Operating margin improved in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value != "supported"


# -- Scenario 9: several metrics/numbers in one passage -----------------------


def test_match_only_the_number_associated_with_the_relevant_metric():
    # The passage mixes revenue growth (12%) with operating cash flow ($250M) and
    # net debt (3000). A revenue claim of 12% is comparable to the revenue figure
    # only; the other numbers are not conflicting assertions for revenue growth.
    comparable, _ = _comparable_basis(
        "Revenue grew 12% in FY2024.",
        "Revenue grew 12% in FY2024; operating cash flow was 250; net debt was 3000.")
    assert comparable is True
    # A revenue claim of 20% in the same passage IS a conflict for revenue only.
    checks = _deterministic_claim_checks(
        "Revenue grew 20% in FY2024.", ["ev-1"],
        {"ev-1": "Revenue grew 12% in FY2024; operating cash flow was 250."})
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


def test_unrelated_metric_number_does_not_contradict():
    # A net-debt claim whose number is absent, against a passage reporting only
    # revenue growth, is UNSUPPORTED (different metric), not contradicted.
    checks = _deterministic_claim_checks(
        "Net debt was 500 in FY2024.", ["ev-1"],
        {"ev-1": "Revenue grew 12% in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


# -- Scenario 10: numerically verified outcome but unsupported causal claim ----


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


# -- Scenario 11: fully comparable basis is eligible for VERIFIED -------------


def test_same_value_metric_entity_period_basis_units_is_verified():
    canonical = {"revenue_growth": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Consolidated revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={"ev-1": "Consolidated revenue grew 12% in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value == "supported"


def test_rounding_and_tolerance_allows_close_values():
    # Canonical 12.0%; claim "12%" is within tolerance -> no numeric failure.
    canonical = {"revenue_growth": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Consolidated revenue grew 12% in FY2024.", canonical_metrics=canonical)
    assert all(c.passed for c in checks)


# -- Scenario 12: missing canonical data -> fail conservatively ---------------


def test_missing_canonical_data_falls_back_without_fabrication():
    # No canonical datum for the claimed metric -> the canonical layer returns
    # no checks (the textual gate still applies); nothing is fabricated.
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Interest coverage was 4.0x in FY2024.", canonical_metrics={})
    assert checks == ()


def test_unstated_scope_claim_vs_consolidated_datum_preserves_uncertainty():
    # The claim does not state its reporting scope; scope is UNKNOWN, so a value
    # difference is not assumed to be a contradiction -> UNSUPPORTED.
    canonical = {"net_debt": {
        "value": 5000.0, "raw_value": 5000.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "consolidated"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Net debt was 100 in FY2024.", canonical_metrics=canonical)
    value_checks = [c for c in checks if c.name == "canonical_numerical_value_match"]
    assert value_checks and not any(c.contradiction for c in value_checks)


# -- unit / basis confusions must not be silent matches OR contradictions ------


def test_dollars_vs_billions_scale_mismatch_is_not_contradiction():
    # "$3 billion" vs "3000 in millions" are the SAME magnitude in different
    # scales; the bare tokens differ but this is NOT a contradiction (no
    # fabricated cross-scale conversion). It is UNSUPPORTED by this gate.
    checks = _deterministic_claim_checks(
        "Net debt is $3 billion in FY2024.", ["ev-1"],
        {"ev-1": "Net debt was 3000 million in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


def test_quarterly_vs_annual_period_is_not_contradiction():
    # Claim asserts Q3 2024; evidence asserts FY2024 -> different period, not a
    # conflicting assertion about the same period -> UNSUPPORTED.
    checks = _deterministic_claim_checks(
        "Revenue rose 5% in Q3 2024.", ["ev-1"],
        {"ev-1": "Revenue rose 8% in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


def test_revenue_growth_vs_ebitda_growth_not_conflated():
    # The alias map resolves 'revenue grew' to revenue_growth, never EBITDA, so a
    # claim about EBITDA growth does not borrow the revenue canonical datum.
    assert _identify_canonical_metric("Revenue grew 10% in FY2024.") == "revenue_growth"
    assert _identify_canonical_metric("EBITDA grew 10% in FY2024.") is None


# ===========================================================================
# V7 — final numerical evidence binding corrections (Problems A/B/C/D).
#
# These adversarial cases exercise the ACTUAL Validator 7 functions and cover
# both SUCCESSFUL matching (genuine support/contradiction preserved) and
# UNSUCCESSFUL matching (false support / false contradiction eliminated). The
# governing principle: a number in a cited passage is not sufficient — it must
# be demonstrably associated with the SAME financial assertion (metric, entity,
# period, basis, units).
# ===========================================================================


# -- Problem A1: a value bound to an UNRELATED metric must not verify ----------


def test_v7_A1_net_debt_number_does_not_verify_revenue_growth():
    # Passage: 'Revenue grew 8%, while net debt was 12 million'. The claimed 12%
    # revenue growth must NOT be verified by the net-debt 12 (a different metric),
    # and because revenue actually grew 8% the claim is CONTRADICTED on revenue.
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={
            "ev-1": "Revenue grew 8% in FY2024, while net debt was 12 million."})
    assert obs.disposition.value == "rejected"
    assert any("contradicted" in r.casefold() for r in obs.validation_reasons)


def test_v7_A1_binding_isolates_the_relevant_metric_value():
    # Direct binding check: in the mixed passage only '8' is bound to revenue
    # growth; the net-debt '12' is NOT in the revenue-growth value set.
    bound = _metric_bound_values(
        "Revenue grew 8% in FY2024, while net debt was 12 million.",
        "revenue_growth")
    assert "8" in bound
    assert "12" not in bound


def test_v7_A1_net_debt_claim_binds_to_net_debt_value_only():
    # The complementary binding: a net-debt claim binds to the net-debt 12, not
    # the revenue 8. A net-debt claim of 50 therefore conflicts with 12 (same
    # metric) and is CONTRADICTED; the revenue 8 is irrelevant.
    bound = _metric_bound_values(
        "Revenue grew 8% in FY2024, while net debt was 12 million.", "net_debt")
    assert "12" in bound and "8" not in bound
    checks = _deterministic_claim_checks(
        "Net debt was 50 million in FY2024.", ["ev-1"],
        {"ev-1": "Revenue grew 8% in FY2024, while net debt was 12 million."})
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


# -- Problem A2: identical value, different metric -> UNSUPPORTED --------------


def test_v7_A2_identical_value_different_metric_is_unsupported():
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={"ev-1": "EBITDA grew 12% in FY2024."})
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"
    assert not any("contradicted" in r.casefold() for r in obs.validation_reasons)


# -- Problem B: a reporting YEAR must not be a conflicting financial value ------


def test_v7_B_year_token_is_not_a_financial_value():
    # 'Revenue increased during FY2024' states no growth %. The FY2024 year must
    # not be read as a figure that conflicts with the claimed 12% -> UNSUPPORTED.
    obs, _ = _govern(
        _management(hypothesis="Revenue grew 12% in FY2024.",
                    credit_mechanism="Top-line growth supports debt service."),
        evidence_texts={"ev-1": "Revenue increased during FY2024."})
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"
    assert not any("contradicted" in r.casefold() for r in obs.validation_reasons)


def test_v7_B_period_year_tokens_are_identified():
    # The 2024 inside FY2024 is a period token, excluded from the value set.
    assert "2024" in _period_value_tokens("Revenue increased during FY2024.")
    # A genuine four-digit amount NOT part of a period phrase is NOT excluded.
    assert "3000" not in _period_value_tokens("Net debt was 3000 in FY2024.")


def test_v7_B_four_digit_financial_amount_still_compared():
    # A legitimate four-digit amount (3000) is still a comparable value: a claim
    # of 9000 conflicts with the cited 3000 for the same metric/period.
    checks = _deterministic_claim_checks(
        "Net debt was 9000 in FY2024.", ["ev-1"],
        {"ev-1": "Net debt was 3000 in FY2024."})
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


# -- Problem C: cross-period canonical comparison is not a contradiction -------


def test_v7_C_fy2023_claim_vs_fy2024_canonical_is_unsupported():
    canonical = {"net_debt": {
        "value": 150.0, "raw_value": 150.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(hypothesis="Consolidated net debt was 100 million in FY2023.",
                    credit_mechanism="Historical leverage read-across."),
        evidence_texts={"ev-1": "Consolidated net debt was 100 million in FY2023."},
        canonical_metrics=canonical)
    assert obs.disposition.value in {"unverified", "qualified"}
    assert obs.disposition.value != "rejected"


def test_v7_C_cross_period_canonical_checks_are_not_contradiction():
    canonical = {"net_debt": {
        "value": 150.0, "raw_value": 150.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "consolidated"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Consolidated net debt was 100 million in FY2023.",
        canonical_metrics=canonical)
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


# -- Problem D: different named subsidiaries are not comparable ----------------


def test_v7_D_different_subsidiary_without_entity_id_is_not_contradicted():
    # Canonical subsidiary datum carries NO entity identifier -> identity cannot
    # be established, so a value difference is UNSUPPORTED, not a contradiction.
    canonical = {"net_debt": {
        "value": 500.0, "raw_value": 500.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "subsidiary"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Subsidiary A reported net debt of 100 million in FY2024.",
        canonical_metrics=canonical)
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


def test_v7_D_mismatched_entity_id_is_not_contradicted():
    # The canonical datum identifies Subsidiary B; the claim is about Subsidiary
    # A. Different legal entities -> NOT comparable -> UNSUPPORTED.
    comparable, reason = _scope_comparable(
        "Subsidiary A reported net debt of 100 million in FY2024.",
        "subsidiary", datum_entity_id="subsidiary-b", datum_entity_name="Subsidiary B")
    assert comparable is False
    assert "same legal entity" in reason or "different subsidiaries" in reason


def test_v7_D_matching_entity_id_is_comparable():
    # When the claim explicitly identifies the SAME subsidiary the datum records,
    # the subsidiary-level comparison IS admissible (genuine detection preserved).
    comparable, _ = _scope_comparable(
        "Subsidiary B reported net debt of 100 million in FY2024.",
        "subsidiary", datum_entity_id="subsidiary-b", datum_entity_name="Subsidiary B")
    assert comparable is True


def test_v7_D_consolidated_datum_does_not_contradict_subsidiary_claim():
    # A consolidated/group figure must never auto-contradict an individual
    # subsidiary claim (keep correct consolidated-metric handling).
    canonical = {"net_debt": {
        "value": 5000.0, "raw_value": 5000.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "consolidated"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Subsidiary A reported net debt of 100 million in FY2024.",
        canonical_metrics=canonical)
    failed = [c for c in checks if not c.passed]
    assert not any(c.contradiction for c in failed)


# -- Problem C/D preserved detection: same entity + period IS contradicted -----


def test_v7_same_entity_same_period_subsidiary_conflict_is_contradicted():
    # Same named subsidiary (identified on the datum) and same FY2024 period, a
    # conflicting value -> genuine CONTRADICTION is preserved.
    canonical = {"net_debt": {
        "value": 500.0, "raw_value": 500.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "USD", "reporting_scope": "subsidiary",
        "entity_id": "subsidiary-b", "entity_name": "Subsidiary B"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Subsidiary B reported net debt of 100 million in FY2024.",
        canonical_metrics=canonical)
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


# -- several metrics/years/values in one passage: bind to the relevant only ----


def test_v7_multi_metric_passage_binds_only_the_relevant_metric():
    passage = ("Revenue grew 12% in FY2024; operating cash flow was 250 million; "
               "net debt was 3000 million.")
    assert _metric_bound_values(passage, "revenue_growth") == {"12"}
    assert "3000" in _metric_bound_values(passage, "net_debt")
    # A revenue claim of 20% conflicts only with the revenue 12, not the other
    # figures.
    checks = _deterministic_claim_checks(
        "Revenue grew 20% in FY2024.", ["ev-1"], {"ev-1": passage})
    failed = [c for c in checks if not c.passed]
    assert failed and any(c.contradiction for c in failed)


# -- numeric outcome correct but causal attribution unsupported ---------------


def test_v7_correct_number_but_unsupported_causal_attribution_is_capped():
    canonical = {"revenue_growth": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    obs, governed = _govern(
        _management(
            hypothesis="Consolidated revenue grew 12% in FY2024 because of management's strategy.",
            credit_mechanism="Growth attributed to management execution."),
        evidence_texts={"ev-1": "Consolidated revenue grew 12% in FY2024."},
        canonical_metrics=canonical)
    # The numeric outcome verifies, but the causal attribution is capped at human
    # review (qualified), never auto-promoted to supported.
    assert obs.disposition.value != "supported"
    assert any(i.rule_id == "m3.requires_human_review"
               for i in governed.validation_issues)


# -- currency mismatch without supported conversion ---------------------------


def test_v7_currency_mismatch_same_metric_period_is_not_contradicted():
    canonical = {"net_debt": {
        "value": 5000.0, "raw_value": 5000.0, "units": "currency", "period": "FY2024",
        "accounting_basis": None, "currency": "SGD", "reporting_scope": "consolidated"}}
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Consolidated net debt was 100 million USD in FY2024.",
        canonical_metrics=canonical)
    failed = [c for c in checks if not c.passed]
    assert failed
    assert not any(c.contradiction for c in failed)


# -- percentage-point vs percentage confusion is not auto-verified -------------


def test_v7_percentage_point_confusion_is_not_auto_verified():
    canonical = {"operating_margin": {
        "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
        "accounting_basis": None, "currency": None, "reporting_scope": "consolidated"}}
    obs, _ = _govern(
        _management(
            hypothesis="Operating margin improved 2% (2 percentage points) in FY2024.",
            credit_mechanism="Margin expansion supports debt service."),
        evidence_texts={"ev-1": "Operating margin improved in FY2024."},
        canonical_metrics=canonical)
    assert obs.disposition.value != "supported"


# -- metric alias outside the known vocabulary -> conservative, not fabricated -


def test_v7_unknown_metric_alias_is_conservative_not_fabricated():
    # 'gross margin' is outside the alias vocabulary -> no canonical match and no
    # metric binding; the claim stays conservative (not falsely verified).
    assert _identify_canonical_metric("Gross margin was 40% in FY2024.") is None
    assert _metric_bound_values("Gross margin was 40% in FY2024.", "operating_margin") == set()
    checks = _canonical_financial_checks(
        {"supporting_evidence_ids": ["ev-1"]},
        "Gross margin was 40% in FY2024.",
        canonical_metrics={"operating_margin": {
            "value": 12.0, "raw_value": 0.12, "units": "ratio", "period": "FY2024",
            "accounting_basis": None, "currency": None,
            "reporting_scope": "consolidated"}})
    # No recognised metric in the claim -> no canonical check fabricated.
    assert checks == ()
