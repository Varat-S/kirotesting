"""Acceptance C, D, E (unit level) — evidence validation, hypothesis logic,
retrieval, indicators and cross-agent reconciliation. No LLM, no database."""

from __future__ import annotations

from app.schemas.bo import (
    Admissibility,
    ClaimType,
    Confidence,
    Direction,
    Disposition,
    HypothesisRecord,
)
from app.services.bo.hypotheses import HypothesisValidator, derive_admissibility
from app.services.bo.indicators import build_indicators
from app.services.bo.plans import BO_SPEC_BY_CODE
from app.services.bo.reconcile import reconcile
from app.services.bo.retrieval import BoEvidenceRetriever, RetrievalBudget
from tests.bo_helpers import PASSAGES, PERIOD, TX_SHARE, evidence_snapshot, metrics

TEXT = {eid: text for eid, (_, text) in PASSAGES.items()}


def _indicators():
    return build_indicators(evidence_snapshot(), metrics(), analysis_run_id="AR")


def _validate(code, hypotheses, *, indicators=None, primary=None, parameters=()):
    validator = HypothesisValidator(
        BO_SPEC_BY_CODE[code], passages=TEXT, indicators=indicators or _indicators(),
        primary_hypotheses=primary)
    return validator.validate(
        {"parameters": list(parameters), "hypotheses": hypotheses},
        analysis_run_id="AR", agent_run_id="RUN")


def _h(hid, **over):
    base = {"hypothesis_id": hid, "claim": "Revenues are seasonal.",
            "claim_type": "fact", "direction": "negative", "disposition": "supported",
            "supporting_evidence_ids": ["ev_season"],
            "evidence_quotes": [{"evidence_id": "ev_season",
                                 "quote": "Our revenues are seasonal"}]}
    return {**base, **over}


# -- C. evidence validation ------------------------------------------------------


def test_supported_fact_with_verbatim_quote_is_established():
    out = _validate("BO1", [_h("BO1-H001")])
    assert out.valid and out.records[0].admissibility is Admissibility.ESTABLISHED


def test_unknown_evidence_id_rejects_the_whole_output():
    out = _validate("BO1", [_h("BO1-H001"),
                            _h("BO1-H002", supporting_evidence_ids=["ev_missing"])])
    assert not out.valid and out.records == []
    assert any("unknown/unadmitted evidence 'ev_missing'" in i for i in out.issues)


def test_fabricated_quote_leaves_the_fact_unverified():
    out = _validate("BO1", [_h("BO1-H001", evidence_quotes=[
        {"evidence_id": "ev_season", "quote": "Our revenues are never seasonal"}])])
    record = out.records[0]
    assert record.disposition is Disposition.UNVERIFIED
    assert record.admissibility is Admissibility.AUDIT_ONLY
    assert not record.usable and record.evidence_quotes == []


def test_fact_without_any_quote_is_unverified():
    out = _validate("BO1", [_h("BO1-H001", evidence_quotes=[])])
    assert out.records[0].disposition is Disposition.UNVERIFIED


def test_management_belief_is_not_accepted_as_fact():
    out = _validate("BO4", [_h(
        "BO4-H001", claim="The nursing college improves recruitment and retention.",
        direction="positive", supporting_evidence_ids=["ev_labor"],
        evidence_quotes=[{"evidence_id": "ev_labor",
                          "quote": "which we believe will improve recruitment and "
                                   "retention"}])])
    record = out.records[0]
    assert record.claim_type is ClaimType.MANAGEMENT_CLAIM
    assert record.admissibility is Admissibility.ATTRIBUTED
    assert any("management belief" in n for n in record.validation_notes)


def test_unbound_number_is_unverified_and_quoted_number_is_accepted():
    invented = _validate("BO2", [_h(
        "BO2-H001", claim="Medicare and Medicaid accounted for 61% of revenues.",
        supporting_evidence_ids=["ev_payer"],
        evidence_quotes=[{"evidence_id": "ev_payer",
                          "quote": "Medicare and Medicaid accounted for 45% of our "
                                   "revenues"}])])
    assert invented.records[0].disposition is Disposition.UNVERIFIED
    disclosed = _validate("BO2", [_h(
        "BO2-H001", claim="Medicare and Medicaid accounted for 45% of revenues.",
        supporting_evidence_ids=["ev_payer"],
        evidence_quotes=[{"evidence_id": "ev_payer",
                          "quote": "Medicare and Medicaid accounted for 45% of our "
                                   "revenues"}])])
    assert disclosed.records[0].disposition is Disposition.SUPPORTED


def _quote(indicators, **over):
    result = next(r for r in indicators.results if r.parameter_id == TX_SHARE)
    entry = {"parameter_result_id": result.parameter_result_id, "value": result.value,
             "unit": "ratio", "period": PERIOD, "denominator": "consolidated_revenue"}
    return {**entry, **over}


