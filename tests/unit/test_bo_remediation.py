"""Quality-remediation acceptance tests (prompt/schema, BO1-BO6, recovery).

Deterministic: no model, no database. Several cases are the failure modes seen
in the first live HCA run, rebuilt on fictional text.
"""

from __future__ import annotations

import pytest

from app.prompts.bo_prompts import SHARED_RULES, bo_prompt_catalogue, specialist_block
from app.schemas.bo import Admissibility, ClaimType, Disposition
from app.services.bo import inference_rules
from app.services.bo.hypotheses import (
    HypothesisValidator,
    derive_evidence_basis,
    gate_parameters,
)
from app.services.bo.indicators import build_indicators
from app.services.bo.plans import (
    BO_AGENT_SPECS,
    BO_SPEC_BY_CODE,
    PARAMETER_VALUE_SETS,
)
from app.services.bo.recovery import recover_quote, tolerant_match
from app.services.bo.retrieval import BoEvidenceRetriever, RetrievalBudget
from app.services.bo.sectors import HEALTHCARE, detect_sector, load_case_checks
from tests.bo_helpers import evidence_snapshot, metrics

PASSAGES = {
    "p_rates": "Our managed care agreements set negotiated reimbursement rates and are "
               "typically renegotiated every one to three years.",
    "p_unit": "Revenue per equivalent admission increased 4.0% in 2025 compared to 2024.",
    "p_er": "Emergency room visits increased 1.6% during 2025 compared to 2024.",
    "p_networks": "Health plans increasingly utilize narrow networks that restrict the "
                  "number of participating providers.",
    "p_aim": "We strive to be the health care system of choice in the communities we serve.",
    "p_keyman": "The loss of key management personnel could adversely affect our business.",
    "p_acq": "We intend to selectively pursue acquisitions in our existing markets.",
    "p_train": "We operate a nursing college to expand our training pipeline.",
    "p_payer": "Medicare accounted for 20% of our revenues for the year ended December 31, 2025.",
    "p_ipc": "At December 31, 2025 estimated implicit price concessions of $7.7 billion had "
             "been recorded to adjust our revenues to the amounts we expect to collect.",
    "p_geo": "Our Florida and Texas facilities' combined revenues represented 51% of our "
             "consolidated revenues for the year ended 49 December 31, 2025. This "
             "concentration makes us sensitive to conditions in those states.",
    "p_labor": "Salaries and benefits increased during 2025 as we raised wages.",
    "p_reimb": "Reimbursement rates under government programs are set by regulation.",
    "p_debt": "We cannot guarantee we will maintain cash flows sufficient to pay our "
              "indebtedness.",
}
CORPUS = [{"evidence_id": k, "text": v, "section": "Item 1", "location": i * 2000,
           "document_id": "doc", "sec_accession": "0000000001-26-000001"}
          for i, (k, v) in enumerate(PASSAGES.items())]


def _validate(code, hypotheses, *, primary=None, packet=None, parameters=()):
    packet = PASSAGES if packet is None else packet
    validator = HypothesisValidator(
        BO_SPEC_BY_CODE[code], passages=packet,
        indicators=build_indicators(evidence_snapshot(), metrics(), analysis_run_id="AR"),
        primary_hypotheses=primary, corpus=CORPUS)
    return validator.validate({"parameters": list(parameters), "hypotheses": hypotheses},
                              analysis_run_id="AR", agent_run_id="RUN")


def _h(hid, claim, evidence, quote, **over):
    base = {"hypothesis_id": hid, "claim": claim, "claim_type": "fact",
            "direction": "negative", "disposition": "supported",
            "supporting_evidence_ids": [evidence] if evidence else [],
            "evidence_quotes": ([{"evidence_id": evidence, "quote": quote}]
                                if quote else [])}
    return {**base, **over}


def _flags(record):
    return [f["rule_id"] for f in record.review_flags]


# -- prompt / schema -------------------------------------------------------------


def test_each_specialist_prompt_names_only_its_own_parameters():
    catalogue = {e["name"]: e["template"] for e in bo_prompt_catalogue()}
    every = {p for spec in BO_AGENT_SPECS for p in spec.owned_parameters}
    for spec in BO_AGENT_SPECS:
        prompt = catalogue[spec.agent_id]
        assert all(p in prompt for p in spec.owned_parameters)
        assert not [p for p in every - set(spec.owned_parameters) if p in prompt]
        assert f"'{spec.code}-H001'" in prompt
        assert prompt == specialist_block(spec) + "\n" + SHARED_RULES


def test_shared_rules_are_identical_and_carry_no_specialist_field():
    every = {p for spec in BO_AGENT_SPECS for p in spec.owned_parameters}
    assert not [p for p in every if p in SHARED_RULES]
    assert not [spec.code for spec in BO_AGENT_SPECS if spec.code in SHARED_RULES]
    catalogue = [e["template"] for e in bo_prompt_catalogue()[:6]]
    assert all(t.endswith(SHARED_RULES) for t in catalogue)
    assert len({t for t in catalogue}) == 6


