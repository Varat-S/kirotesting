"""Material Evidence Completeness tests (Stage 12, Task 8).

Deterministic, offline. Builds the Task 7 CreditFindingSet via the real
CreditSignalAssessmentService on hand-built governed results, and (for offset
scenarios) a real CandidateEvidenceBundle via the Task 4/5 retrieval+coordinator.

Scope honesty: these prove STRUCTURAL completeness logic (statuses, gaps,
progression, dimension coverage) -- NOT that the system can discover an unknown
company-specific insight. Two industries used (manufacturer; subscription SaaS).
"""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.services.ingestion.completeness import CompletenessAssessment
from app.schemas.llm import (
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceAssessment,
    EvidenceEnquiryPlan,
    EvidenceSufficiency,
    GapCategory,
    GovernedHypothesisTestResult,
    InvestigationStatus,
    Materiality,
    OffsetInvestigationStatus,
    PremiseCorroboration,
    PremiseStatus,
    ProgressionRecommendation,
)
from app.services.analysis.credit_signal_assessment import (
    CreditSignalAssessmentInputs,
    CreditSignalAssessmentService,
    MaterialityInputs,
)
from app.services.analysis.enquiry_planning import EvidenceEnquiryCoordinator
from app.services.analysis.evidence_completeness import (
    COMPLETENESS_POLICY_VERSION,
    DimensionNonApplicability,
    EvidenceCompletenessInputs,
    EvidenceCompletenessRejectedError,
    MaterialEvidenceCompletenessService,
)
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_pdf,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _hyp(hid="h1", direction="negative",
         dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
         characteristic="Dependence on scarce technicians.", offsets=None):
    return CreditHypothesis(
        hypothesis_id=hid, dimension=dim, business_characteristic=characteristic,
        statement=f"{characteristic} affects resilience.", credit_direction=direction,
        economic_reasoning="m", potential_credit_impact="i", classification="inference",
        verification_status="unverified", offsetting_characteristics=offsets or [],
    )


def _premise(pid, corroborated, *, outcome=False):
    return PremiseCorroboration(
        premise_id=pid, premise=pid, material=True, is_outcome_premise=outcome,
        model_status=PremiseStatus.VERIFIED, corroborated=corroborated,
        corroboration_basis=["deterministic_grounding"] if corroborated else [],
    )


def _governed(hid="h1", *, status="unverified",
              dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
              statement="Dependence on scarce technicians. affects resilience.",
              assessments=None, premise_corr=None, uncorroborated=None,
              unresolved=None, human_review=False, run_id="run-1"):
    return GovernedHypothesisTestResult(
        hypothesis_id=hid, dimension=dim, hypothesis_statement=statement,
        proposed_status=status, effective_status=status,
        evidence_assessments=assessments or [], premise_corroboration=premise_corr or [],
        uncorroborated_material_premises=uncorroborated or [],
        unresolved_evidence_requirements=unresolved or [],
        human_review_required=human_review, testing_run_id=run_id,
    )


def _findings(hyps, governed, materiality_inputs=None):
    return CreditSignalAssessmentService().assess(
        CreditSignalAssessmentInputs(
            hypotheses=CreditHypothesisSet(hypotheses=hyps), governed=governed,
            materiality_inputs=materiality_inputs or {},
        )
    ).findings


def _assess(hyps, governed, *, materiality_inputs=None, plan=None, bundle=None,
            source_package=None, not_applicable=None):
    findings = _findings(hyps, governed, materiality_inputs)
    service = MaterialEvidenceCompletenessService(case_id="C1")
    return service.assess(EvidenceCompletenessInputs(
        findings=findings, hypotheses=CreditHypothesisSet(hypotheses=hyps),
        governed=governed, plan=plan, bundle=bundle, source_package=source_package,
        case_id="C1", not_applicable_dimensions=not_applicable or {},
    )).assessment


_HIGH = {"h1": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True)}


# -- synthetic bundle for offset scenarios --------------------------------


