"""M1 — roster v3 contracts, validator identity, step 11 policy and adapter.

Specification: BO_ARCHITECTURE_V3_SPEC.md. Nothing here calls a model.
"""

from __future__ import annotations

import pytest

from app.prompts.bo_prompts import SHARED_RULES, bo_prompt_catalogue, bo_v3_prompt_catalogue
from app.schemas.agentic import (
    AcceptanceState,
    Materiality,
    Method,
    ParameterResult,
    ParameterStatus,
    Topic,
)
from app.schemas.bo import Admissibility, Disposition, DisplayPriority, HypothesisRecord
from app.schemas.llm import (
    ClaimClassification,
    CreditDirection,
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceAssessment,
    EvidenceAssessmentKind,
    GovernedHypothesisTestResult,
    HypothesisVerificationStatus,
)
from app.services.agents.registry import (
    AGENT_REGISTRY_VERSION_BO_V3,
    default_registry,
)
from app.services.bo.hypotheses import (
    HYPOTHESIS_VALIDATOR_VERSION,
    HypothesisStore,
    HypothesisValidator,
    validator_identity,
)
from app.services.bo.indicators import IndicatorSet
from app.services.bo.investigation import (
    DISPOSITION_TO_STATUS,
    STATUS_TO_DISPOSITION,
    InvestigationResult,
    corpus_from_packet,
    strictest,
    to_bo_items,
)
from app.services.bo.plans import BO_AGENT_SPECS
from app.services.bo.presentation import PRESENTATION_POLICY_VERSION, classify_presentation
from app.services.bo.roster_v3 import (
    BO_V2_TO_V3_PARAMETER_OWNER,
    V3_AGENT_6_ID,
    V3_M1_PRIMARY_IDS,
    V3_NOT_EXECUTED_IDS,
    V3_PRIMARY_AGENT_IDS,
    V3_SPEC_BY_AGENT,
    V3_SPEC_BY_CODE,
    V3_SPECS,
    not_assessed_parameter_results,
    v3_coverage_checks,
)
from app.services.bo.sectors import SECTOR_PROFILES

# Registry hashes of the untouched baseline (feat/bo-quality-remediation 0ccb429).
V1_HASH = "e16f08ad710d85e0eadb1af3e2226860e26ca898799ca1c424485f738a55869e"
V2_HASH = "8d5b997bcf9832237473f53a47b63415f43809e09d4e4ee7aa52a708f275ad82"


# -- ownership ---------------------------------------------------------------


def test_earlier_rosters_are_unchanged():
    assert default_registry("v1").registry_hash() == V1_HASH
    assert default_registry("v2").registry_hash() == V2_HASH


def test_v3_registry_builds_with_unique_ownership():
    registry = default_registry("v3")  # AgentRegistry validates uniqueness
    assert registry.version == AGENT_REGISTRY_VERSION_BO_V3
    assert len(registry) == 30
    for spec in V3_SPECS:
        for parameter in spec.owned_parameters:
            assert registry.owner_of(parameter) == spec.agent_id


def test_every_v2_parameter_keeps_its_id_and_has_one_v3_owner():
    v2 = sorted(p for s in BO_AGENT_SPECS for p in s.owned_parameters)
    v3 = sorted(p for s in V3_SPECS for p in s.owned_parameters)
    assert v2 == v3
    assert sorted(BO_V2_TO_V3_PARAMETER_OWNER) == v2


def test_review_decisions_of_17_october_are_implemented():
    owner = {p: new for p, (_, new) in BO_V2_TO_V3_PARAMETER_OWNER.items()}
    assert owner["supplier_dependence_assessment"] == "m1_operating_model"
    assert owner["regulatory_dependency_assessment"] == "m2_industry"
    assert owner["external_event_exposure_assessment"] == "m2_industry"
    assert owner["litigation_risk_assessment"] == "m3_corporate_management"
    assert owner["material_event_assessment"] == "m3_corporate_management"
    for p in ("management_quality_assessment", "key_person_dependency_assessment",
              "governance_concern_assessment"):
        assert owner[p] == "m3_corporate_management"
    for p in ("acquisition_strategy_assessment", "integration_risk_assessment",
              "capex_strategy_assessment"):
        assert owner[p] == "m4_strategy_capital"


def test_module_1_has_five_primaries_and_agent_6_after_them():
    assert len(V3_M1_PRIMARY_IDS) == 5
    assert [V3_SPEC_BY_AGENT[a].dimension for a in V3_M1_PRIMARY_IDS] == [
        "revenue_quality_demand_defensiveness", "customer_stickiness_concentration",
        "pricing_power_revenue_realisation", "operating_model_cost_resilience",
        "competitive_durability"]
    agent_6 = default_registry("v3").get(V3_AGENT_6_ID)
    assert set(agent_6.dependencies) == set(V3_M1_PRIMARY_IDS)