def test_every_parameter_has_a_permitted_value_set_or_label_rule():
    from app.services.bo.plans import LABEL_PARAMETERS
    from app.services.bo.synthesis import RUBRIC_SOURCES

    for spec in BO_AGENT_SPECS:
        for parameter in spec.owned_parameters:
            assert (parameter in PARAMETER_VALUE_SETS or parameter in RUBRIC_SOURCES
                    or parameter in LABEL_PARAMETERS), parameter
    assert all("not_established" in v or "none_supported" in v or "none_identified" in v
               for v in PARAMETER_VALUE_SETS.values())


def _gate(parameter_id, value, records, sets=None):
    parsed = {"parameters": [{
        "parameter_id": parameter_id, "value": value, "value_type": "category",
        "status": "ok", "evidence_ids": ["p_rates"],
        "hypothesis_ids": [r.hypothesis_id for r in records]}]}
    sets = sets or {k: list(v) for k, v in PARAMETER_VALUE_SETS.items()}
    adjusted, changes = gate_parameters(parsed, records, value_sets=sets)
    return adjusted["parameters"][0], changes


def test_free_text_classification_is_rejected_not_accepted():
    records = _validate("BO5", [_h("BO5-H001", "Medicare accounted for 20% of revenues "
                                   "in 2025.", "p_payer", PASSAGES["p_payer"][:60])]).records
    item, changes = _gate(
        "regulatory_dependency_assessment",
        "High dependency on Medicare, Medicaid, and state-level licensure programs.",
        records)
    assert item["status"] == "unavailable" and item["value"] is None
    assert changes[0]["reason"] == "value_not_permitted"
    ok, none = _gate("regulatory_dependency_assessment", "high", records)
    assert ok["status"] == "ok" and none == []
    label, changed = _gate("business_model_classification",
                           "The company is a large operator of hospitals.", records)
    assert changed[0]["reason"] == "label_not_concise"


def test_not_established_is_recorded_as_unavailable_not_forced():
    item, changes = _gate("barriers_to_entry_assessment", "not_established", [])
    assert item["status"] == "unavailable" and changes[0]["reason"] == "not_established"


# -- BO1 -------------------------------------------------------------------------


def test_rate_agreement_is_not_a_volume_guarantee():
    out = _validate("BO1", [
        _h("BO1-H001", "Revenue is contractually secured through long-term agreements "
           "that guarantee patient volumes.", "p_rates", PASSAGES["p_rates"][:70],
           direction="positive"),
        _h("BO1-H002", "Managed care agreements set reimbursement rates but do not "
           "guarantee patient volumes or revenue.", "p_rates", PASSAGES["p_rates"][:70]),
    ])
    wrong, right = out.records
    assert "rate_agreement_to_volume_guarantee" in _flags(wrong)
    assert wrong.admissibility is Admissibility.AUDIT_ONLY and not wrong.usable
    assert _flags(right) == [] and right.admissibility is Admissibility.ESTABLISHED


def test_one_year_of_volume_growth_does_not_establish_recession_resistance():
    out = _validate("BO1", [_h(
        "BO1-H001", "Demand is recession-resistant because emergency visits increased "
        "1.6% in 2025.", "p_er", PASSAGES["p_er"], claim_type="inference",
        direction="positive")])
    record = out.records[0]
    assert "essential_service_to_recession_resistance" in _flags(record)
    assert not record.usable
    qualified = _validate("BO1", [_h(
        "BO1-H001", "Emergency visits increased 1.6% in 2025; one year of growth does "
        "not establish recession resistance.", "p_er", PASSAGES["p_er"],
        claim_type="inference", direction="mixed", disposition="qualified")]).records[0]
    assert _flags(qualified) == [] and qualified.usable
    item, changes = _gate("demand_defensiveness_assessment", "demonstrated_resilient",
                          [qualified])
    assert changes[0]["reason"] == "needs_company_specific_evidence"


# -- BO2 -------------------------------------------------------------------------


def test_payer_category_share_is_not_counterparty_concentration():
    out = _validate("BO2", [_h(
        "BO2-H001", "The company depends on a concentrated group of government payers; "
        "Medicare accounted for 20% of revenues in 2025.", "p_payer",
        PASSAGES["p_payer"][:60])])
    record = out.records[0]
    assert "payer_category_share_to_counterparty_concentration" in _flags(record)
    assert record.disposition is Disposition.QUALIFIED
    item, changes = _gate("customer_dependence_assessment", "concentrated_counterparties",
                          [record])
    assert changes[0]["reason"] == "needs_company_specific_evidence"
    fine = _validate("BO2", [_h(
        "BO2-H001", "Medicare, a payer category and not an individual counterparty, "
        "accounted for 20% of revenues in 2025.", "p_payer",
        PASSAGES["p_payer"][:60])]).records[0]
    assert _flags(fine) == []