def _pdf(pages):
    buf = io.BytesIO()
    c = Canvas(buf)
    for lines in pages:
        y = 800
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


def _enq(hid, kind, intent, query, eid, doc="MFG"):
    return {
        "enquiry_id": eid, "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "requirement_kind": kind, "search_intent": intent,
        "investigative_question": "?", "search_query": query, "rationale": "r",
        "evidence_requirement": "req", "priority": 2, "permitted_document_ids": [doc],
    }


def _plan_and_bundle(offset_query="technical training academy", pages=None):
    pages = pages or [
        ["S1", "The company faces a shortage of skilled machining technicians."],
        ["S3", "The company operates a technical training academy for technicians."],
    ]
    plan = EvidenceEnquiryPlan.model_validate({
        "enquiries": [
            _enq("h1", "supporting", "supporting_evidence",
                 "shortage skilled technicians", "e_s"),
            _enq("h1", "contradictory", "contradictory_evidence", "retention", "e_c"),
            _enq("h1", "offsetting", "offsetting_capability", offset_query, "e_o"),
        ],
        "planner_mode": "llm", "incomplete_coverage": [],
    })
    corpus = build_corpus_from_pdf("MFG", _pdf(pages))
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)
    return plan, bundle


# ===========================================================================
# STRUCTURAL COMPLETENESS LOGIC
# ===========================================================================


def test_material_finding_adequate_investigation_and_evidence(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[
                      EvidenceAssessment(passage_id="s", assessment="supports_premise", rationale="r"),
                      EvidenceAssessment(passage_id="c", assessment="contradicts_premise", rationale="r"),
                  ])
    # Contradiction present means not SUPPORTED-clean; use a clean supported one.
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    plan, bundle = _plan_and_bundle()
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    fa = a.finding_completeness[0]
    assert fa.supporting_investigated is True
    assert fa.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT_FOR_PRELIMINARY_ANALYSIS


def test_supporting_but_no_contradictory_investigation(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    # Plan with ONLY supporting + offsetting executed (no contradictory).
    plan = EvidenceEnquiryPlan.model_validate({
        "enquiries": [
            _enq("h1", "supporting", "supporting_evidence", "technicians", "e_s"),
            _enq("h1", "offsetting", "offsetting_capability", "training academy", "e_o"),
        ], "planner_mode": "llm", "incomplete_coverage": [],
    })
    corpus = build_corpus_from_pdf("MFG", _pdf([
        ["S1", "shortage of skilled technicians"],
        ["S3", "a training academy for technicians"]]))
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    cats = {gap.category for gap in a.material_gaps}
    assert GapCategory.NO_CONTRADICTORY_INVESTIGATION in cats


def test_material_negative_no_offset_enquiry(db_session):
    hyp = _hyp(direction="negative")  # no named offset
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)  # no bundle -> no offset enquiry
    fa = a.finding_completeness[0]
    assert fa.offset_status is OffsetInvestigationStatus.NOT_INVESTIGATED
    assert any(gap.category is GapCategory.OFFSET_NOT_INVESTIGATED
               for gap in a.material_gaps)


def test_offset_search_executed_no_match(db_session):
    hyp = _hyp(direction="negative")  # no named offset
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])
    plan, bundle = _plan_and_bundle(offset_query="zzzxylophonequarknonsense")
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    fa = a.finding_completeness[0]
    # Executed but no match != not investigated, and != 'absent'.
    assert fa.offset_status is OffsetInvestigationStatus.INVESTIGATED_NO_MATCH


def test_offset_exists_but_effectiveness_uncertain(db_session):
    hyp = _hyp(direction="negative", offsets=["Technical training academy."])
    g = _governed(status="partially_supported",
                  premise_corr=[_premise("exists", True),
                                _premise("effect", False, outcome=True)],
                  uncorroborated=["effect"])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    fa = a.finding_completeness[0]
    assert fa.offset_status is OffsetInvestigationStatus.EFFECTIVENESS_UNVERIFIED
    assert any(gap.category is GapCategory.OFFSET_EFFECTIVENESS_UNVERIFIED
               for gap in a.material_gaps)


