"""Independent RED challenge & reconciliation (Stage 13).

A dedicated :class:`ModuleRedChallengeService` that independently critiques the
module's credit findings, reasoning, materiality, offsets and the Stage 12
completeness conclusions. It is a SEPARATE service (not the original
``ChallengeService``) because the Stage 6-12 objects -- findings, dimensions,
completeness assessments, offsets -- cannot be represented faithfully by the
original ``AnalysisResponse``/``claim_id`` challenge contract. It nonetheless
REUSES the shared infrastructure: ``LLMClient.challenge`` + the versioned
``module_red_challenge`` prompt, the ``PromptRegistry``, the ``EscalationEngine``
and the append-only ``AuditLog``.

Governance (Stage 13):

* **Independence.** A distinct prompt and a separate model run. Using the same
  provider for analysis and challenge is PROCEDURAL independence, not true model
  independence -- disclosed, not hidden.
* **Reference integrity.** Each challenge target must resolve to a known
  hypothesis / finding / dimension / completeness assessment; an unknown target
  is retained as an explicitly UNRESOLVED objection (never silently dropped).
* **Evidence discipline.** The SERVICE decides ``evidence_backed`` by checking
  cited refs against the permitted evidence set; a model's own
  ``claims_evidence_backed`` is never trusted, and the model cannot create a
  contradiction by asserting one.
* **Verification integrity.** A challenge never changes an original verification
  status; original hypotheses/findings/completeness are untouched.
* **Conservative reconciliation.** Every challenge starts UNRESOLVED; a
  model-generated rebuttal never auto-resolves it, and references merely
  existing never resolves it. Serious unresolved challenges stay visible and
  route to human adjudication.
* **Stage 12 gate.** ``READY``/``READY_WITH_ESCALATION`` run a normal RED pass;
  ``BLOCKED`` runs only an explicitly labelled diagnostic pass; ``NOT_ASSESSABLE``
  returns a structured not-assessable outcome. Blocked cases never silently
  become ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.hashing import content_hash
from app.schemas.llm import (
    ChallengeDisposition,
    ChallengeReconciliation,
    ChallengeSeverity,
    CreditFindingSet,
    CreditHypothesisSet,
    GovernedHypothesisTestResult,
    GovernedRedChallenge,
    ModuleEvidenceCompletenessAssessment,
    ModuleRedChallengeReport,
    ProgressionRecommendation,
    RedChallenge,
    RedChallengeResponse,
    RedTargetKind,
)
from app.services.audit.log import ActorType, AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity

RED_CHALLENGE_PROMPT_NAME = "module_red_challenge"
RED_CHALLENGE_REJECTED_EVENT = "module_red_challenge_rejected"
# Explicit rule id for a high-severity RED challenge entering escalation.
RULE_RED_CHALLENGE_HIGH = "R-RED-CHALLENGE-HIGH-01"


class ModuleRedChallengeRejectedError(ValueError):
    """Raised when the RED output fails validation / reference governance."""

    def __init__(self, message: str, *, stage: str, run=None) -> None:
        super().__init__(message)
        self.stage = stage
        self.run = run


@dataclass
class RedChallengeInputs:
    """Everything the RED pass evaluates (original evidence + conclusions)."""

    hypotheses: CreditHypothesisSet
    governed: list[GovernedHypothesisTestResult]
    findings: CreditFindingSet
    completeness: ModuleEvidenceCompletenessAssessment
    # The permitted evidence ids (passages / metric ids) the RED pass may cite.
    permitted_evidence_ids: frozenset[str] = frozenset()
    case_id: str | None = None

    def target_ids(self) -> dict[RedTargetKind, set[str]]:
        hyp = {h.hypothesis_id for h in self.hypotheses.hypotheses}
        findings = {f.signal.signal_id for f in self.findings.findings}
        dims = {d.dimension.value for d in self.completeness.dimension_coverage}
        completeness = {self.completeness.assessment_version}
        return {
            RedTargetKind.HYPOTHESIS: hyp,
            RedTargetKind.FINDING: findings,
            RedTargetKind.OFFSET: findings | hyp,  # offsets attach to either
            RedTargetKind.DIMENSION: dims,
            RedTargetKind.COMPLETENESS: completeness
            | {"module", self.completeness.progression.value},
        }

    def payload(self) -> dict:
        return {
            "progression": self.completeness.progression.value,
            "hypotheses": self.hypotheses.model_dump(mode="json"),
            "findings": [f.model_dump(mode="json") for f in self.findings.findings],
            "material_gaps": [g.model_dump(mode="json")
                              for g in self.completeness.material_gaps],
            "dimension_coverage": [d.model_dump(mode="json")
                                   for d in self.completeness.dimension_coverage],
            "permitted_evidence_ids": sorted(self.permitted_evidence_ids),
        }


@dataclass
class RedChallengeResult:
    report: ModuleRedChallengeReport
    run: object | None = None


class ModuleRedChallengeService:
    """Independent RED critique + conservative reconciliation (Stage 13)."""

    def __init__(
        self,
        client,
        *,
        prompt_registry=None,
        escalation: EscalationEngine | None = None,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._prompts = prompt_registry
        self._escalation = escalation
        self._audit = audit
        self._case_id = case_id

    def challenge(
        self,
        inputs: RedChallengeInputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
        allow_diagnostic: bool = False,
    ) -> RedChallengeResult:
        progression = inputs.completeness.progression
        case_id = inputs.case_id or self._case_id

        # Stage 12 gate.
        if progression is ProgressionRecommendation.NOT_ASSESSABLE:
            return RedChallengeResult(report=self._empty_report(
                inputs, pass_kind="not_assessable", assessable=False))
        pass_kind = "normal"
        if progression is ProgressionRecommendation.READY_WITH_ESCALATION:
            pass_kind = "escalation"
        elif progression is ProgressionRecommendation.BLOCKED_PENDING_EVIDENCE:
            if not allow_diagnostic:
                # Blocked never silently progresses to a normal RED pass.
                return RedChallengeResult(report=self._empty_report(
                    inputs, pass_kind="blocked", assessable=False))
            pass_kind = "diagnostic"

        run = self._client.challenge(
            inputs.payload(),
            case_id=case_id,
            case_version=case_version,
            prompt_name=RED_CHALLENGE_PROMPT_NAME,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            raise ModuleRedChallengeRejectedError(
                "RED challenge output failed JSON-Schema validation.",
                stage="json_schema", run=run,
            )
        try:
            response = RedChallengeResponse.model_validate(run.parsed)
        except ValidationError as exc:
            self._record_rejection(run=run, stage="pydantic_governance", detail=str(exc))
            raise ModuleRedChallengeRejectedError(
                "RED challenge output violated a Pydantic invariant.",
                stage="pydantic_governance", run=run,
            ) from exc

        targets = inputs.target_ids()
        # Duplicate challenge ids are a structural error.
        seen: set[str] = set()
        for c in response.challenges:
            if c.challenge_id in seen:
                detail = f"duplicate challenge_id {c.challenge_id!r}"
                self._record_rejection(run=run, stage="service_governance", detail=detail)
                raise ModuleRedChallengeRejectedError(
                    detail, stage="service_governance", run=run)
            seen.add(c.challenge_id)

        governed: list[GovernedRedChallenge] = []
        escalation_ids: list[str] = []
        for c in response.challenges:
            gc = self._govern_one(c, inputs, targets, case_id)
            governed.append(gc)
            if gc.escalation_id:
                escalation_ids.append(gc.escalation_id)

        outstanding = list(inputs.completeness.human_review_requirements)

        report = ModuleRedChallengeReport(
            case_id=case_id,
            challenge_prompt_version=run.prompt_id,
            red_run_id=run.run_id,
            progression_gate=progression.value,
            pass_kind=pass_kind,
            challenges=governed,
            escalation_ids=escalation_ids,
            outstanding_dependencies=outstanding,
            assessable=True,
            audit_provenance={
                "prompt_id": run.prompt_id,
                "red_run_id": run.run_id,
                "rubric_version": inputs.findings.rubric_version,
                "completeness_version": inputs.completeness.assessment_version,
                "pass_kind": pass_kind,
            },
        )
        self._record_report(report)
        return RedChallengeResult(report=report, run=run)

    # -- per-challenge governance + reconciliation ----------------------------

    def _govern_one(
        self,
        c: RedChallenge,
        inputs: RedChallengeInputs,
        targets: dict,
        case_id: str | None,
    ) -> GovernedRedChallenge:
        reference_resolved = c.target_id in targets.get(c.target_kind, set())
        unknown_refs = sorted(
            r for r in c.evidence_refs if r not in inputs.permitted_evidence_ids
        )
        # SERVICE-determined evidence backing: ALL cited refs resolve to
        # permitted evidence AND at least one ref is cited. The model's own
        # claim is ignored.
        evidence_backed = bool(c.evidence_refs) and not unknown_refs

        # Reconciliation: ALWAYS starts conservative. A serious objection is
        # pending human adjudication; nothing is auto-resolved here.
        if not reference_resolved:
            disposition = ChallengeDisposition.UNRESOLVED
        elif c.severity is ChallengeSeverity.HIGH:
            disposition = ChallengeDisposition.PENDING_HUMAN_ADJUDICATION
        else:
            disposition = ChallengeDisposition.UNRESOLVED

        reconciliation = ChallengeReconciliation(
            challenge_id=c.challenge_id,
            objection=c.reason,
            supporting_evidence_refs=[
                r for r in c.evidence_refs if r in inputs.permitted_evidence_ids
            ],
            rebuttal=None,  # a model rebuttal never auto-resolves (none applied)
            rebuttal_is_evidence_backed=False,
            disposition=disposition,
            corrective_action=c.recommended_action,
            responsible_party="human_credit_reviewer"
            if disposition is ChallengeDisposition.PENDING_HUMAN_ADJUDICATION
            else None,
            provenance={"red_prompt": RED_CHALLENGE_PROMPT_NAME},
        )

        # Escalate HIGH-severity objections (review required, not a proven error).
        escalation_id = None
        requires_review = c.severity is ChallengeSeverity.HIGH or not reference_resolved
        if c.severity is ChallengeSeverity.HIGH:
            escalation_id = self._escalate(c, inputs, case_id)

        return GovernedRedChallenge(
            challenge_id=c.challenge_id,
            target_kind=c.target_kind,
            target_id=c.target_id,
            category=c.category,
            severity=c.severity,
            challenged_statement=c.challenged_statement,
            reason=c.reason,
            evidence_refs=list(c.evidence_refs),
            reference_resolved=reference_resolved,
            evidence_backed=evidence_backed,
            unresolved_unknown_refs=unknown_refs,
            potential_consequence=c.potential_consequence,
            recommended_action=c.recommended_action,
            reconciliation=reconciliation,
            escalation_id=escalation_id,
            requires_human_review=requires_review,
        )

    def _escalate(self, c: RedChallenge, inputs: RedChallengeInputs, case_id):
        if self._escalation is None:
            # Fail visibly: a HIGH challenge that cannot be durably escalated is
            # flagged, not silently 'escalated'.
            self._record_rejection(
                run=None,
                stage="escalation_unavailable",
                detail=f"HIGH challenge {c.challenge_id!r} could not be escalated: "
                "no EscalationEngine configured.",
            )
            return None
        esc = self._escalation.raise_escalation(
            rule_id=RULE_RED_CHALLENGE_HIGH,
            category=EscalationCategory.AI_DETERMINISTIC_CONFLICT,
            severity=Severity.MANDATORY,
            reason=(
                f"HIGH RED challenge ({c.category.value}) on "
                f"{c.target_kind.value}:{c.target_id}: {c.reason}"
            ),
            evidence_refs=[c.challenge_id, *c.evidence_refs],
            case_id=case_id,
        )
        return esc.escalation_id

    # -- helpers --------------------------------------------------------------

    def _empty_report(self, inputs, *, pass_kind, assessable) -> ModuleRedChallengeReport:
        return ModuleRedChallengeReport(
            case_id=inputs.case_id or self._case_id,
            progression_gate=inputs.completeness.progression.value,
            pass_kind=pass_kind,
            assessable=assessable,
            outstanding_dependencies=list(
                inputs.completeness.human_review_requirements
            )
            + [g.description for g in inputs.completeness.blocking_gaps()],
            audit_provenance={
                "pass_kind": pass_kind,
                "completeness_version": inputs.completeness.assessment_version,
                "progression": inputs.completeness.progression.value,
            },
        )

    def _record_rejection(self, *, run, stage: str, detail: str) -> None:
        if self._audit is None:
            return
        after = {"stage": stage, "detail": detail}
        if run is not None:
            after.update({"run_id": run.run_id, "prompt_id": run.prompt_id})
        self._audit.record(
            RED_CHALLENGE_REJECTED_EVENT,
            case_id=self._case_id,
            actor_type=ActorType.SYSTEM,
            after=after,
            reason=f"RED challenge issue (stage={stage}).",
        )

    def _record_report(self, report: ModuleRedChallengeReport) -> None:
        if self._audit is None:
            return
        self._audit.record(
            "module_red_challenge_completed",
            case_id=report.case_id,
            actor_type=ActorType.MODEL,
            after={
                "red_run_id": report.red_run_id,
                "prompt_id": report.challenge_prompt_version,
                "pass_kind": report.pass_kind,
                "challenge_count": len(report.challenges),
                "escalation_ids": report.escalation_ids,
            },
            reason="Independent RED challenge completed (Stage 13).",
        )
