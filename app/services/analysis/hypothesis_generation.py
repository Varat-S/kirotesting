"""Credit-hypothesis generation service (Business Overview Module 1).

A small, ISOLATED service that executes the versioned ``hypothesis_generation``
prompt (Task 2) through the single :class:`~app.services.llm.client.LLMClient`
and admits its output ONLY after a layered validation gate (Task 1 governance).

The headline guarantee: a structurally valid but analytically INADMISSIBLE LLM
output never passes downstream. Three layers run in order:

1. **Client JSON-Schema gate (reused).** ``LLMClient.analyze`` parses the raw
   response and validates it against the prompt's fixed
   :data:`~app.schemas.llm.CREDIT_HYPOTHESIS_JSON_SCHEMA` before use. Invalid
   JSON / structural violations are rejected here (``run.is_valid`` is False).
2. **Pydantic cross-field governance.** The parsed object is re-validated with
   :meth:`CreditHypothesisSet.model_validate`, which fires the Task-1
   ``model_validator`` invariants (e.g. supported-needs-evidence,
   assumption-cannot-be-supported, calculated-fact-needs-calc-refs). JSON Schema
   alone does NOT express these cross-field rules.
3. **Service-level generation governance.** Rules that belong to the GENERATION
   stage specifically: every hypothesis stays ``unverified`` (generation never
   verifies), no duplicate hypothesis IDs, and every cited evidence /
   calculation reference belongs to the caller's allow-list (no fabricated or
   silently-repaired references).

Explicit non-responsibilities (kept separate on purpose):

* This service does NOT verify hypotheses. Reference validation is NOT semantic
  verification -- a real evidence ID does not mean the evidence supports the
  asserted economic relationship. That judgement is the later hypothesis-testing
  / grounding stage, NOT this service and NOT :class:`GroundingEvaluator`.
* It assigns no numerical credit score and makes no live provider call.

Auditability: the client already persists the full :class:`ModelRun` row and an
``llm_run`` event for every call (accepted or rejected). When a response passes
the client's JSON-Schema gate but fails layer 2 or 3, this service records a
DISTINCT, additive audit event so the service-level rejection is never silently
mistaken for acceptance (see :data:`HYPOTHESIS_GENERATION_REJECTED_EVENT`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.schemas.llm import (
    CreditHypothesis,
    CreditHypothesisSet,
    HypothesisVerificationStatus,
)
from app.services.audit.log import ActorType, AuditLog
from app.services.llm.client import LLMClient, ModelRunResult

# The prompt that drives this service (registered in PROMPT_CATALOGUE, Task 2).
HYPOTHESIS_PROMPT_NAME = "hypothesis_generation"

# Append-only audit event value for a service-level governance rejection. This
# is a STRING passed to AuditLog.record (whose event_type column is a plain
# String), so it needs NO new EventType enum member and NO database migration.
HYPOTHESIS_GENERATION_REJECTED_EVENT = "hypothesis_generation_rejected"


class HypothesisGenerationRejectedError(ValueError):
    """Raised when generated hypotheses fail any validation layer.

    Carries the model-run provenance (when available) so a caller/audit trail
    can link the rejection to the exact run, and a machine-readable ``stage``
    naming which layer rejected the output.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        run: ModelRunResult | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.run = run


@dataclass
class HypothesisGenerationInputs:
    """The controlled input payload for hypothesis generation.

    ``evidence_ids`` and ``calculation_refs`` are the AUTHORITATIVE allow-lists:
    a generated hypothesis may only cite references drawn from them. They are
    supplied explicitly by the caller (derived from already-validated upstream
    evidence/metrics); the service never invents or widens them.

    The remaining fields are context forwarded to the model. Nothing outside
    these fields reaches the prompt, mirroring :class:`AnalysisInputs`.
    """

    evidence_ids: list[str] = field(default_factory=list)
    calculation_refs: list[str] = field(default_factory=list)
    canonical_facts: list[dict] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    trends: list[dict] = field(default_factory=list)
    benchmarks: list[dict] = field(default_factory=list)
    snippets: list[dict] = field(default_factory=list)
    data_limitations: list[str] = field(default_factory=list)
    # BO roster v3: a specialist investigates only its own dimension(s). Empty
    # means module-wide (the original behaviour); the payload is then unchanged.
    dimension_scope: list[str] = field(default_factory=list)
    # Optional specialist remit/boundaries and an investigation agenda (topics
    # and coverage questions) so generation covers the remit, not just the most
    # obvious risk. Forwarded only when set.
    specialist_brief: str = ""
    agenda: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """The exact context forwarded to the model (allow-lists included)."""
        payload = {
            "canonical_facts": self.canonical_facts,
            "metrics": self.metrics,
            "trends": self.trends,
            "benchmarks": self.benchmarks,
            "snippets": self.snippets,
            "data_limitations": self.data_limitations,
            "allowed_evidence_ids": list(self.evidence_ids),
            "allowed_calculation_refs": list(self.calculation_refs),
        }
        if self.dimension_scope:
            payload["dimension_scope"] = list(self.dimension_scope)
        if self.specialist_brief:
            payload["specialist_brief"] = self.specialist_brief
        if self.agenda:
            payload["agenda"] = list(self.agenda)
        return payload

    def evidence_allow_list(self) -> set[str]:
        return set(self.evidence_ids)

    def calculation_allow_list(self) -> set[str]:
        return set(self.calculation_refs)


@dataclass
class HypothesisGenerationResult:
    """A validated hypothesis set plus its model-run provenance."""

    hypotheses: CreditHypothesisSet
    run: ModelRunResult