def test_uncorroborated_material_premise_blocks(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified", uncorroborated=["key premise"])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    assert any(gap.category is GapCategory.UNCORROBORATED_MATERIAL_PREMISE
               and gap.blocks_progression for gap in a.material_gaps)
    assert a.progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE


def test_unresolved_contradiction_blocks(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported",
                  assessments=[EvidenceAssessment(passage_id="c",
                                                  assessment="contradicts_premise", rationale="r")])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    assert any(gap.category is GapCategory.UNRESOLVED_CONTRADICTION
               for gap in a.material_gaps)
    assert a.progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE


def test_source_missing_blocks_progression(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    plan, bundle = _plan_and_bundle()
    source = CompletenessAssessment(
        complete=False, mandatory_escalation=True, reduced_coverage=True,
        missing_critical=["audited_financials"], profile_version=1)
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle,
                source_package=source)
    assert a.source_package_complete is False
    assert a.progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE


def test_source_complete_but_investigation_incomplete(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])
    source = CompletenessAssessment(
        complete=True, mandatory_escalation=False, reduced_coverage=False,
        profile_version=1)
    # No bundle -> offset not investigated -> investigation PARTIAL.
    a = _assess([hyp], [g], materiality_inputs=_HIGH, source_package=source)
    assert a.source_package_complete is True
    assert a.overall_investigation_status is InvestigationStatus.PARTIAL


def test_investigation_complete_but_evidence_insufficient(db_session):
    hyp = _hyp(direction="negative")
    # All perspectives investigated, but a material premise is uncorroborated.
    g = _governed(status="unverified", uncorroborated=["premise"])
    plan, bundle = _plan_and_bundle()
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    assert a.overall_evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT


def test_empty_hypothesis_set_does_not_pass(db_session):
    a = _assess([], [])
    assert a.overall_investigation_status is InvestigationStatus.NOT_ASSESSABLE
    assert a.progression is ProgressionRecommendation.NOT_ASSESSABLE


def test_unassessed_dimension_triggers_coverage_concern(db_session):
    hyp = _hyp(direction="negative")  # only operating-model dimension
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    plan, bundle = _plan_and_bundle()
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    not_assessed = [d for d in a.dimension_coverage
                    if d.coverage_status.value == "not_assessed"]
    assert len(not_assessed) == 5  # the other five dimensions
    assert any(gap.category is GapCategory.DIMENSION_NOT_ASSESSED
               for gap in a.material_gaps)


def test_not_applicable_dimension_not_forced(db_session):
    """Task 12A: a genuine NOT_APPLICABLE requires an AUTHORITATIVE, approved
    claim (reason + approver + approval_ref). An approved claim yields
    NOT_APPLICABLE with no 'not assessed' gap."""
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    plan, bundle = _plan_and_bundle()
    na = {
        d.value: DimensionNonApplicability(
            reason="No external customers; captive supplier.",
            approved_by="credit.officer",
            approval_ref="human-review:na-approval-1",
        )
        for d in CreditDimension
        if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE
    }
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle,
                not_applicable=na)
    na_dims = [d for d in a.dimension_coverage
               if d.applicability.value == "not_applicable"]
    assert len(na_dims) == 5
    # Approved not-applicable dimensions produce no 'not assessed' gaps.
    assert not any(gap.category is GapCategory.DIMENSION_NOT_ASSESSED
                   for gap in a.material_gaps)


def test_unapproved_na_claim_does_not_clear_the_check(db_session):
    """Task 12A: a bare caller-supplied non-applicability string is UNVERIFIED.
    It must NOT silently clear the check: the dimension stays
    APPLICABILITY_UNCERTAIN with a visible human-review gap."""
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    plan, bundle = _plan_and_bundle()
    na = {d.value: "No external customers; captive supplier."  # bare string
          for d in CreditDimension
          if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE}
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle,
                not_applicable=na)
    # None of the claimed dimensions became NOT_APPLICABLE.
    assert not any(d.applicability.value == "not_applicable"
                   for d in a.dimension_coverage)
    uncertain = [d for d in a.dimension_coverage
                 if d.applicability.value == "applicability_uncertain"]
    assert len(uncertain) == 5
    # Each unverified claim raises a human-review coverage gap.
    na_gaps = [g for g in a.material_gaps
               if g.category is GapCategory.DIMENSION_NOT_ASSESSED]
    assert len(na_gaps) == 5
    assert all(g.requires_human_review for g in na_gaps)


