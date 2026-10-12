"""Material Evidence Completeness assessment (Stage 12).

Have we investigated everything MATERIAL? This isolated, DETERMINISTIC service
decides whether the investigation underlying a module's credit analysis is
comprehensive enough to proceed to independent (RED) challenge -- a far stronger
bar than "are documents uploaded" or "do hypotheses carry citations".

Three completeness types are kept STRICTLY SEPARATE (never collapsed into one
score):

* **Source-package completeness** -- reused verbatim from the existing
  :class:`~app.services.ingestion.completeness.CompletenessAssessment` (NOT
  rebuilt here); a serialised view is carried through.
* **Investigation completeness** -- were the relevant questions actually
  investigated (supporting AND contradictory enquiries run, offsets probed,
  requirements turned into executed searches, dimensions covered)? A planned
  enquiry is never equal to an executed one.
* **Evidence sufficiency** -- is the obtained evidence adequate for the proposed
  conclusion (material premises corroborated, contradictions resolved, causal
  inferences not left to management assertion)?

The progression recommendation is derived from BOTH investigation coverage and
evidence sufficiency. ``READY_FOR_RED_CHALLENGE`` is NOT lending approval or
memo sign-off. The service fails conservatively on missing/mismatched inputs,
never invents a successful enquiry or a verified fact, never nets an unproven
offset against a vulnerability, and never converts a missing metric to zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.hashing import content_hash
from app.schemas.evidence import EvidenceSearchOutcome
from app.schemas.llm import (
    CreditDimension,
    CreditFindingSet,
    CreditHypothesisSet,
    DimensionApplicability,
    DimensionCoverageAssessment,
    DimensionCoverageStatus,
    EvidenceSufficiency,
    EvidenceEnquiryPlan,
    FindingCompletenessAssessment,
    GapCategory,
    GovernedHypothesisTestResult,
    HypothesisVerificationStatus,
    InvestigationStatus,
    Materiality,
    MaterialEvidenceGap,
    ModuleEvidenceCompletenessAssessment,
    OffsetInvestigationStatus,
    PrioritisedCreditFinding,
    ProgressionRecommendation,
)
from app.schemas.llm import EvidenceRequirementKind
from app.services.audit.log import ActorType, AuditLog

COMPLETENESS_POLICY_VERSION = "evidence-completeness-1"

_ALL_DIMENSIONS = tuple(CreditDimension)


class EvidenceCompletenessRejectedError(ValueError):
    """Raised when inputs are missing, stale, incompatible or mismatched."""

    def __init__(self, message: str, *, stage: str = "input_validation") -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class DimensionNonApplicability:
    """An AUTHORITATIVE claim that a credit dimension does not apply (Task 12A).

    A bare reason string is NOT proof of non-applicability: a caller can assert
    anything. For a dimension to be treated as genuinely ``NOT_APPLICABLE`` the
    claim must carry an explicit approver (``approved_by``) AND an
    ``approval_ref`` naming the authoritative record that established it (e.g. a
    HumanReview id or a governed policy artifact). A claim that is NOT approved
    is retained as an UNVERIFIED assertion: the dimension stays
    ``APPLICABILITY_UNCERTAIN`` with a visible human-review gap, and it never
    silently clears the Stage 12 completeness check.
    """

    reason: str
    approved_by: str | None = None
    approval_ref: str | None = None

    @property
    def is_approved(self) -> bool:
        return bool(self.reason) and bool(self.approved_by) and bool(self.approval_ref)


@dataclass
class EvidenceCompletenessInputs:
    """Everything Stage 12 consumes (all from earlier, governed stages)."""

    findings: CreditFindingSet
    hypotheses: CreditHypothesisSet
    governed: list[GovernedHypothesisTestResult]
    plan: EvidenceEnquiryPlan | None = None
    bundle: object | None = None  # CandidateEvidenceBundle
    # Serialised source-package CompletenessAssessment (reused, not rebuilt).
    source_package: object | None = None
    case_id: str | None = None
    case_version: int | None = None
    # Dimensions a caller claims not-applicable. BACKWARD-COMPATIBLE value forms:
    #   * ``str``                       -- an UNVERIFIED claim (reason only). It
    #     does NOT clear the check: the dimension stays APPLICABILITY_UNCERTAIN
    #     with a human-review gap (Task 12A: no arbitrary rationale is trusted).
    #   * ``DimensionNonApplicability`` -- genuine NOT_APPLICABLE only when it is
    #     ``is_approved`` (reason + approved_by + approval_ref); otherwise it is
    #     also treated as an unverified claim.
    not_applicable_dimensions: dict[str, "str | DimensionNonApplicability"] = field(
        default_factory=dict
    )


@dataclass
class EvidenceCompletenessResult:
    assessment: ModuleEvidenceCompletenessAssessment


class MaterialEvidenceCompletenessService:
    """Deterministically assess module material evidence completeness."""

    def __init__(self, *, audit: AuditLog | None = None, case_id: str | None = None):
        self._audit = audit
        self._case_id = case_id

    def assess(self, inputs: EvidenceCompletenessInputs) -> EvidenceCompletenessResult:
        self._validate(inputs)
        hyp_by_id = {h.hypothesis_id: h for h in inputs.hypotheses.hypotheses}
        governed_by_id = {g.hypothesis_id: g for g in inputs.governed}

        gaps: list[MaterialEvidenceGap] = []
        finding_assessments: list[FindingCompletenessAssessment] = []

        # Per-finding completeness (HIGH/MEDIUM get full scrutiny; LOW is still
        # scanned for an unrecognised material omission).
        for finding in inputs.findings.findings:
            fa, finding_gaps = self._assess_finding(
                finding, governed_by_id.get(finding.signal.hypothesis_id), inputs
            )
            finding_assessments.append(fa)
            gaps.extend(finding_gaps)

        # Six-dimension coverage.
        dimension_coverage, dim_gaps = self._assess_dimensions(inputs, governed_by_id)
        gaps.extend(dim_gaps)

        # Human-review requirements aggregated across findings + dimensions.
        human_review = sorted(
            {
                f.signal.hypothesis_id
                for f in inputs.findings.findings
                if f.required_human_review
            }
            | {g.gap_id for g in gaps if g.requires_human_review}
        )

        overall_investigation = self._overall_investigation(
            finding_assessments, dimension_coverage, inputs
        )
        overall_sufficiency = self._overall_sufficiency(finding_assessments, gaps)
        progression = self._progression(
            overall_investigation, overall_sufficiency, gaps, inputs
        )

        source_complete = None
        source_view = None
        if inputs.source_package is not None:
            source_view, source_complete = self._source_view(inputs.source_package)

        assessment = ModuleEvidenceCompletenessAssessment(
            case_id=inputs.case_id or self._case_id,
            assessment_version=COMPLETENESS_POLICY_VERSION,
            rubric_version=inputs.findings.rubric_version,
            source_package=source_view,
            source_package_complete=source_complete,
            dimension_coverage=dimension_coverage,
            finding_completeness=finding_assessments,
            material_gaps=gaps,
            investigation_candidates=[
                c.signal_id for c in inputs.findings.investigation_candidates
            ],
            human_review_requirements=human_review,
            overall_investigation_status=overall_investigation,
            overall_evidence_sufficiency=overall_sufficiency,
            progression=progression,
            audit_provenance={
                "policy_version": COMPLETENESS_POLICY_VERSION,
                "rubric_version": inputs.findings.rubric_version,
                "testing_run_ids": sorted(
                    {g.testing_run_id for g in inputs.governed if g.testing_run_id}
                ),
                "finding_count": len(inputs.findings.findings),
                "investigation_candidate_count": len(
                    inputs.findings.investigation_candidates
                ),
            },
        )
        self._record(assessment)
        return EvidenceCompletenessResult(assessment=assessment)

    # -- input validation (fail conservative) ---------------------------------

    @staticmethod
    def _validate(inputs: EvidenceCompletenessInputs) -> None:
        hyp_ids = {h.hypothesis_id for h in inputs.hypotheses.hypotheses}
        gov_ids = {g.hypothesis_id for g in inputs.governed}
        for g in inputs.governed:
            if g.hypothesis_id not in hyp_ids:
                raise EvidenceCompletenessRejectedError(
                    f"governed result references unknown hypothesis "
                    f"{g.hypothesis_id!r}"
                )
        for f in inputs.findings.findings:
            if f.signal.hypothesis_id not in hyp_ids:
                raise EvidenceCompletenessRejectedError(
                    f"finding references unknown hypothesis "
                    f"{f.signal.hypothesis_id!r}"
                )
            if f.signal.hypothesis_id not in gov_ids:
                raise EvidenceCompletenessRejectedError(
                    f"finding {f.signal.hypothesis_id!r} has no governed test "
                    "result (stale/incompatible inputs)"
                )
        if inputs.findings.rubric_version == "":
            raise EvidenceCompletenessRejectedError(
                "findings carry no rubric_version; refusing to assess an "
                "unversioned finding set"
            )

    # -- offset investigation status (six distinct states) --------------------

    def _offset_status(
        self,
        finding: PrioritisedCreditFinding,
        governed: GovernedHypothesisTestResult | None,
        inputs: EvidenceCompletenessInputs,
    ) -> OffsetInvestigationStatus:
        sig = finding.signal
        # Only material NEGATIVE exposures require an offset investigation.
        is_negative = sig.assessed_direction.value == "negative"
        if not is_negative:
            return OffsetInvestigationStatus.NOT_APPLICABLE

        offset_executed, offset_matched = self._offset_enquiry_outcome(
            sig.hypothesis_id, inputs
        )
        has_named_offset = bool(sig.offsetting_characteristics)

        if not offset_executed and not has_named_offset:
            return OffsetInvestigationStatus.NOT_INVESTIGATED
        if offset_executed and not offset_matched and not has_named_offset:
            return OffsetInvestigationStatus.INVESTIGATED_NO_MATCH

        # An offset is named/discovered: is its EXISTENCE / EFFECTIVENESS
        # corroborated? Existence != effectiveness.
        effectiveness_corroborated = False
        existence_corroborated = False
        if governed is not None:
            for pc in governed.premise_corroboration:
                if pc.is_outcome_premise and pc.corroborated:
                    effectiveness_corroborated = True
                elif pc.corroborated:
                    existence_corroborated = True
        if effectiveness_corroborated:
            return OffsetInvestigationStatus.EFFECTIVENESS_SUPPORTED
        if existence_corroborated:
            # Existence is supported but effectiveness is not -> unverified.
            return OffsetInvestigationStatus.EFFECTIVENESS_UNVERIFIED
        if has_named_offset:
            return OffsetInvestigationStatus.POTENTIAL_OFFSET_DISCOVERED
        return OffsetInvestigationStatus.INVESTIGATED_NO_MATCH

    @staticmethod
    def _offset_enquiry_outcome(hypothesis_id, inputs) -> tuple[bool, bool]:
        """Return (offset_enquiry_executed, offset_enquiry_matched) from the bundle."""
        bundle = inputs.bundle
        if bundle is None:
            return False, False
        executed = False
        matched = False
        for ex in getattr(bundle, "executed", []):
            if ex.enquiry.hypothesis_id != hypothesis_id:
                continue
            if ex.enquiry.requirement_kind is EvidenceRequirementKind.OFFSETTING:
                executed = True
                if ex.result.outcome is EvidenceSearchOutcome.MATCHES_FOUND:
                    matched = True
        return executed, matched

    @staticmethod
    def _perspective_executed(hypothesis_id, kind, inputs) -> bool:
        bundle = inputs.bundle
        if bundle is None:
            return False
        return any(
            ex.enquiry.hypothesis_id == hypothesis_id
            and ex.enquiry.requirement_kind is kind
            for ex in getattr(bundle, "executed", [])
        )

    # -- per-finding completeness ---------------------------------------------

    def _assess_finding(
        self,
        finding: PrioritisedCreditFinding,
        governed: GovernedHypothesisTestResult | None,
        inputs: EvidenceCompletenessInputs,
    ) -> tuple[FindingCompletenessAssessment, list[MaterialEvidenceGap]]:
        sig = finding.signal
        hid = sig.hypothesis_id
        material = finding.materiality.materiality
        gaps: list[MaterialEvidenceGap] = []
        # Per-(finding, category) occurrence counter so several gaps of the SAME
        # category within one finding (e.g. two uncorroborated material
        # premises) get DISTINCT ids even if their discriminators were to clash.
        seq: dict[str, int] = {}

        def add_gap(category: GapCategory, desc, *, blocks=False, review=False,
                    required=None, available=None, discriminator=None):
            # Task 12A: a gap id must distinguish logically-distinct gaps that
            # share a finding and category. Key on case/version + finding +
            # category + a stable discriminator (a premise/requirement id or its
            # text) + a per-(finding,category) occurrence index, so two distinct
            # premises never collide while identical inputs stay deterministic.
            n = seq.get(category.value, 0)
            seq[category.value] = n + 1
            gid = content_hash({
                "case": inputs.case_id,
                "version": inputs.case_version,
                "scope": "finding",
                "finding": finding.signal.signal_id,
                "hypothesis": hid,
                "category": category.value,
                "discriminator": discriminator if discriminator is not None else desc,
                "ordinal": n,
            })
            gaps.append(MaterialEvidenceGap(
                gap_id=gid, finding_id=finding.signal.signal_id, hypothesis_id=hid,
                dimension=sig.dimension, category=category, description=desc,
                materiality=material, related_ids=[finding.signal.signal_id],
                evidence_available=available or [],
                evidence_required=required or [],
                recommended_follow_up=desc,
                requires_human_review=review, blocks_progression=blocks,
            ))

        is_material = material is not Materiality.LOW
        # Investigation coverage from the executed bundle (planned != executed).
        supporting_investigated = (
            bool(sig.supporting_evidence_ids)
            or self._perspective_executed(hid, EvidenceRequirementKind.SUPPORTING, inputs)
        )
        contradictory_investigated = (
            bool(sig.contradictory_evidence_ids)
            or self._perspective_executed(hid, EvidenceRequirementKind.CONTRADICTORY, inputs)
        )
        offset_status = self._offset_status(finding, governed, inputs)

        # Gaps (recorded for material findings; LOW scanned for omission below).
        if is_material and not supporting_investigated:
            add_gap(GapCategory.NO_SUPPORTING_INVESTIGATION,
                    "No supporting-evidence investigation for a material finding.",
                    blocks=True, review=True)
        if is_material and not contradictory_investigated:
            add_gap(GapCategory.NO_CONTRADICTORY_INVESTIGATION,
                    "No contradictory-evidence investigation for a material finding.",
                    blocks=False, review=True)
        if offset_status is OffsetInvestigationStatus.NOT_INVESTIGATED and is_material:
            add_gap(GapCategory.OFFSET_NOT_INVESTIGATED,
                    "Material negative exposure with no offset investigation.",
                    blocks=False, review=True,
                    required=["company-specific offset enquiry"])
        if offset_status is OffsetInvestigationStatus.EFFECTIVENESS_UNVERIFIED:
            add_gap(GapCategory.OFFSET_EFFECTIVENESS_UNVERIFIED,
                    "Offset existence established but effectiveness unverified "
                    "(existence != effect; not netted against the exposure).",
                    review=True, available=list(sig.offsetting_characteristics),
                    required=["measured offset outcome evidence"])
        for premise in finding.uncorroborated_material_premises:
            # The premise string IS the upstream structured premise identity
            # (GovernedHypothesisTestResult.uncorroborated_material_premises
            # carries premise_id when set, else the premise text) -> use it as
            # the distinguishing discriminator so two distinct premises differ.
            add_gap(GapCategory.UNCORROBORATED_MATERIAL_PREMISE,
                    f"Material premise uncorroborated: {premise}",
                    blocks=is_material, review=True, required=[premise],
                    discriminator=premise)
        for contra in finding.outstanding_contradictions:
            add_gap(GapCategory.UNRESOLVED_CONTRADICTION,
                    f"Unresolved contradictory evidence: {contra}",
                    blocks=is_material, review=True, available=[contra],
                    discriminator=contra)
        for req in finding.unresolved_evidence_requirements:
            add_gap(GapCategory.OUTSTANDING_EVIDENCE_REQUIREMENT,
                    f"Outstanding evidence requirement: {req}",
                    required=[req], discriminator=req)
        if finding.required_human_review:
            add_gap(GapCategory.HUMAN_REVIEW_OUTSTANDING,
                    "Material human-review requirement outstanding.", review=True)

        # LOW-materiality omission scan: a LOW finding that itself names
        # uncorroborated material premises or contradictions is flagged as a
        # potential overlooked material issue (never silently dropped).
        if not is_material and (
            finding.uncorroborated_material_premises
            or finding.outstanding_contradictions
        ):
            add_gap(GapCategory.POTENTIAL_INVESTIGATION_GAP,
                    "Low-materiality finding exposes an unresolved premise or "
                    "contradiction; possible overlooked material issue.",
                    review=True)

        investigation_status = (
            InvestigationStatus.COMPLETE_FOR_ASSESSED_SCOPE
            if supporting_investigated and contradictory_investigated
            and offset_status is not OffsetInvestigationStatus.NOT_INVESTIGATED
            else InvestigationStatus.PARTIAL
        )
        evidence_sufficiency = self._finding_sufficiency(finding)

        fa = FindingCompletenessAssessment(
            finding_id=finding.signal.signal_id,
            hypothesis_id=hid,
            dimension=sig.dimension,
            materiality=material,
            verification_status=sig.verification_status,
            investigation_status=investigation_status,
            evidence_sufficiency=evidence_sufficiency,
            supporting_investigated=supporting_investigated,
            contradictory_investigated=contradictory_investigated,
            offset_status=offset_status,
            unresolved_requirements=list(finding.unresolved_evidence_requirements),
            gaps=[g.gap_id for g in gaps],
            requires_human_review=finding.required_human_review
            or any(g.requires_human_review for g in gaps),
        )
        return fa, gaps

    @staticmethod
    def _finding_sufficiency(finding: PrioritisedCreditFinding) -> EvidenceSufficiency:
        status = finding.signal.verification_status
        if finding.outstanding_contradictions or finding.uncorroborated_material_premises:
            return EvidenceSufficiency.INSUFFICIENT
        if status is HypothesisVerificationStatus.SUPPORTED:
            return EvidenceSufficiency.SUFFICIENT_FOR_PRELIMINARY_ANALYSIS
        if status is HypothesisVerificationStatus.PARTIALLY_SUPPORTED:
            return EvidenceSufficiency.LIMITED
        return EvidenceSufficiency.INSUFFICIENT

    # -- six-dimension coverage -----------------------------------------------

    @staticmethod
    def _na_claim(value) -> DimensionNonApplicability | None:
        """Coerce a not-applicable map value to a claim (None when absent).

        Accepts the backward-compatible bare ``str`` form (an UNVERIFIED claim,
        never approved) or a :class:`DimensionNonApplicability` record."""
        if value is None:
            return None
        if isinstance(value, DimensionNonApplicability):
            return value
        return DimensionNonApplicability(reason=str(value))

    @staticmethod
    def _dimension_gap_id(inputs, dim, kind: str) -> str:
        """Stable, case/version-scoped id for a dimension-level gap."""
        return content_hash({
            "case": inputs.case_id,
            "version": inputs.case_version,
            "scope": "dimension",
            "dimension": dim.value,
            "kind": kind,
        })

    def _assess_dimensions(
        self, inputs: EvidenceCompletenessInputs, governed_by_id
    ) -> tuple[list[DimensionCoverageAssessment], list[MaterialEvidenceGap]]:
        by_dim: dict[CreditDimension, list[str]] = {d: [] for d in _ALL_DIMENSIONS}
        incomplete_dims: set[CreditDimension] = set()
        for f in inputs.findings.findings:
            by_dim[f.signal.dimension].append(f.signal.hypothesis_id)
            if f.materiality.materiality is not Materiality.LOW and (
                f.uncorroborated_material_premises or f.outstanding_contradictions
            ):
                incomplete_dims.add(f.signal.dimension)

        assessments: list[DimensionCoverageAssessment] = []
        gaps: list[MaterialEvidenceGap] = []
        for dim in _ALL_DIMENSIONS:
            evaluated = by_dim[dim]
            claim = self._na_claim(inputs.not_applicable_dimensions.get(dim.value))
            # Genuine NOT_APPLICABLE requires an AUTHORITATIVE, approved claim.
            if claim is not None and claim.is_approved:
                assessments.append(DimensionCoverageAssessment(
                    dimension=dim, applicability=DimensionApplicability.NOT_APPLICABLE,
                    coverage_status=DimensionCoverageStatus.NOT_APPLICABLE,
                    evaluated_hypotheses=evaluated,
                    reason=(f"{claim.reason} (approved by {claim.approved_by}; "
                            f"ref {claim.approval_ref})"),
                ))
                continue
            # An UNVERIFIED non-applicability claim never clears the check: the
            # dimension stays APPLICABILITY_UNCERTAIN with a visible review gap.
            if claim is not None:
                gid = self._dimension_gap_id(inputs, dim, "unverified_na_claim")
                gaps.append(MaterialEvidenceGap(
                    gap_id=gid, dimension=dim,
                    category=GapCategory.DIMENSION_NOT_ASSESSED,
                    description=(
                        f"Dimension {dim.value} was claimed not-applicable "
                        f"('{claim.reason}') WITHOUT authoritative approval; the "
                        "claim is unverified and applicability remains uncertain."),
                    materiality=Materiality.MEDIUM,
                    recommended_follow_up="Obtain authoritative approval of "
                    "non-applicability, or investigate the dimension.",
                    requires_human_review=True, blocks_progression=False,
                ))
                assessments.append(DimensionCoverageAssessment(
                    dimension=dim,
                    applicability=DimensionApplicability.APPLICABILITY_UNCERTAIN,
                    coverage_status=DimensionCoverageStatus.NOT_ASSESSED,
                    evaluated_hypotheses=evaluated, material_gaps=[gid],
                    reason="Non-applicability claimed but not authoritatively "
                    "approved; applicability uncertain.",
                ))
                continue
            if not evaluated:
                # No hypothesis is NOT proof nothing is material: flag + review.
                gid = self._dimension_gap_id(inputs, dim, "not_assessed")
                gaps.append(MaterialEvidenceGap(
                    gap_id=gid, dimension=dim,
                    category=GapCategory.DIMENSION_NOT_ASSESSED,
                    description=f"Dimension {dim.value} has no hypotheses; "
                    "applicability unestablished.",
                    materiality=Materiality.MEDIUM,
                    recommended_follow_up="Establish applicability or investigate.",
                    requires_human_review=True, blocks_progression=False,
                ))
                assessments.append(DimensionCoverageAssessment(
                    dimension=dim,
                    applicability=DimensionApplicability.APPLICABILITY_UNCERTAIN,
                    coverage_status=DimensionCoverageStatus.NOT_ASSESSED,
                    evaluated_hypotheses=[], material_gaps=[gid],
                    reason="No hypotheses generated; applicability uncertain.",
                ))
                continue
            status = (
                DimensionCoverageStatus.MATERIALLY_INCOMPLETE
                if dim in incomplete_dims
                else DimensionCoverageStatus.ASSESSED
            )
            dim_gap_ids: list[str] = []
            if dim in incomplete_dims:
                gid = self._dimension_gap_id(inputs, dim, "incomplete")
                gaps.append(MaterialEvidenceGap(
                    gap_id=gid, dimension=dim,
                    category=GapCategory.DIMENSION_MATERIALLY_INCOMPLETE,
                    description=f"Dimension {dim.value} assessed but a material "
                    "finding has unresolved premises/contradictions.",
                    materiality=Materiality.MEDIUM, requires_human_review=True,
                ))
                dim_gap_ids.append(gid)
            assessments.append(DimensionCoverageAssessment(
                dimension=dim, applicability=DimensionApplicability.APPLICABLE,
                coverage_status=status, evaluated_hypotheses=evaluated,
                material_gaps=dim_gap_ids,
                reason="Assessed with hypotheses." if status
                is DimensionCoverageStatus.ASSESSED
                else "Assessed but materially incomplete.",
            ))
        return assessments, gaps

    # -- overall statuses + progression ---------------------------------------

    @staticmethod
    def _overall_investigation(finding_assessments, dimension_coverage, inputs):
        if not inputs.findings.findings and not any(
            d.applicability is DimensionApplicability.APPLICABLE
            for d in dimension_coverage
        ):
            # Nothing assessed at all. An empty hypothesis set must NOT pass.
            return InvestigationStatus.NOT_ASSESSABLE
        if any(
            f.investigation_status is not InvestigationStatus.COMPLETE_FOR_ASSESSED_SCOPE
            for f in finding_assessments
        ) or any(
            d.coverage_status
            in {DimensionCoverageStatus.NOT_ASSESSED,
                DimensionCoverageStatus.MATERIALLY_INCOMPLETE}
            for d in dimension_coverage
        ):
            return InvestigationStatus.PARTIAL
        return InvestigationStatus.COMPLETE_FOR_ASSESSED_SCOPE

    @staticmethod
    def _overall_sufficiency(finding_assessments, gaps):
        if not finding_assessments:
            return EvidenceSufficiency.INSUFFICIENT
        levels = {f.evidence_sufficiency for f in finding_assessments}
        if EvidenceSufficiency.INSUFFICIENT in levels:
            return EvidenceSufficiency.INSUFFICIENT
        if EvidenceSufficiency.LIMITED in levels:
            return EvidenceSufficiency.LIMITED
        return EvidenceSufficiency.SUFFICIENT_FOR_PRELIMINARY_ANALYSIS

    @staticmethod
    def _progression(investigation, sufficiency, gaps, inputs):
        blocking = [g for g in gaps if g.blocks_progression]
        # Source-package missing-critical blocks (reused source completeness).
        source_blocks = False
        if inputs.source_package is not None:
            source_blocks = bool(
                getattr(inputs.source_package, "mandatory_escalation", False)
            )
        if investigation is InvestigationStatus.NOT_ASSESSABLE:
            return ProgressionRecommendation.NOT_ASSESSABLE
        if blocking or source_blocks or (
            sufficiency is EvidenceSufficiency.INSUFFICIENT
        ):
            return ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE
        review_outstanding = any(g.requires_human_review for g in gaps) or any(
            f.required_human_review for f in inputs.findings.findings
        )
        if (
            investigation is InvestigationStatus.COMPLETE_FOR_ASSESSED_SCOPE
            and sufficiency is EvidenceSufficiency.SUFFICIENT_FOR_PRELIMINARY_ANALYSIS
            and not review_outstanding
        ):
            return ProgressionRecommendation.READY_FOR_RED_CHALLENGE
        return ProgressionRecommendation.READY_WITH_ESCALATION

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _source_view(source_package) -> tuple[dict, bool]:
        complete = bool(getattr(source_package, "complete", False))
        return (
            {
                "complete": complete,
                "mandatory_escalation": getattr(
                    source_package, "mandatory_escalation", None
                ),
                "missing_critical": list(
                    getattr(source_package, "missing_critical", []) or []
                ),
                "missing_important": list(
                    getattr(source_package, "missing_important", []) or []
                ),
                "profile_version": getattr(source_package, "profile_version", None),
            },
            complete,
        )

    def _record(self, assessment: ModuleEvidenceCompletenessAssessment) -> None:
        if self._audit is None:
            return
        self._audit.record(
            "evidence_completeness_assessed",
            case_id=assessment.case_id,
            actor_type=ActorType.SYSTEM,
            after={
                "assessment_version": assessment.assessment_version,
                "progression": assessment.progression.value,
                "investigation": assessment.overall_investigation_status.value,
                "sufficiency": assessment.overall_evidence_sufficiency.value,
                "material_gap_count": len(assessment.material_gaps),
            },
            reason="Material evidence completeness assessed (Stage 12).",
        )