def test_implicit_price_concessions_are_not_read_as_incremental_cash_losses():
    quote = PASSAGES["p_ipc"][:80]
    loss = _validate("BO2", [_h(
        "BO2-H001", "Implicit price concessions show deteriorating collections and "
        "growing losses.", "p_ipc", quote)]).records[0]
    assert "accounting_estimate_to_cash_loss" in _flags(loss)
    assert loss.disposition is Disposition.QUALIFIED
    careful = _validate("BO2", [_h(
        "BO2-H001", "Implicit price concessions are a period-end accounting estimate; "
        "no trend is established from one amount.", "p_ipc", quote,
        direction="neutral")]).records[0]
    assert _flags(careful) == []


# -- BO3 -------------------------------------------------------------------------


def test_revenue_per_equivalent_admission_does_not_prove_pricing_power():
    wrong = _validate("BO3", [_h(
        "BO3-H001", "Revenue per equivalent admission increased 4.0%, demonstrating "
        "strong pricing power.", "p_unit", PASSAGES["p_unit"],
        direction="positive")]).records[0]
    assert "unit_revenue_growth_to_pricing_power" in _flags(wrong) and not wrong.usable
    right = _validate("BO3", [_h(
        "BO3-H001", "Revenue per equivalent admission increased 4.0%; pricing power "
        "cannot be separated from payer mix and case mix on this evidence.", "p_unit",
        PASSAGES["p_unit"], claim_type="inference", direction="mixed",
        disposition="qualified")]).records[0]
    assert _flags(right) == [] and right.usable


def test_narrow_networks_do_not_imply_low_switching_costs():
    wrong = _validate("BO3", [_h(
        "BO3-H001", "Switching costs are low due to narrow network structures.",
        "p_networks", PASSAGES["p_networks"][:60], claim_type="inference")]).records[0]
    assert "narrow_networks_to_switching_costs" in _flags(wrong) and not wrong.usable
    item, changes = _gate("switching_cost_assessment", "low", [wrong],
                          sets={"switching_cost_assessment": ["high", "low", "moderate"]})
    assert item["status"] == "unavailable"
    assert changes[0]["reason"] == "no_validated_supporting_hypothesis"


def test_company_aspiration_is_not_independent_competitive_evidence():
    record = _validate("BO3", [_h(
        "BO3-H001", "The company holds a strong competitive position through network "
        "density.", "p_aim", PASSAGES["p_aim"], direction="positive")]).records[0]
    assert record.claim_type is ClaimType.MANAGEMENT_CLAIM
    assert record.evidence_basis == "management_statement"
    assert record.admissibility is Admissibility.ATTRIBUTED
    item, changes = _gate("competitive_position_assessment", "strong", [record],
                          sets={"competitive_position_assessment": ["strong", "weak"]})
    assert changes[0]["reason"] == "needs_company_specific_evidence"


# -- BO4 -------------------------------------------------------------------------


def test_generic_key_person_disclosure_does_not_prove_high_dependency():
    record = _validate("BO4", [_h(
        "BO4-H001", "Key management dependency poses a material risk to continuity.",
        "p_keyman", PASSAGES["p_keyman"])]).records[0]
    assert record.evidence_basis == "generic_risk_disclosure"
    assert "generic_departure_risk_to_key_person_dependency" in _flags(record)
    assert record.admissibility is Admissibility.QUALIFIED_NARRATIVE
    item, changes = _gate("key_person_dependency_assessment", "high", [record],
                          sets={"key_person_dependency_assessment": ["high", "low"]})
    assert changes[0]["reason"] == "needs_company_specific_evidence"


def test_acquisition_intention_does_not_prove_capital_allocation_discipline():
    record = _validate("BO4", [_h(
        "BO4-H001", "The acquisition strategy is disciplined and successful.", "p_acq",
        PASSAGES["p_acq"], claim_type="inference", direction="positive")]).records[0]
    assert "stated_strategy_to_proven_discipline" in _flags(record)
    assert record.disposition is Disposition.QUALIFIED
    item, changes = _gate("acquisition_strategy_assessment", "outcomes_evidenced", [record])
    assert changes[0]["reason"] == "needs_company_specific_evidence"
    stated, none = _gate("acquisition_strategy_assessment", "stated_strategy_only", [record])
    assert stated["status"] == "ok" and none == []


def test_unmeasured_mitigant_remains_unproven():
    record = _validate("BO4", [_h(
        "BO4-H001", "A nursing college expands the training pipeline.", "p_train",
        PASSAGES["p_train"], direction="positive",
        mitigant={"capability": "Nursing college", "existence": "verified",
                  "effectiveness": "demonstrated", "outcome_evidence_ids": []})]).records[0]
    assert record.mitigant.existence == "verified"
    assert record.mitigant.effectiveness.value == "unproven"
    item, changes = _gate("workforce_cost_resilience_assessment", "mitigants_unproven",
                          [record])
    assert item["status"] == "ok"