def test_partially_approved_na_claim_is_rejected(db_session):
    """A claim missing an approver OR an approval_ref is NOT approved."""
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")])
    plan, bundle = _plan_and_bundle()
    na = {
        d.value: DimensionNonApplicability(
            reason="n/a", approved_by="credit.officer")  # no approval_ref
        for d in CreditDimension
        if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE
    }
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle,
                not_applicable=na)
    assert not any(d.applicability.value == "not_applicable"
                   for d in a.dimension_coverage)


def test_high_materiality_unverified_remains_visible(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified", uncorroborated=["premise"])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    fa = a.finding_completeness[0]
    assert fa.materiality is Materiality.HIGH
    assert fa.verification_status.value == "unverified"
    # The finding is present (visible), not suppressed.
    assert fa.finding_id


def test_rejected_investigation_candidate_traceable(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="rejected")
    a = _assess([hyp], [g])
    assert a.investigation_candidates  # preserved, not discarded
    assert a.finding_completeness == []  # rejected is not a finding


def test_low_materiality_omission_flagged(db_session):
    hyp = _hyp(direction="positive")
    g = _governed(status="unverified", uncorroborated=["hidden material premise"])
    a = _assess([hyp], [g],
                materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=False)})
    fa = a.finding_completeness[0]
    assert fa.materiality is Materiality.LOW
    assert any(gap.category is GapCategory.POTENTIAL_INVESTIGATION_GAP
               for gap in a.material_gaps)


def test_omitted_vs_executed_no_match_distinct(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])
    # (a) omitted: no bundle.
    a_omit = _assess([hyp], [g], materiality_inputs=_HIGH)
    # (b) executed no-match: offset enquiry run, nothing matched.
    plan, bundle = _plan_and_bundle(offset_query="zzzxylophonequarknonsense")
    a_nomatch = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    assert a_omit.finding_completeness[0].offset_status is (
        OffsetInvestigationStatus.NOT_INVESTIGATED)
    assert a_nomatch.finding_completeness[0].offset_status is (
        OffsetInvestigationStatus.INVESTIGATED_NO_MATCH)


def test_deterministic_for_identical_inputs(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])
    a1 = _assess([hyp], [g], materiality_inputs=_HIGH)
    a2 = _assess([hyp], [g], materiality_inputs=_HIGH)
    assert a1.model_dump(mode="json") == a2.model_dump(mode="json")
    assert a1.assessment_version == COMPLETENESS_POLICY_VERSION


# ===========================================================================
# TASK 12A — gap-id uniqueness, stability and traceability
# ===========================================================================


def test_two_distinct_premises_same_finding_get_distinct_gap_ids(db_session):
    """Two distinct uncorroborated material premises in ONE finding must receive
    DIFFERENT gap IDs (the Task 12 defect: they collided on finding+category)."""
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified",
                  uncorroborated=["premise alpha: dependence exists",
                                  "premise beta: impact is material"])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    premise_gaps = [gap for gap in a.material_gaps
                    if gap.category is GapCategory.UNCORROBORATED_MATERIAL_PREMISE]
    assert len(premise_gaps) == 2
    ids = {gap.gap_id for gap in premise_gaps}
    assert len(ids) == 2  # distinct, not colliding
    # And each gap still maps back to its own premise text.
    descs = {gap.description for gap in premise_gaps}
    assert len(descs) == 2


