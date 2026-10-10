"""Controlled Business Overview Module 1 orchestration (Task 10).

A THIN integration layer that runs the already-implemented analytical services
(Tasks 1-9) as one controlled sequence and verifies they compose with consistent
evidence ids, provenance, verification statuses, materiality, completeness gates
and RED challenges. It adds NO analytical methodology and rewrites NO service.

Stage order (A-H):

    A Evidence Preparation  (deterministic; reuse corpus/retrieval + approved
                             facts/metrics; never widens sources)
    B Hypothesis Generation (LLM; HypothesisGenerationService)
    C Enquiry Planning       (LLM; EvidenceEnquiryPlanningService)
    D Evidence Retrieval     (deterministic; EvidenceEnquiryCoordinator)
    E Hypothesis Testing     (LLM + deterministic governance)
    F Signals/Impact/Material(LLM-optional + deterministic rubric)
    G Evidence Completeness  (deterministic; Stage 12 gate)
    H RED Challenge          (LLM; only per the Stage 12 progression policy)

Governance preserved end-to-end: no auto-verification from id membership, no
corroboration from matching numbers alone, approved-metrics-only grounding,
no model-promoted status, no auto-resolved RED challenge, and the Stage 12
BLOCKED / NOT_ASSESSABLE gate is never overridden to "complete" the run. Every
stage records a :class:`StageStatus`; a failed/blocked stage is never dressed up
as success, and dependent analytical stages do not run after an invalid
prerequisite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.schemas.llm import (
    CreditFindingSet,
    CreditHypothesisSet,
    ModuleEvidenceCompletenessAssessment,
    ModuleRedChallengeReport,
    ProgressionRecommendation,
)
from app.services.analysis.credit_signal_assessment import (
    CreditSignalAssessmentInputs,
    CreditSignalAssessmentRejectedError,
    CreditSignalAssessmentService,
    MaterialityInputs,
)
from app.services.analysis.enquiry_planning import (
    EnquiryBudget,
    EnquiryPlanningInputs,
    EvidenceEnquiryCoordinator,
    EvidenceEnquiryPlanningRejectedError,
    EvidenceEnquiryPlanningService,
)
from app.services.analysis.evidence_completeness import (
    EvidenceCompletenessInputs,
    EvidenceCompletenessRejectedError,
    MaterialEvidenceCompletenessService,
)
from app.services.analysis.evidence_retrieval import EvidenceSearchService
from app.services.analysis.grounding import EvidenceItem
from app.services.analysis.hypothesis_generation import (
    HypothesisGenerationInputs,
    HypothesisGenerationRejectedError,
    HypothesisGenerationService,
)
from app.services.analysis.hypothesis_testing import (
    AdjudicationRecord,
    HypothesisTestingInputs,
    HypothesisTestingRejectedError,
    HypothesisTestingService,
)
from app.services.analysis.red_challenge import (
    ModuleRedChallengeRejectedError,
    ModuleRedChallengeService,
    RedChallengeInputs,
)
from app.schemas.evidence import EvidenceCorpus
from app.services.audit.log import ActorType, AuditLog

ORCHESTRATOR_VERSION = "module1-orchestrator-1"


class Stage(str, Enum):
    EVIDENCE_PREPARATION = "A_evidence_preparation"
    HYPOTHESIS_GENERATION = "B_hypothesis_generation"
    ENQUIRY_PLANNING = "C_enquiry_planning"
    EVIDENCE_RETRIEVAL = "D_evidence_retrieval"
    HYPOTHESIS_TESTING = "E_hypothesis_testing"
    CREDIT_SIGNALS = "F_credit_signals"
    EVIDENCE_COMPLETENESS = "G_evidence_completeness"
    RED_CHALLENGE = "H_red_challenge"


class StageState(str, Enum):
    COMPLETED = "completed"
    REJECTED = "rejected"           # invalid LLM output / governance rejection
    BLOCKED = "blocked"             # Stage 12 gate blocked
    SKIPPED_BY_GATE = "skipped_by_gate"
    FAILED = "failed"               # technical exception / bad prerequisite
    NOT_RUN = "not_run"


class WorkflowDisposition(str, Enum):
    COMPLETED_READY_FOR_RED = "completed_ready_for_red"
    COMPLETED_WITH_ESCALATION = "completed_with_escalation"
    BLOCKED_PENDING_EVIDENCE = "blocked_pending_evidence"
    NOT_ASSESSABLE = "not_assessable"
    FAILED = "failed"


@dataclass
class StageStatus:
    stage: Stage
    state: StageState
    detail: str = ""
    run_id: str | None = None


@dataclass
class Module1Config:
    """Model/prompt + execution configuration passed by the caller."""

    use_llm_for_signals: bool = False
    allow_diagnostic_red: bool = False
    budget: EnquiryBudget = field(default_factory=EnquiryBudget)
    # Scripted-backend keys per stage (offline testing). None -> default key.
    keys: dict[str, str] = field(default_factory=dict)


@dataclass
class Module1Inputs:
    """Approved, provenance-complete inputs for one controlled module run."""

    case_id: str
    case_version: int
    reporting_entity: str
    as_of_date: str
    evidence_cutoff: str
    approved_document_ids: list[str]
    corpus: EvidenceCorpus
    # Approved facts/metrics with provenance -> grounding items (Task 6C: these
    # must come from authoritative records; for offline runs they are explicitly
    # approved fixtures, never caller-invented at test time).
    grounding_items: list[EvidenceItem] = field(default_factory=list)
    validated_metric_ids: frozenset[str] = frozenset()
    source_package: object | None = None          # CompletenessAssessment
    materiality_inputs: dict[str, MaterialityInputs] = field(default_factory=dict)
    not_applicable_dimensions: dict[str, str] = field(default_factory=dict)
    adjudications: tuple[AdjudicationRecord, ...] = ()
    config: Module1Config = field(default_factory=Module1Config)

    def permitted_passage_ids(self) -> frozenset[str]:
        """Permitted evidence ids = corpus passages + approved metric ids."""
        return frozenset(
            {p.passage_id for p in self.corpus.passages}
            | {i.evidence_id for i in self.grounding_items}
        )


@dataclass
class BusinessOverviewModule1Run:
    """Composition of the real stage results (not a flattened copy)."""

    run_id: str
    case_id: str
    case_version: int
    reporting_entity: str
    stage_statuses: list[StageStatus] = field(default_factory=list)
    hypotheses: CreditHypothesisSet | None = None
    enquiry_plan: object | None = None
    candidate_evidence: object | None = None        # CandidateEvidenceBundle
    governed_tests: list = field(default_factory=list)
    findings: CreditFindingSet | None = None
    completeness: ModuleEvidenceCompletenessAssessment | None = None
    red_report: ModuleRedChallengeReport | None = None
    open_escalations: list[str] = field(default_factory=list)
    human_review_dependencies: list[str] = field(default_factory=list)
    disposition: WorkflowDisposition = WorkflowDisposition.FAILED
    provenance: dict = field(default_factory=dict)

    def stage(self, stage: Stage) -> StageStatus | None:
        return next((s for s in self.stage_statuses if s.stage is stage), None)


class _StageAbort(Exception):
    """Internal: stop the run early with a recorded stage status + disposition."""

    def __init__(self, disposition: WorkflowDisposition) -> None:
        self.disposition = disposition


class BusinessOverviewModule1Orchestrator:
    """Run the Tasks 1-9 services as one controlled, provenance-preserving flow."""

    def __init__(
        self,
        *,
        hypothesis_service: HypothesisGenerationService,
        planning_service: EvidenceEnquiryPlanningService,
        search_service: EvidenceSearchService,
        testing_service: HypothesisTestingService,
        signal_service: CreditSignalAssessmentService,
        completeness_service: MaterialEvidenceCompletenessService,
        red_service: ModuleRedChallengeService,
        audit: AuditLog | None = None,
    ) -> None:
        self._gen = hypothesis_service
        self._plan = planning_service
        self._search = search_service
        self._test = testing_service
        self._signal = signal_service
        self._complete = completeness_service
        self._red = red_service
        self._audit = audit

    def run(self, inputs: Module1Inputs) -> BusinessOverviewModule1Run:
        from app.core.hashing import content_hash

        run = BusinessOverviewModule1Run(
            run_id=content_hash(
                {"case": inputs.case_id, "version": inputs.case_version,
                 "orchestrator": ORCHESTRATOR_VERSION}
            ),
            case_id=inputs.case_id,
            case_version=inputs.case_version,
            reporting_entity=inputs.reporting_entity,
            provenance={
                "orchestrator_version": ORCHESTRATOR_VERSION,
                "as_of_date": inputs.as_of_date,
                "evidence_cutoff": inputs.evidence_cutoff,
                "approved_document_ids": sorted(inputs.approved_document_ids),
            },
        )
        cfg = inputs.config
        try:
            self._stage_a(run, inputs)
            self._stage_b(run, inputs, cfg)
            self._stage_c(run, inputs, cfg)
            self._stage_d(run, inputs, cfg)
            self._stage_e(run, inputs, cfg)
            self._stage_f(run, inputs, cfg)
            self._stage_g(run, inputs, cfg)
            self._stage_h(run, inputs, cfg)
        except _StageAbort as abort:
            run.disposition = abort.disposition
            self._record(run)
            return run
        run.disposition = self._final_disposition(run)
        self._record(run)
        return run

    # -- Stage A: evidence preparation (deterministic) ------------------------

    def _stage_a(self, run, inputs: Module1Inputs) -> None:
        # Validate case/source boundaries BEFORE any analysis. The corpus must
        # not contain documents outside the approved set, and approved metric
        # ids must be present as grounding items (no caller-invented metrics).
        corpus_docs = set(inputs.corpus.document_ids)
        approved = set(inputs.approved_document_ids)
        outside = sorted(corpus_docs - approved)
        if outside:
            self._fail(run, Stage.EVIDENCE_PREPARATION,
                       f"corpus contains unapproved documents {outside!r}",
                       WorkflowDisposition.FAILED)
        grounding_ids = {i.evidence_id for i in inputs.grounding_items}
        missing_metrics = sorted(inputs.validated_metric_ids - grounding_ids)
        if missing_metrics:
            self._fail(run, Stage.EVIDENCE_PREPARATION,
                       f"validated metric ids {missing_metrics!r} are not backed by "
                       "approved grounding items (no caller-invented metrics)",
                       WorkflowDisposition.FAILED)
        run.stage_statuses.append(StageStatus(
            Stage.EVIDENCE_PREPARATION, StageState.COMPLETED,
            detail=f"{len(corpus_docs)} approved doc(s), "
            f"{len(inputs.corpus.passages)} passage(s), "
            f"{len(inputs.grounding_items)} approved metric(s)."))

    # -- Stage B: hypothesis generation (LLM) ---------------------------------

    def _stage_b(self, run, inputs, cfg) -> None:
        gen_inputs = HypothesisGenerationInputs(
            evidence_ids=sorted({p.passage_id for p in inputs.corpus.passages}),
            calculation_refs=sorted(inputs.validated_metric_ids),
            canonical_facts=[{"fact_id": i.evidence_id} for i in inputs.grounding_items],
        )
        try:
            result = self._gen.generate(
                gen_inputs, case_version=inputs.case_version,
                key=cfg.keys.get("generate"))
        except HypothesisGenerationRejectedError as exc:
            self._reject(run, Stage.HYPOTHESIS_GENERATION, exc.stage,
                         WorkflowDisposition.FAILED)
        run.hypotheses = result.hypotheses
        run.stage_statuses.append(StageStatus(
            Stage.HYPOTHESIS_GENERATION, StageState.COMPLETED,
            detail=f"{len(result.hypotheses.hypotheses)} hypothesis(es).",
            run_id=getattr(result.run, "run_id", None)))

    # -- Stage C: enquiry planning (LLM) --------------------------------------

    def _stage_c(self, run, inputs, cfg) -> None:
        plan_inputs = EnquiryPlanningInputs(
            hypotheses=run.hypotheses,
            permitted_document_ids=list(inputs.approved_document_ids),
        )
        try:
            result = self._plan.plan(
                plan_inputs, case_version=inputs.case_version,
                key=cfg.keys.get("plan"))
        except EvidenceEnquiryPlanningRejectedError as exc:
            self._reject(run, Stage.ENQUIRY_PLANNING, exc.stage,
                         WorkflowDisposition.FAILED)
        run.enquiry_plan = result.plan
        run.stage_statuses.append(StageStatus(
            Stage.ENQUIRY_PLANNING, StageState.COMPLETED,
            detail=f"{len(result.plan.enquiries)} enquiry(ies); mode="
            f"{result.plan.planner_mode}.",
            run_id=getattr(result.run, "run_id", None)))

    # -- Stage D: evidence retrieval (deterministic) --------------------------

    def _stage_d(self, run, inputs, cfg) -> None:
        try:
            coordinator = EvidenceEnquiryCoordinator(self._search, budget=cfg.budget)
            bundle = coordinator.execute(run.enquiry_plan)
        except Exception as exc:  # technical failure -> FAILED, not success
            self._fail(run, Stage.EVIDENCE_RETRIEVAL,
                       f"retrieval error: {type(exc).__name__}",
                       WorkflowDisposition.FAILED)
        run.candidate_evidence = bundle
        # Provenance: never claim a source is available just because an id
        # appeared; passage ids come only from the executed retrieval.
        retrieved = {rp.passage.passage_id
                     for ex in bundle.executed for rp in ex.result.ranked_passages}
        run.provenance["retrieved_passage_ids"] = sorted(retrieved)
        run.stage_statuses.append(StageStatus(
            Stage.EVIDENCE_RETRIEVAL, StageState.COMPLETED,
            detail=f"{len(bundle.executed)} executed; "
            f"{len(bundle.unresolved_enquiries)} unresolved."))

    # -- Stage E: hypothesis testing (LLM + governance) -----------------------

    def _stage_e(self, run, inputs, cfg) -> None:
        test_inputs = HypothesisTestingInputs(
            hypotheses=run.hypotheses, bundle=run.candidate_evidence,
            plan=run.enquiry_plan, grounding_items=list(inputs.grounding_items),
            budget=cfg.budget, adjudications=inputs.adjudications)
        try:
            result = self._test.test(
                test_inputs, case_version=inputs.case_version,
                key=cfg.keys.get("test"))
        except HypothesisTestingRejectedError as exc:
            self._reject(run, Stage.HYPOTHESIS_TESTING, exc.stage,
                         WorkflowDisposition.FAILED)
        run.governed_tests = result.governed
        run.provenance["testing_run_id"] = getattr(result.run, "run_id", None)
        run.stage_statuses.append(StageStatus(
            Stage.HYPOTHESIS_TESTING, StageState.COMPLETED,
            detail=f"{len(result.governed)} governed test(s).",
            run_id=getattr(result.run, "run_id", None)))

    # -- Stage F: signals / impact / materiality ------------------------------

    def _stage_f(self, run, inputs, cfg) -> None:
        signal_inputs = CreditSignalAssessmentInputs(
            hypotheses=run.hypotheses, governed=run.governed_tests,
            validated_metric_ids=inputs.validated_metric_ids,
            materiality_inputs=inputs.materiality_inputs)
        try:
            result = self._signal.assess(
                signal_inputs, case_version=inputs.case_version,
                key=cfg.keys.get("signal"), use_llm=cfg.use_llm_for_signals)
        except CreditSignalAssessmentRejectedError as exc:
            self._reject(run, Stage.CREDIT_SIGNALS, exc.stage,
                         WorkflowDisposition.FAILED)
        run.findings = result.findings
        run.stage_statuses.append(StageStatus(
            Stage.CREDIT_SIGNALS, StageState.COMPLETED,
            detail=f"{len(result.findings.findings)} finding(s); "
            f"{len(result.findings.investigation_candidates)} candidate(s)."))

    # -- Stage G: evidence completeness (deterministic gate) ------------------

    def _stage_g(self, run, inputs, cfg) -> None:
        complete_inputs = EvidenceCompletenessInputs(
            findings=run.findings, hypotheses=run.hypotheses,
            governed=run.governed_tests, plan=run.enquiry_plan,
            bundle=run.candidate_evidence, source_package=inputs.source_package,
            case_id=inputs.case_id,
            not_applicable_dimensions=inputs.not_applicable_dimensions)
        try:
            result = self._complete.assess(complete_inputs)
        except EvidenceCompletenessRejectedError as exc:
            self._reject(run, Stage.EVIDENCE_COMPLETENESS, exc.stage,
                         WorkflowDisposition.FAILED)
        run.completeness = result.assessment
        run.human_review_dependencies = list(
            result.assessment.human_review_requirements)
        run.stage_statuses.append(StageStatus(
            Stage.EVIDENCE_COMPLETENESS, StageState.COMPLETED,
            detail=f"progression={result.assessment.progression.value}; "
            f"{len(result.assessment.material_gaps)} gap(s)."))

    # -- Stage H: RED challenge (per Stage 12 gate) ---------------------------

    def _stage_h(self, run, inputs, cfg) -> None:
        progression = run.completeness.progression
        red_inputs = RedChallengeInputs(
            hypotheses=run.hypotheses, governed=run.governed_tests,
            findings=run.findings, completeness=run.completeness,
            permitted_evidence_ids=inputs.permitted_passage_ids(),
            case_id=inputs.case_id)

        if progression is ProgressionRecommendation.NOT_ASSESSABLE:
            run.stage_statuses.append(StageStatus(
                Stage.RED_CHALLENGE, StageState.SKIPPED_BY_GATE,
                detail="Stage 12 NOT_ASSESSABLE; no RED pass."))
            return
        if progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE \
                and not cfg.allow_diagnostic_red:
            # Never override a BLOCKED gate to complete the run.
            run.stage_statuses.append(StageStatus(
                Stage.RED_CHALLENGE, StageState.BLOCKED,
                detail="Stage 12 BLOCKED; normal RED pass withheld."))
            return

        try:
            result = self._red.challenge(
                red_inputs, case_version=inputs.case_version,
                key=cfg.keys.get("red"),
                allow_diagnostic=cfg.allow_diagnostic_red)
        except ModuleRedChallengeRejectedError as exc:
            self._reject(run, Stage.RED_CHALLENGE, exc.stage,
                         WorkflowDisposition.FAILED)
        run.red_report = result.report
        run.open_escalations = list(result.report.escalation_ids)
        state = (
            StageState.SKIPPED_BY_GATE
            if not result.report.assessable
            else StageState.COMPLETED
        )
        run.stage_statuses.append(StageStatus(
            Stage.RED_CHALLENGE, state,
            detail=f"pass_kind={result.report.pass_kind}; "
            f"{len(result.report.challenges)} challenge(s).",
            run_id=result.report.red_run_id))

    # -- disposition + helpers ------------------------------------------------

    @staticmethod
    def _final_disposition(run: BusinessOverviewModule1Run) -> WorkflowDisposition:
        progression = run.completeness.progression if run.completeness else None
        if progression is ProgressionRecommendation.NOT_ASSESSABLE:
            return WorkflowDisposition.NOT_ASSESSABLE
        if progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE:
            return WorkflowDisposition.BLOCKED_PENDING_EVIDENCE
        if progression is ProgressionRecommendation.READY_WITH_ESCALATION:
            return WorkflowDisposition.COMPLETED_WITH_ESCALATION
        if progression is ProgressionRecommendation.READY_FOR_RED_CHALLENGE:
            return WorkflowDisposition.COMPLETED_READY_FOR_RED
        return WorkflowDisposition.FAILED

    def _fail(self, run, stage: Stage, detail: str,
              disposition: WorkflowDisposition) -> None:
        run.stage_statuses.append(StageStatus(stage, StageState.FAILED, detail=detail))
        raise _StageAbort(disposition)

    def _reject(self, run, stage: Stage, inner_stage: str,
                disposition: WorkflowDisposition) -> None:
        run.stage_statuses.append(StageStatus(
            stage, StageState.REJECTED, detail=f"rejected at {inner_stage}"))
        raise _StageAbort(disposition)

    def _record(self, run: BusinessOverviewModule1Run) -> None:
        if self._audit is None:
            return
        self._audit.record(
            "module1_orchestration_run",
            case_id=run.case_id,
            actor_type=ActorType.SYSTEM,
            after={
                "run_id": run.run_id,
                "orchestrator_version": ORCHESTRATOR_VERSION,
                "disposition": run.disposition.value,
                "stages": {s.stage.value: s.state.value for s in run.stage_statuses},
                "open_escalations": run.open_escalations,
            },
            reason="Business Overview Module 1 controlled orchestration run.",
        )