# -- BO5: verification and targeted recovery --------------------------------------

CLEAN_GEO = ("Our Florida and Texas facilities' combined revenues represented 51% of our "
             "consolidated revenues for the year ended December 31, 2025.")


def test_layout_gap_quote_is_recovered_with_source_text_and_audit_trail():
    record = _validate("BO5", [_h(
        "BO5-H001", "Florida and Texas facilities generated 51% of consolidated revenues "
        "for the year ended December 31, 2025.", "p_geo", CLEAN_GEO)]).records[0]
    assert record.disposition is Disposition.SUPPORTED
    assert record.admissibility is Admissibility.ESTABLISHED
    quote = record.evidence_quotes[0]
    assert quote["recovered"] == "layout_gap" and "ended 49 December" in quote["quote"]
    audit = record.recovery[0]
    assert audit["original_quote"] == CLEAN_GEO and audit["attempts"] == 1
    assert audit["outcome"] == "layout_gap" and "49" in audit["detail"]
    assert audit["retrieval_request"]["terms"]
    assert record.model_stated["quotes"][0]["quote"] == CLEAN_GEO
    assert record.claim.startswith("Florida and Texas facilities generated 51%")


def test_quote_in_another_admitted_passage_is_relocated():
    record = _validate("BO5", [_h(
        "BO5-H001", "Reimbursement rates under government programmes are set by "
        "regulation.", "p_labor", PASSAGES["p_reimb"])]).records[0]
    assert record.recovery[0]["outcome"] == "relocated"
    assert record.evidence_quotes[0]["evidence_id"] == "p_reimb"
    assert "p_reimb" in record.supporting_evidence_ids
    assert record.admissibility is Admissibility.ESTABLISHED


def test_unrecoverable_evidence_never_becomes_a_fact():
    record = _validate("BO5", [_h(
        "BO5-H001", "Hurricanes closed several facilities for weeks.", "p_geo",
        "Hurricanes closed several of our facilities for a number of weeks in 2025.")]
    ).records[0]
    assert record.recovery[0]["outcome"] == "unresolved"
    assert record.recovery[0]["replacement_quote"] is None
    assert record.disposition is Disposition.UNVERIFIED and not record.usable
    assert record.evidence_quotes == []


def test_recovery_is_bounded_and_does_not_stitch_distant_words():
    assert tolerant_match("alpha beta gamma delta epsilon zeta",
                          "alpha beta gamma delta epsilon zeta") is not None
    spread = "alpha " + "x " * 20 + "beta gamma delta epsilon zeta"
    assert tolerant_match("alpha beta gamma delta epsilon zeta", spread) is None
    assert tolerant_match("too short", "too short quote") is None
    three_gaps = "a1 b2 9 c3 d4 9 e5 f6 9 g7 h8"
    assert tolerant_match("a1 b2 c3 d4 e5 f6 g7 h8", three_gaps) is None
    result = recover_quote(quote="alpha beta gamma delta epsilon zeta",
                           cited_evidence_id="p_geo", corpus=CORPUS)
    assert result["outcome"] == "unresolved" and result["attempts"] == 1


def test_existing_proceedings_are_distinguished_from_hypothetical_risk():
    assert derive_evidence_basis(
        quotes=["We are currently defendants in a number of lawsuits."],
        has_calculation=False, has_other_support=False) == "company_specific_disclosure"
    assert derive_evidence_basis(
        quotes=["We could be subject to investigations in the future."],
        has_calculation=False, has_other_support=False) == "generic_risk_disclosure"
    assert derive_evidence_basis(
        quotes=["We believe our compliance program is effective."],
        has_calculation=False, has_other_support=False) == "management_statement"
    record = _validate("BO5", [_h(
        "BO5-H001", "Regulatory change poses a material risk.", "p_keyman",
        PASSAGES["p_keyman"])]).records[0]
    assert "generic_risk_disclosure_only" in _flags(record)
    assert record.admissibility is Admissibility.QUALIFIED_NARRATIVE


# -- BO6 -------------------------------------------------------------------------


def _primary():
    labour = _validate("BO4", [_h(
        "BO4-H001", "Salaries and benefits increased during 2025.", "p_labor",
        PASSAGES["p_labor"])]).records[0]
    rates = _validate("BO2", [_h(
        "BO2-H001", "Reimbursement rates under government programmes are set by "
        "regulation.", "p_reimb", PASSAGES["p_reimb"])]).records[0]
    aim = _validate("BO3", [_h(
        "BO3-H001", "The company holds a strong position.", "p_aim", PASSAGES["p_aim"],
        direction="positive")]).records[0]
    rejected = _validate("BO1", [_h(
        "BO1-H001", "Demand is recession-proof.", None, None, claim_type="inference",
        direction="positive", disposition="rejected")]).records[0]
    return {r.hypothesis_id: r for r in (labour, rates, aim, rejected)}