def test_all_gap_ids_unique_across_the_assessment(db_session):
    """No two gaps anywhere in the assessment share an id (NVIDIA-like shape:
    two findings, each with two uncorroborated premises + requirements)."""
    hyp1 = _hyp("h1", direction="negative",
                dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
                characteristic="Supply dependence.")
    hyp2 = _hyp("h2", direction="negative",
                dim=CreditDimension.COMPETITIVE_DURABILITY,
                characteristic="Competitive intensity.")
    g1 = _governed("h1", dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
                   statement="Supply dependence. affects resilience.",
                   status="unverified",
                   uncorroborated=["supply dependence exists",
                                   "supply impact is material"],
                   unresolved=["measured supply constraint"])
    g2 = _governed("h2", dim=CreditDimension.COMPETITIVE_DURABILITY,
                   statement="Competitive intensity. affects resilience.",
                   status="unverified",
                   uncorroborated=["competition exists",
                                   "margin compression is material"],
                   unresolved=["measured margin compression"])
    mi = {"h1": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True),
          "h2": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True)}
    a = _assess([hyp1, hyp2], [g1, g2], materiality_inputs=mi)
    all_ids = [gap.gap_id for gap in a.material_gaps]
    assert len(all_ids) == len(set(all_ids)), "every gap id is unique"


def test_repeated_gap_category_across_findings_does_not_collide(db_session):
    """The SAME gap category appearing in two different findings yields two
    distinct ids (keyed on finding identity, not category alone)."""
    hyp1 = _hyp("h1", direction="negative",
                dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE)
    hyp2 = _hyp("h2", direction="negative",
                dim=CreditDimension.COMPETITIVE_DURABILITY,
                characteristic="Competitive intensity.")
    g1 = _governed("h1", status="unverified", uncorroborated=["same wording"])
    g2 = _governed("h2", dim=CreditDimension.COMPETITIVE_DURABILITY,
                   statement="Competitive intensity. affects resilience.",
                   status="unverified", uncorroborated=["same wording"])
    mi = {"h1": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True),
          "h2": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True)}
    a = _assess([hyp1, hyp2], [g1, g2], materiality_inputs=mi)
    premise_gaps = [gap for gap in a.material_gaps
                    if gap.category is GapCategory.UNCORROBORATED_MATERIAL_PREMISE]
    assert len(premise_gaps) == 2
    assert len({gap.gap_id for gap in premise_gaps}) == 2


def test_gap_ids_are_stable_across_identical_runs(db_session):
    """Identical analytical inputs reproduce identical gap IDs (deterministic)."""
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified",
                  uncorroborated=["premise alpha", "premise beta"])
    a1 = _assess([hyp], [g], materiality_inputs=_HIGH)
    a2 = _assess([hyp], [g], materiality_inputs=_HIGH)
    ids1 = [gap.gap_id for gap in a1.material_gaps]
    ids2 = [gap.gap_id for gap in a2.material_gaps]
    assert ids1 == ids2 and ids1  # same order, same values, non-empty


def test_human_review_reference_links_to_correct_individual_gap(db_session):
    """A finding's completeness assessment references its own gap IDs, and each
    gap id resolves to exactly one gap (traceable human-review workflow)."""
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified",
                  uncorroborated=["premise alpha", "premise beta"])
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    by_id = {}
    for gap in a.material_gaps:
        assert gap.gap_id not in by_id, "no duplicate gap id"
        by_id[gap.gap_id] = gap
    fa = a.finding_completeness[0]
    assert fa.gaps, "finding references its gap ids"
    for gid in fa.gaps:
        assert gid in by_id            # every referenced id resolves
        assert by_id[gid].finding_id == fa.finding_id  # to the correct finding


def test_case_version_scopes_gap_ids(db_session):
    """Gap ids incorporate case/version identity: different versions of the same
    case produce different ids for the same logical gap (audit separation)."""
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified", uncorroborated=["premise alpha"])
    findings = _findings([hyp], [g], _HIGH)
    svc = MaterialEvidenceCompletenessService(case_id="C1")

    def run(version):
        return svc.assess(EvidenceCompletenessInputs(
            findings=findings, hypotheses=CreditHypothesisSet(hypotheses=[hyp]),
            governed=[g], case_id="C1", case_version=version,
        )).assessment

    v1 = {gap.gap_id for gap in run(1).material_gaps}
    v2 = {gap.gap_id for gap in run(2).material_gaps}
    assert v1 and v2 and v1.isdisjoint(v2)