def test_eight_primary_agents_have_eight_separate_validators():
    validators = [validator_identity(V3_SPEC_BY_AGENT[a]) for a in V3_PRIMARY_AGENT_IDS]
    assert validators == [f"validator_{n}" for n in range(1, 9)]
    assert validator_identity(V3_SPEC_BY_AGENT[V3_AGENT_6_ID]) == "agent6_governance"
    assert V3_AGENT_6_ID not in V3_PRIMARY_AGENT_IDS


def test_dimension_values_match_the_imported_credit_dimensions():
    known = {d.value for d in CreditDimension}
    for code in ("M1S1", "M1S2", "M1S3", "M1S4", "M1S5"):
        assert V3_SPEC_BY_CODE[code].dimension in known
    # Financial stewardship belongs to Module 4, never to a Module 1 agent.
    assert all(s.dimension != "financial_stewardship_capital_commitments"
               for s in V3_SPECS if s.module == "M1")


def test_geographic_evidence_is_shared_but_not_owned_by_customer_stickiness():
    m1s2 = V3_SPEC_BY_CODE["M1S2"]
    assert "external_event_exposure_assessment" not in m1s2.owned_parameters
    assert any(q.query_id == "m1s2.geographic_context" for q in m1s2.plan)
    checks = {c.check_id: c for c in
              v3_coverage_checks(SECTOR_PROFILES["healthcare_provider"].checklist)}
    assert checks["hc.geography"].agent_codes == ("M2",)
    assert checks["hc.revenue_realisation"].agent_codes == ("M1S3",)


def test_case_coverage_checks_follow_the_code_map():
    from app.services.bo.sectors import load_case_checks  # noqa: PLC0415

    galen = v3_coverage_checks(
        load_case_checks("examples/healthcare/hca-2025/coverage_checks.json"))[0]
    assert galen.agent_codes == ("M1S4",)


# -- modules 2-4: executable with exclusive ownership --------------------------


def test_modules_2_to_4_are_executable_and_prompted():
    registry = default_registry("v3")
    names = {e["name"] for e in bo_v3_prompt_catalogue()}
    assert V3_NOT_EXECUTED_IDS == ()
    for agent_id in ("m2_industry", "m3_corporate_management", "m4_strategy_capital"):
        definition = registry.get(agent_id)
        assert definition.task_type == agent_id
        assert definition.prompt_name == agent_id and definition.routing is not None
        assert agent_id in names


def test_no_v3_parameter_is_preemptively_not_assessed():
    results = not_assessed_parameter_results("ar_test")
    assert results == []


def test_a_not_assessed_result_cannot_carry_a_value_or_a_run():
    base = dict(parameter_result_id="x", analysis_run_id="ar",
                parameter_id="management_quality_assessment", topic=Topic.BUSINESS,
                value_type="category", method=Method.LLM,
                status=ParameterStatus.NOT_ASSESSED, agent_id="m3_corporate_management")
    ParameterResult(**base)
    with pytest.raises(ValueError):
        ParameterResult(**base, value="strong")
    with pytest.raises(ValueError):
        ParameterResult(**base, agent_run_id="invented_run")


# -- prompts -----------------------------------------------------------------


def test_v3_prompts_are_specialist_blocks_plus_unchanged_shared_rules():
    v2_names = {e["name"] for e in bo_prompt_catalogue()}
    for entry in bo_v3_prompt_catalogue():
        assert entry["name"] not in v2_names  # v2 prompt versions never alternate
        if entry["name"] in V3_SPEC_BY_AGENT:  # specialist prompts
            assert entry["template"].endswith(SHARED_RULES)
    for code in ("M1S1", "M1S2", "M1S3", "M1S4", "M1S5", "M1A6"):
        assert code not in SHARED_RULES


# -- step 11 presentation policy ------------------------------------------------


@pytest.mark.parametrize("disposition, admissibility, materiality, expected", [
    (Disposition.REJECTED, Admissibility.AUDIT_ONLY, Materiality.HIGH, "suppress"),
    (Disposition.UNVERIFIED, Admissibility.AUDIT_ONLY, Materiality.HIGH, "on_demand"),
    (Disposition.UNVERIFIED, Admissibility.AUDIT_ONLY, Materiality.LOW, "suppress"),
    (Disposition.SUPPORTED, Admissibility.ESTABLISHED, Materiality.HIGH, "display"),
    (Disposition.QUALIFIED, Admissibility.QUALIFIED_NARRATIVE, Materiality.MEDIUM,
     "display"),
    (Disposition.SUPPORTED, Admissibility.ESTABLISHED, Materiality.LOW, "on_demand"),
])
def test_presentation_policy_table(disposition, admissibility, materiality, expected):
    decision = classify_presentation(disposition=disposition, admissibility=admissibility,
                                     materiality=materiality)
    assert decision.presentation.value == expected
    assert decision.as_record()["policy_version"] == PRESENTATION_POLICY_VERSION