def _bo6(claim, components, **over):
    base = {"hypothesis_id": "BO6-H001", "claim": claim, "claim_type": "inference",
            "direction": "negative", "disposition": "supported", "materiality": "medium",
            "supporting_evidence_ids": ["p_labor", "p_reimb"],
            "component_hypothesis_ids": components, "interaction_kind": "compound_risk",
            "transmission_mechanism": "Wage growth raises cost per case while regulated "
                                      "rates do not adjust at the same pace."}
    return {**base, **over}


def test_debt_capacity_extrapolation_is_held_back_and_handed_to_fo():
    primary = _primary()
    out = _validate("BO6", [_bo6(
        "Labour cost inflation with fixed reimbursement rates limits the ability to "
        "service debt, potentially triggering a liquidity crisis.",
        ["BO4-H001", "BO2-H001"])], primary=primary)
    record = out.records[0]
    assert "operating_pressure_to_debt_capacity" in _flags(record)
    assert record.admissibility is Admissibility.AUDIT_ONLY and not record.usable
    handoff = [h for h in record.fo_handoffs if h["origin"] == "validator"]
    assert handoff and handoff[0]["variable"] == "debt_service_capacity"


def test_margin_claim_with_an_explicit_fo_handoff_is_allowed():
    record = _validate("BO6", [_bo6(
        "Labour cost inflation with regulated reimbursement rates could compress "
        "operating margins; the effect on debt service is outside the Business "
        "Overview and is for the Financial Overview.", ["BO4-H001", "BO2-H001"],
        fo_handoff=[{"variable": "operating_margin_sensitivity",
                     "question": "Margin effect of wage growth above rate growth?"}])],
        primary=_primary()).records[0]
    assert _flags(record) == [] and record.usable
    assert record.fo_handoffs == [{
        "variable": "operating_margin_sensitivity",
        "question": "Margin effect of wage growth above rate growth?", "origin": "agent"}]


def test_component_ids_must_be_valid_and_rejected_findings_are_not_premises():
    primary = _primary()
    for bad in ("BO9-H001", "BO1-H001"):  # non-existent; rejected by its specialist
        out = _validate("BO6", [
            _bo6("Wage growth compounds the other finding.", ["BO4-H001", bad]),
            _bo6("Wage growth and regulated rates interact.", ["BO4-H001", "BO2-H001"],
                 hypothesis_id="BO6-H002")], primary=primary)
        broken, sound = out.records
        assert not broken.usable and bad not in broken.component_hypothesis_ids
        assert any(bad in n and "not a validated primary" in n
                   for n in broken.validation_notes)
        assert sound.usable  # one bad premise does not discard the other finding


def test_bo6_parameters_may_rest_on_validated_primary_hypotheses():
    """Regression from a live run: BO6 cited BO1-BO5 ids for its parameters."""
    primary = _primary()
    records = _validate("BO6", [_bo6("Wage growth and regulated rates interact.",
                                     ["BO4-H001", "BO2-H001"])], primary=primary).records
    parsed = {"parameters": [
        {"parameter_id": "compound_risk_assessment", "status": "ok",
         "value": "interaction_plausible_unquantified", "value_type": "category",
         "evidence_ids": ["p_labor"], "hypothesis_ids": ["BO4-H001", "BO2-H001"]},
        {"parameter_id": "mitigating_capability_assessment", "status": "ok",
         "value": "existing_unproven", "value_type": "category",
         "evidence_ids": ["p_labor"], "hypothesis_ids": ["BO1-H001"]}]}
    sets = {k: list(v) for k, v in PARAMETER_VALUE_SETS.items()}
    adjusted, changes = gate_parameters(parsed, records, value_sets=sets, primary=primary)
    assert adjusted["parameters"][0]["status"] == "ok"
    # ...but not on a primary hypothesis its own specialist rejected.
    assert [c["reason"] for c in changes] == ["no_validated_supporting_hypothesis"]
    without, changes = gate_parameters(parsed, records, value_sets=sets)
    assert len(changes) == 2


def test_bo6_cannot_upgrade_certainty_or_severity_of_its_inputs():
    primary = _primary()
    record = _validate("BO6", [_bo6(
        "Wage growth and the company's stated market position interact.",
        ["BO4-H001", "BO3-H001"], materiality="high")], primary=primary).records[0]
    assert record.disposition is Disposition.QUALIFIED  # BO3-H001 is a management claim
    assert record.materiality.value == "medium"  # capped at the components' level
    assert "rests_on_management_claim" in _flags(record)
    notes = " ".join(record.validation_notes)
    assert "cannot be more certain" in notes and "capped at medium" in notes