def test_audit_provenance_preserved(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)],
                  run_id="run-xyz")
    a = _assess([hyp], [g], materiality_inputs=_HIGH)
    assert a.audit_provenance["policy_version"] == COMPLETENESS_POLICY_VERSION
    assert "run-xyz" in a.audit_provenance["testing_run_ids"]
    assert a.rubric_version  # carried from the Task 7 finding set


def test_stale_inputs_fail_closed(db_session):
    hyp = _hyp("h1", direction="negative")
    findings = _findings([hyp], [_governed("h1")])
    # Governed list for a DIFFERENT hypothesis than the findings reference.
    service = MaterialEvidenceCompletenessService(case_id="C1")
    with pytest.raises(EvidenceCompletenessRejectedError):
        service.assess(EvidenceCompletenessInputs(
            findings=findings, hypotheses=CreditHypothesisSet(hypotheses=[hyp]),
            governed=[_governed("OTHER")], case_id="C1"))


# ===========================================================================
# CROSS-INDUSTRY + OFFSET DISCOVERED vs OVERLOOKED
# ===========================================================================


def test_offset_discovered_vs_overlooked_manufacturer(db_session):
    """Same manufacturer vulnerability; offset investigated vs overlooked."""
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])

    # Overlooked: no offset enquiry -> NOT_INVESTIGATED + gap.
    a_overlooked = _assess([hyp], [g], materiality_inputs=_HIGH)
    assert a_overlooked.finding_completeness[0].offset_status is (
        OffsetInvestigationStatus.NOT_INVESTIGATED)

    # Investigated (separate report section discloses the academy) -> matched.
    plan, bundle = _plan_and_bundle(
        offset_query="technical training academy technicians",
        pages=[["S1", "shortage of skilled machining technicians"],
               ["S2", "governance notes"],
               ["S3", "The company operates a technical training academy."]])
    a_inv = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle)
    # Offset enquiry matched a passage, so it is at least investigated (no 'not
    # investigated' gap); existence/effectiveness still separate.
    assert a_inv.finding_completeness[0].offset_status is not (
        OffsetInvestigationStatus.NOT_INVESTIGATED)


def test_second_industry_saas_concentration(db_session):
    hyp = _hyp("sw1", direction="negative",
               dim=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
               characteristic="Top-customer revenue concentration.")
    g = _governed("sw1", dim=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
                  statement="Top-customer revenue concentration. affects resilience.",
                  status="unverified", uncorroborated=["contract durability"])
    a = _assess([hyp], [g],
                materiality_inputs={"sw1": MaterialityInputs(cash_flow_relevant=True,
                                                             broad_or_concentrated=True)})
    fa = a.finding_completeness[0]
    assert fa.dimension is CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION
    # Concentration with an uncorroborated durability premise -> insufficient.
    assert fa.evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert a.progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE


def test_ready_with_escalation_when_review_outstanding(db_session):
    """Investigated + sufficient but a human-review flag -> READY_WITH_ESCALATION."""
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="s",
                                                  assessment="supports_premise", rationale="r")],
                  human_review=True)
    plan, bundle = _plan_and_bundle()
    # Approved not-applicable claims for the other five dimensions, so coverage
    # is complete and the only outstanding item is the human-review flag.
    na = {
        d.value: DimensionNonApplicability(
            reason="n/a", approved_by="credit.officer",
            approval_ref="human-review:na-approval-2")
        for d in CreditDimension
        if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE
    }
    a = _assess([hyp], [g], materiality_inputs=_HIGH, plan=plan, bundle=bundle,
                not_applicable=na)
    assert a.progression is ProgressionRecommendation.READY_WITH_ESCALATION