def _calc(indicators, **over):
    return _h("BO5-H001", claim="Texas generated 30% of consolidated revenue.",
              claim_type="calculated_fact", supporting_evidence_ids=["ev_geo"],
              evidence_quotes=[], quoted_values=[_quote(indicators, **over)])


def test_calculated_fact_bound_to_a_deterministic_result_is_established():
    indicators = _indicators()
    out = _validate("BO5", [_calc(indicators)], indicators=indicators)
    assert out.valid and out.records[0].admissibility is Admissibility.ESTABLISHED
    assert out.records[0].parameter_result_ids


def test_wrong_denominator_is_rejected():
    """An admissions share must not be presented as a revenue share."""
    indicators = _indicators()
    out = _validate("BO5", [_calc(indicators, denominator="total_admissions")],
                    indicators=indicators)
    assert not out.valid
    assert any("denominator 'total_admissions'" in i for i in out.issues)


def test_wrong_period_and_mismatched_value_and_invented_result_are_rejected():
    indicators = _indicators()
    assert not _validate("BO5", [_calc(indicators, period="2024-12-31")],
                         indicators=indicators).valid
    wrong = _validate("BO5", [_calc(indicators, value=0.52)], indicators=indicators)
    assert any("no LLM arithmetic" in i for i in wrong.issues)
    invented = _validate("BO5", [_calc(indicators, parameter_result_id="pr_fake")],
                         indicators=indicators)
    assert any("invented number" in i for i in invented.issues)


def test_score_like_fields_reject_the_output():
    out = _validate("BO1", [_h("BO1-H001", risk_score=1)])
    assert any("official_score_assignment" in i for i in out.issues)


def test_malformed_and_duplicate_hypothesis_ids_are_rejected():
    assert not _validate("BO1", [_h("BO2-H001")]).valid
    assert not _validate("BO1", [_h("BO1-H001"), _h("BO1-H001")]).valid


# -- D. hypothesis logic ---------------------------------------------------------


def test_supported_with_counter_evidence_becomes_qualified_and_keeps_it():
    out = _validate("BO1", [_h("BO1-H001", contradicting_evidence_ids=["ev_model"])])
    record = out.records[0]
    assert record.disposition is Disposition.QUALIFIED
    assert record.contradicting_evidence_ids == ["ev_model"]
    assert record.admissibility is Admissibility.QUALIFIED_NARRATIVE


def test_rejected_hypothesis_is_retained_and_links_to_its_accepted_correction():
    out = _validate("BO5", [
        _h("BO5-H001", claim="A broad footprint eliminates concentration risk.",
           claim_type="inference", direction="positive", disposition="rejected",
           supporting_evidence_ids=[], evidence_quotes=[],
           contradicting_evidence_ids=["ev_geo"]),
        _h("BO5-H002", claim="Two states generate approximately 52% of revenues.",
           supporting_evidence_ids=["ev_geo"], corrects_hypothesis_id="BO5-H001",
           evidence_quotes=[{"evidence_id": "ev_geo",
                             "quote": "approximately 52% of our consolidated "
                                      "revenues"}]),
    ])
    rejected, corrected = out.records
    assert rejected.disposition is Disposition.REJECTED and not rejected.usable
    assert rejected.direction is Direction.POSITIVE  # direction survives rejection
    assert corrected.disposition is Disposition.SUPPORTED
    assert corrected.corrects_hypothesis_id == "BO5-H001"


def test_unverified_is_never_established_or_displayed():
    out = _validate("BO1", [_h("BO1-H001", disposition="unverified",
                               display_priority="display")])
    record = out.records[0]
    assert record.admissibility is Admissibility.AUDIT_ONLY
    assert record.display_priority.value == "on_demand"


def test_confidence_and_direction_are_independent_of_validity():
    low = _validate("BO1", [_h("BO1-H001", confidence="low", direction="positive")])
    assert low.records[0].disposition is Disposition.SUPPORTED
    assert low.records[0].confidence is Confidence.LOW
    assert low.records[0].direction is Direction.POSITIVE
    for claim_type in ClaimType:
        for disposition in Disposition:
            # Admissibility is a function of type and disposition only.
            assert derive_admissibility(claim_type, disposition) in Admissibility


def test_mitigating_initiative_is_not_an_effective_mitigant_without_an_outcome():
    out = _validate("BO4", [_h(
        "BO4-H001", claim="The nursing college expands the training pipeline.",
        direction="positive", supporting_evidence_ids=["ev_labor"],
        evidence_quotes=[{"evidence_id": "ev_labor",
                          "quote": "We operate a College of Nursing to expand our "
                                   "training pipeline"}],
        mitigant={"capability": "Nursing college", "existence": "verified",
                  "effectiveness": "demonstrated", "outcome_evidence_ids": []})])
    mitigant = out.records[0].mitigant
    assert mitigant.existence == "verified"
    assert mitigant.effectiveness.value == "unproven"