def test_bo6_interaction_needs_a_mechanism_and_adds_no_new_numbers():
    primary = _primary()
    bare = _validate("BO6", [_bo6("Labour and rates interact.", ["BO4-H001", "BO2-H001"],
                                  transmission_mechanism=None)], primary=primary).records[0]
    assert bare.disposition is Disposition.UNVERIFIED
    invented = _validate("BO6", [_bo6(
        "Labour costs rose 9.3% against fixed rates.", ["BO4-H001", "BO2-H001"])],
        primary=primary).records[0]
    assert invented.disposition is Disposition.UNVERIFIED
    assert any("9.3" in n for n in invented.validation_notes)


# -- inference rules are negation-aware and labelled heuristic --------------------


@pytest.mark.parametrize("claim", [
    "Agreements set rates; they do not guarantee patient volumes.",
    "No contract guarantees volumes or revenue.",
    "Debt service capacity is outside the Business Overview and is for the Financial Overview.",
    "Narrow networks do not by themselves establish switching costs.",
    "Demand is not recession-proof.",
])
def test_claims_that_deny_the_over_reading_are_not_flagged(claim):
    assert inference_rules.evaluate(claim=claim, mechanism=None, claim_type="fact",
                                    evidence_basis="inference") == []


def test_every_inference_flag_is_marked_heuristic_with_a_correction():
    flags = inference_rules.evaluate(
        claim="Contracts guarantee patient volumes and the company cannot service its debt.",
        mechanism=None, claim_type="inference", evidence_basis="inference")
    assert {f["rule_id"] for f in flags} == {
        "rate_agreement_to_volume_guarantee", "operating_pressure_to_debt_capacity"}
    assert all(f["heuristic"] and f["correction"] and f["layer"].startswith("C")
               for f in flags)


# -- indicators and coverage -----------------------------------------------------


def _sector_evidence():
    evidence = evidence_snapshot()

    def fact(fid, name, year, value, dims=None):
        return {"fact_id": fid, "name": name, "period_type": "FY", "fiscal_year": year,
                "normalized_value": value, "consolidation_scope": "consolidated",
                "dimensions": dims or {}}

    evidence.facts = [
        fact("f_lab", "us-gaap:LaborAndRelatedExpense", 2025, 430.0),
        fact("f_lab2", "us-gaap:LaborAndRelatedExpense", 2025, 430.0),  # repeated tag
        fact("f_sup", "us-gaap:SuppliesExpense", 2025, 150.0),
        fact("f_sup_x", "us-gaap:SuppliesExpense", 2025, 151.0),  # conflicting value
        fact("f_mc", "{x}RevenuesFromThirdPartyPayers", 2025, 200.0, {"a": "{x}MedicareMember"}),
        fact("f_mg", "{x}RevenuesFromThirdPartyPayers", 2025, 480.0,
             {"a": "{x}ManagedCareAndOtherInsurersMember"}),
        fact("f_ipc", "{x}EstimatedImplicitPriceConcessions", 2025, 76.0),
        fact("f_ipc0", "{x}EstimatedImplicitPriceConcessions", 2024, 80.0),
    ]
    return evidence


def test_sector_indicators_are_deterministic_traceable_and_never_estimated():
    indicators = build_indicators(_sector_evidence(), metrics(), analysis_run_id="AR",
                                  sector=HEALTHCARE)
    by_id = {t.parameter_id: t for t in indicators.traces}
    labour = by_id["labour_cost_to_revenue"]
    assert labour.value == 0.43 and labour.source_fact_ids[-1] == "f_rev"
    assert labour.denominator == {"name": "consolidated_revenue", "value": 1000.0}
    assert labour.formula_id == "safe_ratio" and labour.group == "workforce"
    assert "supplies_to_revenue" not in by_id  # conflicting facts are left out
    medicare = by_id["revenue_share.payer.medicare"]
    assert medicare.value == 0.2 and "CATEGORY" in medicare.caveat
    change = by_id["implicit_price_concessions.change"]
    assert change.value == -0.05 and change.formula_id == "growth_rate"
    assert "not an incremental cash loss" in change.caveat
    assert "labour_cost_to_revenue.prior" not in by_id  # no prior-year revenue admitted


def test_each_specialist_receives_a_curated_indicator_set():
    indicators = build_indicators(_sector_evidence(), metrics(), analysis_run_id="AR",
                                  sector=HEALTHCARE)

    def ids(code):
        return {i["parameter_id"] for i in indicators.agent_view(
            groups=BO_SPEC_BY_CODE[code].indicator_groups)}

    assert "labour_cost_to_revenue" in ids("BO4") and "labour_cost_to_revenue" not in ids("BO3")
    assert "revenue_share.payer.medicare" in ids("BO2")
    assert "revenue_share.payer.medicare" not in ids("BO4")
    assert "implicit_price_concessions.change" in ids("BO2")
    assert "capex_to_revenue" in ids("BO4") and "capex_to_revenue" not in ids("BO5")
    assert len({frozenset(ids(s.code)) for s in BO_AGENT_SPECS}) > 3  # not one packet for all
    extra = indicators.results[0].parameter_result_id
    assert extra in {i["parameter_result_id"] for i in indicators.agent_view(
        groups=("none",), include_ids={extra})}