class HypothesisGenerationService:
    """Generate credit hypotheses and admit them only after the full gate."""

    def __init__(
        self,
        client: LLMClient,
        *,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._audit = audit
        self._case_id = case_id

    def generate(
        self,
        inputs: HypothesisGenerationInputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
    ) -> HypothesisGenerationResult:
        """Produce a validated :class:`CreditHypothesisSet` or reject.

        Raises :class:`HypothesisGenerationRejectedError` (with ``stage``) if any
        validation layer fails. On success returns the typed set and the run.
        """
        # Layer 1: client JSON-Schema gate (reused; logs the run either way).
        run = self._client.analyze(
            inputs.to_payload(),
            evidence_ids=list(inputs.evidence_ids),
            case_id=self._case_id,
            case_version=case_version,
            prompt_name=HYPOTHESIS_PROMPT_NAME,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            # The client already logged the rejected ModelRun + llm_run event.
            raise HypothesisGenerationRejectedError(
                "Hypothesis-generation output failed JSON-Schema validation and "
                "was rejected before use.",
                stage="json_schema",
                run=run,
            )

        # Layer 2: Pydantic cross-field governance invariants (Task 1).
        try:
            hypotheses = CreditHypothesisSet.model_validate(run.parsed)
        except ValidationError as exc:
            self._record_rejection(run, stage="pydantic_governance", detail=str(exc))
            raise HypothesisGenerationRejectedError(
                "Hypothesis-generation output passed JSON-Schema validation but "
                "violated a cross-field governance invariant.",
                stage="pydantic_governance",
                run=run,
            ) from exc

        # Layer 3: generation-stage service governance.
        violations = self._service_violations(
            hypotheses,
            evidence_allow=inputs.evidence_allow_list(),
            calc_allow=inputs.calculation_allow_list(),
        )
        if violations:
            detail = "; ".join(violations)
            self._record_rejection(run, stage="service_governance", detail=detail)
            raise HypothesisGenerationRejectedError(
                f"Hypothesis-generation output violated generation-stage "
                f"governance: {detail}",
                stage="service_governance",
                run=run,
            )

        return HypothesisGenerationResult(hypotheses=hypotheses, run=run)

    # -- service-level governance checks --------------------------------------

    @staticmethod
    def _service_violations(
        hypotheses: CreditHypothesisSet,
        *,
        evidence_allow: set[str],
        calc_allow: set[str],
    ) -> list[str]:
        """Return a list of generation-stage governance violations (empty = ok).

        These are the rules that belong specifically to GENERATION and are not
        (and should not be) expressed in the JSON Schema or the per-object
        Pydantic validator:

        * every hypothesis must be ``unverified`` (generation never verifies);
        * hypothesis IDs must be unique within the response;
        * cited supporting/contradictory evidence must be in the evidence
          allow-list; calculation refs must be in the calculation allow-list.
        """
        violations: list[str] = []
        seen: set[str] = set()

        for h in hypotheses.hypotheses:
            # Self-verification is forbidden at generation time. A SUPPORTED
            # hypothesis is rejected even if it carries supporting evidence.
            if h.verification_status is not HypothesisVerificationStatus.UNVERIFIED:
                violations.append(
                    f"hypothesis {h.hypothesis_id!r} is "
                    f"{h.verification_status.value!r}; generation must leave every "
                    "hypothesis 'unverified'"
                )

            if h.hypothesis_id in seen:
                violations.append(f"duplicate hypothesis_id {h.hypothesis_id!r}")
            seen.add(h.hypothesis_id)

            violations.extend(
                HypothesisGenerationService._reference_violations(
                    h, evidence_allow=evidence_allow, calc_allow=calc_allow
                )
            )

        return violations

    @staticmethod
    def _reference_violations(
        h: CreditHypothesis,
        *,
        evidence_allow: set[str],
        calc_allow: set[str],
    ) -> list[str]:
        """Flag any cited reference absent from its allow-list (never repaired)."""
        problems: list[str] = []
        for ref in h.supporting_evidence_ids:
            if ref not in evidence_allow:
                problems.append(
                    f"hypothesis {h.hypothesis_id!r} cites unknown supporting "
                    f"evidence id {ref!r}"
                )
        for ref in h.contradictory_evidence_ids:
            if ref not in evidence_allow:
                problems.append(
                    f"hypothesis {h.hypothesis_id!r} cites unknown contradictory "
                    f"evidence id {ref!r}"
                )
        for ref in h.calculation_refs:
            if ref not in calc_allow:
                problems.append(
                    f"hypothesis {h.hypothesis_id!r} cites unknown calculation "
                    f"ref {ref!r}"
                )
        return problems

    # -- audit ----------------------------------------------------------------

    def _record_rejection(
        self, run: ModelRunResult, *, stage: str, detail: str
    ) -> None:
        """Record an additive, append-only service-level rejection event.

        Reuses the existing :class:`AuditLog`. The event type is a plain string
        (no new EventType enum member, no migration); it is distinct from the
        client's ``llm_run`` event so a response that cleared the JSON-Schema gate
        but failed governance is never silently treated as accepted.
        """
        if self._audit is None:
            return
        self._audit.record(
            HYPOTHESIS_GENERATION_REJECTED_EVENT,
            case_id=self._case_id,
            actor_type=ActorType.SYSTEM,
            after={
                "run_id": run.run_id,
                "prompt_id": run.prompt_id,
                "stage": stage,
                "detail": detail,
            },
            reason=(
                "Hypothesis-generation output rejected at the service governance "
                f"boundary (stage={stage})."
            ),
            linked_objects=[f"model_run:{run.run_id}"],
        )
