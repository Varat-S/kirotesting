"""Evidence enquiry planning + retrieval coordination (Stage 5).

Two cooperating, independently testable pieces:

* :class:`EvidenceEnquiryPlanningService` -- turns a validated
  :class:`~app.schemas.llm.CreditHypothesisSet` into a typed, auditable
  :class:`~app.schemas.llm.EvidenceEnquiryPlan`. It runs the registered
  ``evidence_enquiry_planning`` prompt through the single
  :class:`~app.services.llm.client.LLMClient` (reusing ``analyze``), then admits
  the output only after the same layered gate used for hypothesis generation:
  client JSON-Schema validation -> Pydantic governance -> service-level
  governance (bounds, scope, hypothesis linkage, uniqueness). A small
  DETERMINISTIC fallback planner is available when no LLM is wired; it is
  explicitly labelled ``planner_mode="deterministic_fallback"`` and is NOT
  equivalent to generative enquiry.

* :class:`EvidenceEnquiryCoordinator` -- converts an approved plan into
  :class:`~app.schemas.evidence.EvidenceSearchRequest` objects, runs them
  through the deterministic :class:`EvidenceSearchService` (Task 4), and groups
  the candidate passages back under their originating hypothesis and
  investigative intent in a :class:`CandidateEvidenceBundle`.

GOVERNANCE (critical separation):
* The planner decides WHAT to investigate. It never concludes, never invents a
  programme, never cites an unobserved passage, and never changes a hypothesis
  or its verification status.
* The retrieval service LOCATES candidate passages. A passage retrieved under a
  given intent is NOT thereby evidence for/against the hypothesis: query intent
  and a passage's actual evidentiary meaning are separate, and semantic
  evaluation is a later stage.
* A ``no_match`` outcome is NOT proof the capability is absent; unresolved and
  unsearchable enquiries are recorded explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.hashing import content_hash
from app.schemas.evidence import (
    EvidenceCorpus,
    EvidenceSearchIntent,
    EvidenceSearchOutcome,
    EvidenceSearchRequest,
    EvidenceSearchResult,
)
from app.schemas.llm import (
    CreditHypothesisSet,
    EvidenceEnquiry,
    EvidenceEnquiryPlan,
    EvidenceRequirementKind,
    HypothesisVerificationStatus,
)
from app.services.analysis.evidence_retrieval import EvidenceSearchService
from app.services.audit.log import ActorType, AuditLog

ENQUIRY_PROMPT_NAME = "evidence_enquiry_planning"

# Append-only audit event (plain string; no new EventType member, no migration).
ENQUIRY_PLANNING_REJECTED_EVENT = "evidence_enquiry_planning_rejected"

# The requirement perspectives expected for every hypothesis.
_REQUIRED_PERSPECTIVES = {
    EvidenceRequirementKind.SUPPORTING,
    EvidenceRequirementKind.CONTRADICTORY,
}

# Canonical requirement_kind -> search_intent mapping (Task 5A §6).
# An enquiry whose declared search_intent contradicts its requirement_kind is a
# governance violation: it would let an offsetting-capability enquiry be treated
# as ordinary supporting evidence purely via an inconsistent intent label. The
# effectiveness perspective has no dedicated retrieval intent and uses a general
# investigation (an outcome search is not itself "supporting" the hypothesis).
_CANONICAL_INTENT: dict[EvidenceRequirementKind, EvidenceSearchIntent] = {
    EvidenceRequirementKind.SUPPORTING: EvidenceSearchIntent.SUPPORTING_EVIDENCE,
    EvidenceRequirementKind.CONTRADICTORY: EvidenceSearchIntent.CONTRADICTORY_EVIDENCE,
    EvidenceRequirementKind.OFFSETTING: EvidenceSearchIntent.OFFSETTING_CAPABILITY,
    EvidenceRequirementKind.EFFECTIVENESS: EvidenceSearchIntent.GENERAL_INVESTIGATION,
}


def _is_material_negative(hypothesis) -> bool:
    """Conservative applicability indicator for the offsetting perspective.

    Credit direction is used as an INITIAL, conservative proxy for a material
    negative exposure: a ``negative`` hypothesis, or one that already names an
    offset, triggers a mandatory offsetting investigation. This is deliberately
    documented as a proxy -- it is not a substitute for understanding the
    hypothesis's economic mechanism, and a later stage may refine it.
    """
    from app.schemas.llm import CreditDirection

    return (
        hypothesis.credit_direction is CreditDirection.NEGATIVE
        or bool(hypothesis.offsetting_characteristics)
    )


def applicable_perspectives(hypothesis) -> set[EvidenceRequirementKind]:
    """Return the investigative perspectives REQUIRED for one hypothesis.

    Documented, transparent applicability rules (Task 5A §4):

    * supporting + contradictory      -- always required;
    * offsetting                      -- required for a material negative
      exposure (see :func:`_is_material_negative`), EVEN IF no offset is named;
    * effectiveness                   -- required ONLY when a potential
      offset/initiative is already named (``offsetting_characteristics``), since
      a not-yet-found initiative has no outcomes to test.

    Four categories are NOT forced onto every hypothesis.
    """
    required = set(_REQUIRED_PERSPECTIVES)
    if _is_material_negative(hypothesis):
        required.add(EvidenceRequirementKind.OFFSETTING)
    if hypothesis.offsetting_characteristics:
        required.add(EvidenceRequirementKind.EFFECTIVENESS)
    return required


@dataclass(frozen=True)
class CoverageGap:
    """One missing mandatory perspective for one hypothesis."""

    hypothesis_id: str
    missing_perspective: str
    budget_permitted: bool


def evaluate_coverage(plan, hypotheses, budget) -> list[CoverageGap]:
    """Deterministically check a plan for omitted mandatory perspectives.

    Returns a list of :class:`CoverageGap` (empty => fully covered). For each
    gap, ``budget_permitted`` is True when the per-hypothesis budget was large
    enough to have included every applicable perspective -- i.e. the omission is
    NOT excused by the budget and should fail the plan closed. When the budget
    is too small to fit all applicable perspectives, the gap is still reported
    (so coverage is never claimed complete) but marked budget-constrained.
    """
    present: dict[str, set[EvidenceRequirementKind]] = {}
    for enq in plan.enquiries:
        present.setdefault(enq.hypothesis_id, set()).add(enq.requirement_kind)

    gaps: list[CoverageGap] = []
    for hypothesis in hypotheses.hypotheses:
        required = applicable_perspectives(hypothesis)
        have = present.get(hypothesis.hypothesis_id, set())
        missing = required - have
        if not missing:
            continue
        budget_permitted = (
            budget.max_enquiries_per_hypothesis >= len(required)
        )
        for perspective in sorted(missing, key=lambda p: p.value):
            gaps.append(
                CoverageGap(
                    hypothesis_id=hypothesis.hypothesis_id,
                    missing_perspective=perspective.value,
                    budget_permitted=budget_permitted,
                )
            )
    return gaps


@dataclass(frozen=True)
class EnquiryBudget:
    """Configurable bounds that cap LLM/search expansion (no recursion here)."""

    max_enquiries_per_hypothesis: int = 6
    max_queries_per_intent: int = 3
    max_passages_per_query: int = 5
    max_total_queries: int = 60


class EvidenceEnquiryPlanningRejectedError(ValueError):
    """Raised when a planned enquiry set fails any validation layer."""

    def __init__(self, message: str, *, stage: str, run=None) -> None:
        super().__init__(message)
        self.stage = stage
        self.run = run


@dataclass
class EnquiryPlanningInputs:
    """Controlled input for enquiry planning.

    ``permitted_document_ids`` is the authoritative document scope: an enquiry
    may not search outside it. Context fields are forwarded to the model.
    """

    hypotheses: CreditHypothesisSet
    permitted_document_ids: list[str] = field(default_factory=list)
    evidence_requirements: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)

    def to_payload(self, budget: EnquiryBudget) -> dict:
        return {
            "hypotheses": self.hypotheses.model_dump(mode="json"),
            "permitted_document_ids": list(self.permitted_document_ids),
            "evidence_requirements": list(self.evidence_requirements),
            "context": self.context,
            "budget": {
                "max_enquiries_per_hypothesis": budget.max_enquiries_per_hypothesis,
                "max_queries_per_intent": budget.max_queries_per_intent,
            },
        }


@dataclass
class EnquiryPlanningResult:
    """A validated enquiry plan plus its model-run provenance (if any)."""

    plan: EvidenceEnquiryPlan
    run: object | None = None


class EvidenceEnquiryPlanningService:
    """Produce a validated, auditable enquiry plan from a hypothesis set."""

    def __init__(
        self,
        client,
        *,
        budget: EnquiryBudget | None = None,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._budget = budget or EnquiryBudget()
        self._audit = audit
        self._case_id = case_id

    def plan(
        self,
        inputs: EnquiryPlanningInputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
    ) -> EnquiryPlanningResult:
        """Run the planner prompt and admit the plan only after the full gate."""
        run = self._client.analyze(
            inputs.to_payload(self._budget),
            evidence_ids=list(inputs.permitted_document_ids),
            case_id=self._case_id,
            case_version=case_version,
            prompt_name=ENQUIRY_PROMPT_NAME,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            raise EvidenceEnquiryPlanningRejectedError(
                "Enquiry-planning output failed JSON-Schema validation.",
                stage="json_schema",
                run=run,
            )
        try:
            plan = EvidenceEnquiryPlan.model_validate(run.parsed)
        except ValidationError as exc:
            self._record_rejection(run, stage="pydantic_governance", detail=str(exc))
            raise EvidenceEnquiryPlanningRejectedError(
                "Enquiry-planning output passed JSON Schema but violated a "
                "Pydantic governance invariant.",
                stage="pydantic_governance",
                run=run,
            ) from exc

        violations = self._service_violations(plan, inputs)
        if violations:
            detail = "; ".join(violations)
            self._record_rejection(run, stage="service_governance", detail=detail)
            raise EvidenceEnquiryPlanningRejectedError(
                f"Enquiry-planning output violated service governance: {detail}",
                stage="service_governance",
                run=run,
            )

        # Coverage check (Task 5A): a schema-valid plan is not necessarily
        # analytically complete. Fail CLOSED when the budget could have included
        # a missing mandatory perspective; otherwise keep the partial plan but
        # mark its coverage incomplete and expose the unresolved requirement.
        gaps = evaluate_coverage(plan, inputs.hypotheses, self._budget)
        blocking = [g for g in gaps if g.budget_permitted]
        if blocking:
            detail = "; ".join(
                f"hypothesis {g.hypothesis_id!r} missing mandatory "
                f"{g.missing_perspective!r} perspective"
                for g in blocking
            )
            self._record_rejection(run, stage="coverage", detail=detail)
            raise EvidenceEnquiryPlanningRejectedError(
                "Enquiry plan omits a mandatory investigative perspective the "
                f"budget could have accommodated: {detail}",
                stage="coverage",
                run=run,
            )
        if gaps:
            # Budget-constrained omissions: preserve the plan, never claim full
            # coverage. Append to incomplete_coverage (additive, deduplicated).
            plan = self._mark_incomplete(plan, gaps)
        return EnquiryPlanningResult(plan=plan, run=run)

    @staticmethod
    def _mark_incomplete(plan, gaps: list[CoverageGap]):
        """Return a copy of ``plan`` with budget-constrained gaps recorded."""
        additions = [
            {
                "hypothesis_id": g.hypothesis_id,
                "missing_perspective": g.missing_perspective,
                "reason": "mandatory perspective omitted; budget insufficient "
                "for full coverage",
            }
            for g in gaps
        ]
        return plan.model_copy(
            update={"incomplete_coverage": [*plan.incomplete_coverage, *additions]}
        )

    def _service_violations(
        self, plan: EvidenceEnquiryPlan, inputs: EnquiryPlanningInputs
    ) -> list[str]:
        """Service-stage governance checks on a structurally valid plan."""
        violations: list[str] = []
        known_hypotheses = {h.hypothesis_id for h in inputs.hypotheses.hypotheses}
        permitted = set(inputs.permitted_document_ids)
        seen_ids: set[str] = set()
        per_hypothesis: dict[str, int] = {}

        for enq in plan.enquiries:
            if enq.enquiry_id in seen_ids:
                violations.append(f"duplicate enquiry_id {enq.enquiry_id!r}")
            seen_ids.add(enq.enquiry_id)

            if enq.hypothesis_id not in known_hypotheses:
                violations.append(
                    f"enquiry {enq.enquiry_id!r} references unknown hypothesis "
                    f"{enq.hypothesis_id!r}"
                )
            # Scope: an enquiry may not search outside the permitted documents.
            outside = [d for d in enq.permitted_document_ids if d not in permitted]
            if outside:
                violations.append(
                    f"enquiry {enq.enquiry_id!r} targets unpermitted documents "
                    f"{sorted(outside)!r}"
                )
            # Intent consistency (Task 5A §6): the declared search_intent must
            # match the canonical intent for the requirement_kind, so an
            # offsetting enquiry can't be silently treated as supporting.
            expected_intent = _CANONICAL_INTENT[enq.requirement_kind]
            if enq.search_intent is not expected_intent:
                violations.append(
                    f"enquiry {enq.enquiry_id!r} has requirement_kind "
                    f"{enq.requirement_kind.value!r} but inconsistent search_intent "
                    f"{enq.search_intent.value!r} (expected "
                    f"{expected_intent.value!r})"
                )
            per_hypothesis[enq.hypothesis_id] = (
                per_hypothesis.get(enq.hypothesis_id, 0) + 1
            )

        for hid, count in per_hypothesis.items():
            if count > self._budget.max_enquiries_per_hypothesis:
                violations.append(
                    f"hypothesis {hid!r} has {count} enquiries, exceeding the "
                    f"budget of {self._budget.max_enquiries_per_hypothesis}"
                )
        if len(plan.enquiries) > self._budget.max_total_queries:
            violations.append(
                f"plan has {len(plan.enquiries)} enquiries, exceeding the total "
                f"budget of {self._budget.max_total_queries}"
            )
        return violations

    # -- deterministic reduced-capability fallback ----------------------------

    def plan_deterministic(
        self, inputs: EnquiryPlanningInputs
    ) -> EnquiryPlanningResult:
        """Build a minimal plan WITHOUT an LLM (reduced-capability fallback).

        Emits the mandatory perspectives for each hypothesis per
        :func:`applicable_perspectives`:

        * supporting + contradictory for every hypothesis;
        * an OFFSETTING investigation for a material negative exposure EVEN IF
          the hypothesis names no offset (proactive discovery, Task 5A §3) --
          the search terms are generic operating-response concepts, NOT assumed
          company facts;
        * an EFFECTIVENESS enquiry only when an offset is already named.

        This is NOT a substitute for generative conceptual expansion and is
        labelled ``planner_mode="deterministic_fallback"``.
        """
        enquiries: list[EvidenceEnquiry] = []
        incomplete: list[dict] = []
        docs = list(inputs.permitted_document_ids)

        for h in inputs.hypotheses.hypotheses:
            required = applicable_perspectives(h)
            # Ordered so mandatory supporting/contradictory survive a tight
            # budget before the (also-mandatory-when-applicable) offset/effect.
            specs: list[tuple] = [
                (
                    EvidenceRequirementKind.SUPPORTING,
                    f"What evidence supports: {h.statement}",
                    h.business_characteristic,
                    "support for the proposed credit mechanism",
                ),
                (
                    EvidenceRequirementKind.CONTRADICTORY,
                    f"What evidence could weaken or disprove: {h.statement}",
                    h.business_characteristic,
                    "contradictory or qualifying evidence",
                ),
            ]
            if EvidenceRequirementKind.OFFSETTING in required:
                # Proactive, industry-general operating-response CONCEPTS. These
                # are investigation directions, not claimed company initiatives.
                offset_terms = (
                    " ".join(h.offsetting_characteristics)
                    if h.offsetting_characteristics
                    else (
                        f"{h.business_characteristic} mitigation workforce "
                        "development recruitment retention training automation "
                        "productivity contracts operating model response"
                    )
                )
                specs.append(
                    (
                        EvidenceRequirementKind.OFFSETTING,
                        "What company-specific capability, programme, resource "
                        "or arrangement could offset this exposure (if any)?",
                        offset_terms,
                        "existence of a potential company-specific offset",
                    )
                )
            if EvidenceRequirementKind.EFFECTIVENESS in required:
                offset_terms = " ".join(h.offsetting_characteristics)
                specs.append(
                    (
                        EvidenceRequirementKind.EFFECTIVENESS,
                        "Is there measured evidence the identified offset "
                        "improved outcomes (not merely that it exists)?",
                        f"{offset_terms} outcomes results effectiveness",
                        "measured effectiveness of the identified offset",
                    )
                )

            budgeted = specs[: self._budget.max_enquiries_per_hypothesis]
            dropped = [s[0] for s in specs[len(budgeted):]]
            if dropped:
                incomplete.append(
                    {
                        "hypothesis_id": h.hypothesis_id,
                        "dropped_perspectives": [k.value for k in dropped],
                        "reason": "max_enquiries_per_hypothesis budget reached",
                    }
                )
            for kind, question, query, requirement in budgeted:
                enquiries.append(
                    EvidenceEnquiry(
                        enquiry_id=content_hash(
                            {"h": h.hypothesis_id, "kind": kind.value, "q": query}
                        ),
                        hypothesis_id=h.hypothesis_id,
                        dimension=h.dimension,
                        requirement_kind=kind,
                        # Canonical intent keeps requirement_kind and intent
                        # consistent (passes the §6 consistency check).
                        search_intent=_CANONICAL_INTENT[kind],
                        investigative_question=question,
                        search_query=query,
                        rationale=(
                            "Deterministic template enquiry derived from the "
                            "hypothesis statement/characteristic (reduced capability)."
                        ),
                        evidence_requirement=requirement,
                        priority=2 if kind in _REQUIRED_PERSPECTIVES else 3,
                        permitted_document_ids=docs,
                    )
                )
        plan = EvidenceEnquiryPlan(
            enquiries=enquiries,
            planner_mode="deterministic_fallback",
            incomplete_coverage=incomplete,
        )
        return EnquiryPlanningResult(plan=plan, run=None)

    # -- audit ----------------------------------------------------------------

    def _record_rejection(self, run, *, stage: str, detail: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            ENQUIRY_PLANNING_REJECTED_EVENT,
            case_id=self._case_id,
            actor_type=ActorType.SYSTEM,
            after={
                "run_id": run.run_id,
                "prompt_id": run.prompt_id,
                "stage": stage,
                "detail": detail,
            },
            reason=(
                "Evidence-enquiry plan rejected at the service governance "
                f"boundary (stage={stage})."
            ),
            linked_objects=[f"model_run:{run.run_id}"],
        )


# ---------------------------------------------------------------------------
# Coordinator: plan -> searches -> grouped candidate evidence
# ---------------------------------------------------------------------------


class ExecutedEnquiry(BaseModel):
    """One enquiry after execution: the enquiry plus its retrieval result."""

    model_config = ConfigDict(extra="forbid")

    enquiry: EvidenceEnquiry
    result: EvidenceSearchResult


class CandidateEvidenceBundle(BaseModel):
    """Grouped, auditable output of executing an enquiry plan.

    ``by_hypothesis`` maps ``hypothesis_id -> list[ExecutedEnquiry]`` so every
    retrieved passage is traceable to the hypothesis and intent that caused the
    search. ``unresolved_enquiries`` lists enquiries whose outcome was not
    ``matches_found`` (no-match / unsearchable / unavailable), kept explicit so
    an absent result is never mistaken for an answer. Nothing here asserts a
    passage supports or contradicts a hypothesis -- that is semantic evaluation
    for a later stage.
    """

    model_config = ConfigDict(extra="forbid")

    planner_mode: str
    retrieval_version: str | None = None
    executed: list[ExecutedEnquiry] = Field(default_factory=list)
    by_hypothesis: dict[str, list[ExecutedEnquiry]] = Field(default_factory=dict)
    unresolved_enquiries: list[dict] = Field(default_factory=list)
    incomplete_coverage: list[dict] = Field(default_factory=list)


class EvidenceEnquiryCoordinator:
    """Execute an enquiry plan against the deterministic retrieval service."""

    def __init__(
        self, search_service: EvidenceSearchService, *, budget: EnquiryBudget | None = None
    ) -> None:
        self._search = search_service
        self._budget = budget or EnquiryBudget()

    def execute(self, plan: EvidenceEnquiryPlan) -> CandidateEvidenceBundle:
        """Run every enquiry (bounded) and group candidate evidence."""
        executed: list[ExecutedEnquiry] = []
        by_hypothesis: dict[str, list[ExecutedEnquiry]] = {}
        unresolved: list[dict] = []
        retrieval_version: str | None = None
        total = 0

        for enq in plan.enquiries:
            if total >= self._budget.max_total_queries:
                unresolved.append(
                    {
                        "enquiry_id": enq.enquiry_id,
                        "hypothesis_id": enq.hypothesis_id,
                        "reason": "total query budget reached before execution",
                    }
                )
                continue
            total += 1

            request = EvidenceSearchRequest(
                queries=[enq.search_query],
                permitted_document_ids=enq.permitted_document_ids or ["__none__"],
                intent=enq.search_intent,
                max_results=self._budget.max_passages_per_query,
            )
            [result] = self._search.search(request)
            retrieval_version = result.retrieval_version
            item = ExecutedEnquiry(enquiry=enq, result=result)
            executed.append(item)
            by_hypothesis.setdefault(enq.hypothesis_id, []).append(item)

            if result.outcome is not EvidenceSearchOutcome.MATCHES_FOUND:
                unresolved.append(
                    {
                        "enquiry_id": enq.enquiry_id,
                        "hypothesis_id": enq.hypothesis_id,
                        "intent": enq.search_intent.value,
                        "outcome": result.outcome.value,
                    }
                )

        return CandidateEvidenceBundle(
            planner_mode=plan.planner_mode,
            retrieval_version=retrieval_version,
            executed=executed,
            by_hypothesis=by_hypothesis,
            unresolved_enquiries=unresolved,
            incomplete_coverage=list(plan.incomplete_coverage),
        )