def test_checklist_guarantees_passages_reach_the_assigned_specialist():
    corpus = evidence_snapshot().narrative_evidence
    retriever = BoEvidenceRetriever()
    spec = BO_SPEC_BY_CODE["BO3"]
    base = retriever.run_plan(spec, [], analysis_run_id="AR")
    result, located = retriever.run_checklist(spec, HEALTHCARE.checklist, corpus, base,
                                              analysis_run_id="AR")
    assert set(located) == {"hc.unit_revenue_vs_pricing", "hc.local_competition"}
    assert "ev_compete" in located["hc.unit_revenue_vs_pricing"]
    assert "ev_compete" in result.evidence_ids
    attempt = next(a for a in result.attempts if a.origin == "checklist")
    assert attempt.purpose == "coverage" and attempt.query_id.startswith("check:hc.")


def test_case_coverage_checks_are_data_and_state_no_conclusion(tmp_path):
    path = tmp_path / "checks.json"
    path.write_text('{"coverage_checks": [{"check_id": "case.entity_x", '
                    '"agent_codes": ["BO4"], "question": "What does the filing say about '
                    'Entity X?", "terms": ["Entity X"], "finding_patterns": ["Entity X"]}]}',
                    encoding="utf-8")
    check = load_case_checks(path)[0]
    assert check.origin == "case" and check.agent_codes == ("BO4",)
    assert check.as_prompt() == {"check_id": "case.entity_x",
                                 "question": "What does the filing say about Entity X?"}
    # The generic code base names no borrower-specific entity.
    import pathlib

    generic = "".join(p.read_text(encoding="utf-8") for p in
                      pathlib.Path("app/services/bo").glob("*.py"))
    generic += pathlib.Path("app/prompts/bo_prompts.py").read_text(encoding="utf-8")
    import re

    assert not re.search(r"\bGalen\b|\bHCA\b|\bStryker\b", generic)


def test_sector_profile_is_detected_from_evidence_not_from_a_name():
    assert detect_sector(evidence_snapshot()) is None  # too few matching passages
    evidence = evidence_snapshot()
    evidence.narrative_evidence = [
        {**p, "text": p["text"] + " Our hospitals treat Medicare patients."}
        for p in evidence.narrative_evidence]
    assert detect_sector(evidence) is HEALTHCARE


def test_a_verified_quote_does_not_make_an_unrelated_claim_an_established_fact():
    """Regression from the live HCA runs: a genuine quote attached to a claim it
    does not state."""
    unrelated = _validate("BO1", [_h(
        "BO1-H001", "Payer contracts only establish reimbursement levels, not volume "
        "commitments.", "p_er", PASSAGES["p_er"])]).records[0]
    assert "quote_does_not_cover_claim" in _flags(unrelated)
    assert unrelated.disposition is Disposition.QUALIFIED
    assert unrelated.admissibility is Admissibility.QUALIFIED_NARRATIVE
    flag = next(f for f in unrelated.review_flags
                if f["rule_id"] == "quote_does_not_cover_claim")
    assert flag["heuristic"] and flag["layer"].startswith("D")
    covered = _validate("BO1", [_h(
        "BO1-H001", "Emergency room visits increased 1.6% during 2025.", "p_er",
        PASSAGES["p_er"])]).records[0]
    assert _flags(covered) == [] and covered.admissibility is Admissibility.ESTABLISHED


# -- interpretation, quantification and results evidence ------------------------


def test_interpretation_is_kept_and_an_unbound_number_drops_only_that_field():
    record = _validate("BO1", [_h(
        "BO1-H001", "Medicare accounted for 20% of revenues in 2025.", "p_payer",
        PASSAGES["p_payer"], interpretation={
            "implication": "Government rate setting moves a fifth of revenue.",
            "magnitude": "Medicare was 20% of revenues in 2025.",
            "trend": "Expected to reach 35% by 2027.",
            "watch_trigger": None})]).records[0]
    assert record.usable and record.disposition is Disposition.SUPPORTED
    assert record.interpretation == {
        "implication": "Government rate setting moves a fifth of revenue.",
        "magnitude": "Medicare was 20% of revenues in 2025."}
    assert any("Interpretation trend dropped" in n for n in record.validation_notes)