def test_unresolved_material_matter_becomes_an_open_question_not_a_finding():
    d = classify_presentation(disposition=Disposition.UNVERIFIED,
                              admissibility=Admissibility.AUDIT_ONLY,
                              materiality=Materiality.HIGH)
    assert d.presentation is DisplayPriority.ON_DEMAND and d.open_question


def test_superseded_findings_are_suppressed_whatever_their_status():
    d = classify_presentation(disposition=Disposition.SUPPORTED,
                              admissibility=Admissibility.ESTABLISHED,
                              materiality=Materiality.HIGH,
                              acceptance_state=AcceptanceState.SUPERSEDED)
    assert d.presentation is DisplayPriority.SUPPRESS


def test_model_label_is_recorded_but_never_decides():
    for proposal in ("display", "on_demand", "suppress"):
        d = classify_presentation(disposition=Disposition.REJECTED,
                                  admissibility=Admissibility.AUDIT_ONLY,
                                  materiality=Materiality.HIGH, model_proposed=proposal)
        assert d.presentation is DisplayPriority.SUPPRESS
        assert d.basis["model_proposed"] == proposal


PASSAGE = ("Medicare and Medicaid accounted for 41% of our revenues in 2025, and "
           "we cannot assure that payment rates will not decline.")


def _validator(**kwargs):
    return HypothesisValidator(V3_SPEC_BY_CODE["M1S2"], passages={"e1": PASSAGE},
                               indicators=IndicatorSet(), **kwargs)


def _item(**overrides):
    item = {"hypothesis_id": "M1S2-H001",
            "claim": "Government programmes accounted for 41% of revenues in 2025.",
            "claim_type": "fact", "direction": "negative", "disposition": "supported",
            "materiality": "high", "supporting_evidence_ids": ["e1"],
            "evidence_quotes": [{"evidence_id": "e1", "quote": PASSAGE}],
            "display_priority": "suppress"}
    item.update(overrides)
    return item


def test_v3_validator_stamps_identity_and_applies_the_policy():
    out = _validator(apply_presentation_policy=True).validate(
        {"hypotheses": [_item()]}, analysis_run_id="ar", agent_run_id="run")
    assert not out.issues
    record = out.records[0]
    assert record.validator_id == "validator_2"
    assert record.validator_version == HYPOTHESIS_VALIDATOR_VERSION
    expected = classify_presentation(
        disposition=record.disposition, admissibility=record.admissibility,
        materiality=record.materiality).presentation
    assert record.display_priority is expected
    assert record.presentation_basis["model_proposed"] == "suppress"
    assert record.presentation_basis["materiality_basis"] == "model_proposed"


def test_v2_behaviour_is_kept_when_the_policy_is_off():
    out = _validator().validate({"hypotheses": [_item(display_priority="suppress")]},
                                analysis_run_id="ar", agent_run_id="run")
    assert out.records[0].display_priority is DisplayPriority.SUPPRESS
    assert out.records[0].presentation_basis == {}


def test_suppressed_findings_remain_in_the_store(db_session):
    from app.models.orm import AgenticAnalysisRun as RunRow  # noqa: PLC0415

    db_session.add(RunRow(analysis_run_id="ar", case_id="C", evidence_snapshot_version=1,
                          router_version="r", agent_registry_version="v3",
                          agent_registry_hash="h", scoring_config_version=1,
                          scoring_config_hash="h"))
    db_session.flush()
    out = _validator(apply_presentation_policy=True).validate(
        {"hypotheses": [_item(disposition="rejected", claim_type="inference")]},
        analysis_run_id="ar", agent_run_id="run")
    record = out.records[0]
    assert record.display_priority is DisplayPriority.SUPPRESS
    store = HypothesisStore(db_session, case_id="C", snapshot_version=1)
    store.persist(record)
    reloaded = [HypothesisRecord.model_validate(row.payload) for row in store.rows("ar")]
    assert [r.hypothesis_id for r in reloaded] == ["M1S2-H001"]
    assert reloaded[0].presentation_basis["rule"] == "rejected"


# -- schema compatibility -----------------------------------------------------------