def test_a_finding_can_be_both_favourable_and_adverse():
    out = _validate("BO2", [_h(
        "BO2-H001", claim="Contracts fix rates but do not guarantee volumes.",
        direction="mixed", supporting_evidence_ids=["ev_payer"],
        evidence_quotes=[{"evidence_id": "ev_payer",
                          "quote": "the contracts do not guarantee minimum volumes "
                                   "of patients"}])])
    assert out.records[0].direction is Direction.MIXED
    assert out.records[0].disposition is Disposition.SUPPORTED


def _record(hid, agent, claim, direction="negative", evidence=("ev_geo",),
            disposition=Disposition.SUPPORTED, claim_type=ClaimType.FACT):
    return HypothesisRecord(
        hypothesis_id=hid, analysis_run_id="AR", agent_id=agent, agent_run_id="R",
        section=hid[:3], claim=claim, claim_type=claim_type,
        direction=Direction(direction), supporting_evidence_ids=list(evidence),
        disposition=disposition,
        admissibility=derive_admissibility(claim_type, disposition))


def test_bo6_interaction_needs_validated_primary_components():
    primary = {"BO4-H001": _record("BO4-H001", "bo_operations_management", "Labour."),
               "BO5-H001": _record("BO5-H001", "bo_regulation_external", "Geography.")}
    item = {"hypothesis_id": "BO6-H001", "claim": "Labour and geography compound.",
            "claim_type": "inference", "direction": "negative",
            "disposition": "qualified", "supporting_evidence_ids": ["ev_geo"],
            "interaction_kind": "compound_risk",
            "component_hypothesis_ids": ["BO4-H001", "BO5-H001"]}
    good = _validate("BO6", [item], primary=primary)
    assert good.valid and good.records[0].component_hypothesis_ids
    ghost = _validate("BO6", [{**item, "component_hypothesis_ids": ["BO4-H009"]}],
                      primary=primary)
    assert any("not a validated primary hypothesis" in i for i in ghost.issues)
    single = _validate("BO6", [{**item, "component_hypothesis_ids": ["BO4-H001"]}],
                       primary=primary)
    assert single.records[0].disposition is Disposition.UNVERIFIED


# -- E. reconciliation -----------------------------------------------------------


def test_duplicate_findings_from_two_agents_are_grouped():
    a = _record("BO2-H001", "bo_customers_contracts",
                "Revenue depends heavily on government programmes Medicare and Medicaid.")
    b = _record("BO5-H001", "bo_regulation_external",
                "Revenue depends heavily on government programmes Medicare Medicaid "
                "reimbursement.")
    c = _record("BO4-H001", "bo_operations_management", "Nursing shortage raises cost.",
                evidence=("ev_labor",))
    result = reconcile([a, b, c])
    assert len(result.duplicate_groups) == 1
    group = result.duplicate_groups[0]
    assert group["hypothesis_ids"] == ["BO2-H001", "BO5-H001"]
    assert group["shared_evidence_ids"] == ["ev_geo"]
    assert result.group_of("BO4-H001") is None
    assert [o["evidence_id"] for o in result.overlapping_evidence] == ["ev_geo"]


def test_conflicting_agent_assessments_are_contradictions_not_duplicates():
    a = _record("BO1-H001", "bo_business_model_revenue",
                "Geographic footprint across many states diversifies revenue.",
                direction="positive")
    b = _record("BO5-H001", "bo_regulation_external",
                "Geographic footprint concentrates revenue in two states.")
    result = reconcile([a, b])
    assert result.duplicate_groups == []
    assert result.contradictions[0]["kind"] == "opposite_direction"
    assert result.contradictions[0]["hypothesis_ids"] == ["BO1-H001", "BO5-H001"]


def test_supported_versus_rejected_and_disconfirming_evidence_are_surfaced():
    supported = _record("BO3-H001", "bo_competition_pricing",
                        "Scale provides superior negotiated pricing power.",
                        direction="positive")
    rejected = _record("BO1-H009", "bo_business_model_revenue",
                       "Scale provides superior negotiated pricing power.",
                       direction="positive", disposition=Disposition.REJECTED,
                       claim_type=ClaimType.INFERENCE)
    result = reconcile([supported], [rejected])
    assert result.contradictions[0]["kind"] == "supported_vs_rejected"
    counter = supported.model_copy(update={"contradicting_evidence_ids": ["ev_compete"]})
    assert reconcile([counter]).disconfirming[0]["hypothesis_id"] == "BO3-H001"