def test_a_size_in_words_with_indicators_supplied_is_noted_not_downgraded():
    record = _validate("BO1", [_h(
        "BO1-H001", "Medicare accounted for a significant share of our revenues.",
        "p_payer", PASSAGES["p_payer"])]).records[0]
    assert "unquantified_magnitude" in _flags(record)
    flag = next(f for f in record.review_flags if f["rule_id"] == "unquantified_magnitude")
    assert flag["effect"] == "note" and flag["heuristic"]
    assert record.disposition is Disposition.SUPPORTED and record.usable
    hypothetical = _validate("BO1", [_h(
        "BO1-H001", "Medicare rate cuts could reduce our revenues.", "p_payer",
        PASSAGES["p_payer"], claim_type="inference")]).records[0]
    assert "unquantified_magnitude" not in _flags(hypothetical)


def test_indicators_no_hypothesis_binds_are_reported():
    out = _validate("BO1", [_h(
        "BO1-H001", "Medicare accounted for 20% of revenues in 2025.", "p_payer",
        PASSAGES["p_payer"])])
    assert any(a["hypothesis_id"] == "(output)" and "bound by no hypothesis" in a["note"]
               for a in out.adjustments)


def test_plan_query_adds_a_results_passage_without_displacing_its_top_matches():
    from app.services.bo.plans import BoAgentSpec, RetrievalQuery

    corpus = [
        {"evidence_id": "e_desc", "section": "Item 1", "location": 1,
         "text": "Our revenue comes from hospital services; revenue grew and admissions grew."},
        {"evidence_id": "e_mdna", "section": "Item 7", "location": 2,
         "text": "Revenue increased compared to the prior year, primarily due to volume."},
    ]
    spec = BoAgentSpec("BO1", "x", "t", (), (
        RetrievalQuery("x.q", "support", (r"revenue", r"grew", r"admissions"), 1),))
    result = BoEvidenceRetriever().run_plan(spec, corpus, analysis_run_id="AR")
    assert [r["evidence_id"] for r in result.attempts[0].results] == ["e_desc", "e_mdna"]
    assert set(result.evidence_ids) == {"e_desc", "e_mdna"}


def test_payor_spelling_and_adjusted_admissions_are_covered():
    import re

    from app.services.bo.roster_v3 import V3_SPEC_BY_CODE

    payor = "The following table presents the percentages of net operating revenues by payor source."
    unit = "On a same-store basis, net operating revenues per adjusted admission increased 4.0%."
    terms = {c.check_id: c.terms for c in HEALTHCARE.checklist}
    assert any(re.search(t, payor, re.I) for t in terms["hc.payer_mix"])
    assert any(re.search(t, unit, re.I) for t in terms["hc.unit_revenue_vs_pricing"])
    m1s2 = next(q for q in V3_SPEC_BY_CODE["M1S2"].plan
                if q.query_id == "m1s2.customer_payer_mix")
    assert any(re.search(t, "Revenues from our largest payors", re.I) for t in m1s2.terms)
    # v2 stays as recorded.
    assert any(t == r"payers?" for q in BO_SPEC_BY_CODE["BO2"].plan for t in q.terms)


def test_supplementary_passages_are_added_and_never_displace():
    from app.services.bo.plans import BoAgentSpec, RetrievalQuery

    corpus = [
        {"evidence_id": "e_old", "section": "Item 1", "location": 1,
         "candidate_topics": ["customer_concentration"],
         "text": "Revenue from customers is concentrated."},
        {"evidence_id": "e_new", "section": "Item 1", "location": 2,
         "candidate_topics": ["business_results"],
         "text": "Revenue from customers grew; revenue from customers rose."},
    ]
    spec = BoAgentSpec("BO1", "x", "t", (), (
        RetrievalQuery("x.q", "support", (r"revenue", r"customers", r"grew"), 1),))
    result = BoEvidenceRetriever().run_plan(spec, corpus, analysis_run_id="AR")
    assert [r["evidence_id"] for r in result.attempts[0].results] == ["e_old", "e_new"]
    tight = BoEvidenceRetriever(RetrievalBudget(max_passages_per_agent=1))
    assert tight.run_plan(spec, corpus, analysis_run_id="AR").evidence_ids == ["e_old"]


def test_with_validators_off_the_interpretation_is_kept_and_noted():
    validator = HypothesisValidator(
        BO_SPEC_BY_CODE["BO1"], passages=PASSAGES,
        indicators=build_indicators(evidence_snapshot(), metrics(), analysis_run_id="AR"),
        corpus=CORPUS, enforce=False)
    record = validator.validate({"parameters": [], "hypotheses": [_h(
        "BO1-H001", "Medicare accounted for a significant share of revenues.", "p_payer",
        PASSAGES["p_payer"], interpretation={"trend": "Expected to reach 35% by 2027."})]},
        analysis_run_id="AR", agent_run_id="RUN").records[0]
    assert record.interpretation == {"trend": "Expected to reach 35% by 2027."}
    assert any(n.startswith("Advisory: interpretation trend kept") for n in record.validation_notes)
    assert "unquantified_magnitude" in _flags(record)