def test_records_written_before_v3_still_load():
    old_payload = {
        "hypothesis_id": "BO2-H001", "analysis_run_id": "ar", "agent_id":
        "bo_customers_contracts", "agent_run_id": "run", "section": "BO2",
        "claim": "c", "claim_type": "fact", "direction": "negative",
        "disposition": "supported", "admissibility": "established"}
    record = HypothesisRecord.model_validate(old_payload)
    assert record.validator_id is None and record.investigation == {}
    again = HypothesisRecord.model_validate(record.model_dump(mode="json"))
    assert again == record


# -- adapter -------------------------------------------------------------------


def test_status_vocabularies_map_one_to_one():
    assert set(STATUS_TO_DISPOSITION) == set(HypothesisVerificationStatus)
    assert {DISPOSITION_TO_STATUS[d] for d in Disposition} == set(HypothesisVerificationStatus)
    assert STATUS_TO_DISPOSITION[HypothesisVerificationStatus.PARTIALLY_SUPPORTED] \
        is Disposition.QUALIFIED


def test_combining_judgements_keeps_the_strictest():
    assert strictest(Disposition.SUPPORTED, Disposition.QUALIFIED) is Disposition.QUALIFIED
    assert strictest(Disposition.QUALIFIED, Disposition.UNVERIFIED) is Disposition.UNVERIFIED
    assert strictest(Disposition.UNVERIFIED, Disposition.REJECTED) is Disposition.REJECTED


def test_corpus_keeps_baseline_evidence_ids_and_only_packet_passages():
    corpus = corpus_from_packet([
        {"evidence_id": "ev_a", "document_id": "10-K", "text": "alpha"},
        {"evidence_id": "ev_b", "document_id": "10-K", "text": "beta"},
        {"evidence_id": None, "text": "no id"}])
    assert [p.passage_id for p in corpus.passages] == ["ev_a", "ev_b"]
    assert corpus.document_ids == ["10-K"]


def _investigation(effective, *, corroborated, assessments):
    h = CreditHypothesis.model_construct(
        hypothesis_id="h1", dimension=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
        business_characteristic="b", statement="Rate agreements guarantee revenue.",
        credit_direction=CreditDirection.POSITIVE, economic_reasoning="r",
        potential_credit_impact="i", offsetting_characteristics=[],
        supporting_evidence_ids=[], contradictory_evidence_ids=[],
        missing_information=[], classification=ClaimClassification.INFERENCE)
    g = GovernedHypothesisTestResult.model_construct(
        hypothesis_id="h1", dimension=h.dimension, hypothesis_statement=h.statement,
        proposed_status=HypothesisVerificationStatus.SUPPORTED, effective_status=effective,
        downgrade_reasons=[], factual_grounding=[], evidence_assessments=assessments,
        mechanism_premises=[], alternative_explanations=[],
        unresolved_evidence_requirements=[], independently_corroborated=corroborated,
        model_only_support=not corroborated, uncorroborated_material_premises=[],
        interpretation_confidence=0.8, human_review_required=False)
    result = InvestigationResult(spec_code="M1S2")
    result.hypotheses = CreditHypothesisSet.model_construct(hypotheses=[h])
    result.governed = [g]
    result.id_map = {"h1": "M1S2-H001"}
    return result


def test_genuine_quote_without_corroboration_cannot_be_supported():
    quote = EvidenceAssessment.model_construct(
        passage_id="e1", assessment=EvidenceAssessmentKind.SUPPORTS_PREMISE,
        rationale="r", quoted_excerpt=PASSAGE)
    items = to_bo_items(_investigation(HypothesisVerificationStatus.SUPPORTED,
                                       corroborated=False, assessments=[quote]),
                        V3_SPEC_BY_CODE["M1S2"])
    assert items[0]["disposition"] == "qualified"
    assert "supported_without_deterministic_corroboration" in items[0]["_adapter_notes"]


def test_adapter_keeps_contradicting_evidence_and_the_governed_status():
    contra = EvidenceAssessment.model_construct(
        passage_id="e2", assessment=EvidenceAssessmentKind.CONTRADICTS_PREMISE,
        rationale="r", quoted_excerpt="no volume commitment")
    items = to_bo_items(_investigation(HypothesisVerificationStatus.REJECTED,
                                       corroborated=False, assessments=[contra]),
                        V3_SPEC_BY_CODE["M1S2"])
    assert items[0]["disposition"] == "rejected"
    assert items[0]["contradicting_evidence_ids"] == ["e2"]
    assert items[0]["evidence_quotes"] == [
        {"evidence_id": "e2", "quote": "no volume commitment"}]