# -- retrieval -------------------------------------------------------------------


def _passages():
    return list(evidence_snapshot().narrative_evidence)


def test_plan_retrieval_is_bidirectional_bounded_and_located():
    spec = BO_SPEC_BY_CODE["BO4"]
    result = BoEvidenceRetriever().run_plan(spec, _passages(), analysis_run_id="AR")
    purposes = {a.purpose for a in result.attempts}
    assert purposes == {"support", "contradict", "mitigant", "mitigant_challenge"}
    assert len(result.attempts) == len(spec.plan)
    labour = next(p for p in result.passages if p["evidence_id"] == "ev_labor")
    assert {"contradict", "mitigant", "mitigant_challenge"} <= set(labour["retrieved_for"])
    reference = result.attempts[0].results[0]
    assert {"evidence_id", "document_id", "section", "location"} <= set(reference)


def test_missing_evidence_is_a_recorded_gap_not_a_silent_omission():
    only = [p for p in _passages() if p["evidence_id"] == "ev_mgmt"]
    result = BoEvidenceRetriever().run_plan(
        BO_SPEC_BY_CODE["BO4"], only, analysis_run_id="AR")
    gaps = {a.query_id for a in result.gaps}
    assert "bo4.workforce" in gaps and "bo4.management" not in gaps
    assert all(a.status == "no_results" and a.results == [] for a in result.gaps)


def test_evidence_budget_caps_the_packet():
    budget = RetrievalBudget(max_passages_per_agent=2, max_chars_per_agent=100_000)
    result = BoEvidenceRetriever(budget).run_plan(
        BO_SPEC_BY_CODE["BO2"], _passages(), analysis_run_id="AR")
    assert len(result.passages) == 2


def test_follow_up_is_bounded_and_searches_admitted_passages_only():
    retriever = BoEvidenceRetriever(RetrievalBudget(max_followup_requests=1))
    spec = BO_SPEC_BY_CODE["BO3"]
    first = retriever.run_plan(spec, [p for p in _passages()
                                      if p["evidence_id"] == "ev_compete"],
                               analysis_run_id="AR")
    requests = [{"purpose": "contradict", "terms": ["uninsured"]},
                {"purpose": "support", "terms": ["hurricanes"]}]
    second = retriever.follow_up(spec, requests, _passages(), first,
                                 analysis_run_id="AR")
    assert [a.origin for a in second.attempts] == ["agent_request"]  # 1 of 2 honoured
    assert "ev_uninsured" in second.evidence_ids and "ev_geo" not in second.evidence_ids
    assert set(second.evidence_ids) <= set(PASSAGES)


def test_instruction_like_passage_is_flagged_as_data():
    result = BoEvidenceRetriever().run_plan(
        BO_SPEC_BY_CODE["BO2"], _passages(), analysis_run_id="AR")
    flagged = {p["evidence_id"] for p in result.passages if p["instruction_like"]}
    assert flagged == {"ev_inject"}


# -- indicators ------------------------------------------------------------------


def test_indicators_carry_full_calculation_metadata():
    indicators = _indicators()
    trace = next(t for t in indicators.traces if t.parameter_id == TX_SHARE)
    assert trace.value == 0.3 and trace.units == "ratio" and trace.period == PERIOD
    assert trace.numerator["value"] == 300.0
    assert trace.denominator == {"name": "consolidated_revenue", "value": 1000.0,
                                 "fact_id": "f_rev"}
    assert trace.source_fact_ids == ["f_tx", "f_rev"]
    assert trace.formula_id == "safe_ratio" and trace.definition_version
    assert trace.calculation_state == "ok"
    result = next(r for r in indicators.results if r.parameter_id == TX_SHARE)
    assert result.method.value == "deterministic" and result.agent_run_id is None


def test_cash_from_operations_less_capex_is_not_labelled_debt_service_cash():
    trace = next(t for t in _indicators().traces if t.parameter_id == "free_cash_flow")
    assert trace.label == "Cash from operations less capital expenditure"
    assert "NOT contractual cash available for debt service" in trace.caveat
    assert trace.denominator is None


def test_incomplete_breakdown_is_not_offered_as_a_concentration_fact():
    evidence = evidence_snapshot()
    drop = next(k for k in evidence.data_quality if "OtherStates" in k)
    del evidence.data_quality[drop]
    indicators = build_indicators(evidence, {}, analysis_run_id="AR")
    hhi = next(r for r in indicators.results if r.parameter_id.startswith("revenue_hhi"))
    assert hhi.status.value == "requires_review"
    assert hhi.parameter_result_id not in indicators.known_values()
    # Shares still use the consolidated denominator.
    assert next(t for t in indicators.traces if t.parameter_id == TX_SHARE).value == 0.3
