"""Semantic credit-hypothesis testing (Stage 8).

An isolated :class:`HypothesisTestingService` that evaluates each hypothesis
against the ACTUAL retrieved candidate evidence and proposes a verification
status -- then INDEPENDENTLY enforces reference, coverage and conservatism
constraints so a model's plausible story can never, by itself, promote a
hypothesis to SUPPORTED.

Three questions are kept strictly separate (they are NOT interchangeable):

* Layer A -- factual grounding: does a cited passage/metric actually establish a
  specific factual assertion? Reuses the deterministic
  :class:`~app.services.analysis.grounding.GroundingEvaluator`. Critically, the
  grounding evidence is built from the real passages/metrics WITHOUT any
  model-asserted ``supports`` flag, so a verdict the testing model merely
  asserted can never masquerade as independent grounding (Task 6 §2).
* Layer B -- evidence relevance: what does each passage ACTUALLY establish,
  independent of the search intent it was retrieved under?
* Layer C -- economic mechanism: do the premises linking the business
  characteristic to the credit implication hold? The EXISTENCE of an initiative
  never establishes its EFFECTIVENESS or materiality.

The model PROPOSES (:class:`HypothesisTestResult`); the service DISPOSES
(:class:`GovernedHypothesisTestResult`), recording the proposed status, the
effective status, and every downgrade reason. The original
:class:`CreditHypothesis` is never mutated.

Coverage: the Stage 5A coverage policy is REUSED (not re-implemented) via
:func:`evaluate_coverage` / :func:`applicable_perspectives`, so an incomplete
plan -- including a directly constructed one that bypassed the planner -- can
never silently yield a fully SUPPORTED conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.schemas.llm import (
    AnalyticalClaim,
    ClaimClassification,
    ClaimKind,
    CreditDirection,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceAssessmentKind,
    EvidenceEnquiryPlan,
    FactualGroundingRecord,
    GovernedHypothesisTestResult,
    HypothesisTestResponse,
    HypothesisTestResult,
    HypothesisVerificationStatus,
    MechanismPremise,
    PremiseCorroboration,
    PremiseStatus,
)
from app.services.analysis.enquiry_planning import (
    CandidateEvidenceBundle,
    EnquiryBudget,
    applicable_perspectives,
    evaluate_coverage,
)
from app.services.analysis.grounding import (
    EvidenceIndex,
    EvidenceItem,
    GroundingEvaluator,
    verbatim_fact_support,
)
from app.services.audit.log import ActorType, AuditLog

HYPOTHESIS_TESTING_PROMPT_NAME = "hypothesis_testing"
HYPOTHESIS_TESTING_REJECTED_EVENT = "hypothesis_testing_rejected"

_SUPPORTING = HypothesisVerificationStatus
_OUTCOME_ASSESSMENTS = {EvidenceAssessmentKind.MEASURED_OUTCOME}


class HypothesisTestingRejectedError(ValueError):
    """Raised when the testing output fails a validation / governance layer."""

    def __init__(self, message: str, *, stage: str, run=None) -> None:
        super().__init__(message)
        self.stage = stage
        self.run = run


# Human-review actions that count as a premise/hypothesis corroboration decision.
_CORROBORATING_REVIEW_ACTIONS = {
    "accept_ai_interpretation",
    "resolve_conflict",
    "resolve_contradiction",
    "approve_assumptions",
    "verify_source",
}


@dataclass(frozen=True)
class AdjudicationRecord:
    """An AUTHORISED human-adjudication decision (Task 6B §7).

    Derived from a real, append-only ``HumanReview`` row -- never a bare caller
    id and never model output. Carries reviewer identity, the decision, the
    premise/hypothesis target, referenced evidence, rationale and provenance so
    corroboration is tied to an auditable review, not an assertion.
    """

    review_id: str
    reviewer: str
    action: str
    hypothesis_id: str
    premise_id: str | None
    decision_supports: bool
    evidence_refs: tuple[str, ...]
    rationale: str | None

    @classmethod
    def from_human_review(cls, row) -> "AdjudicationRecord | None":
        """Build from a ``HumanReview`` ORM row, or None if it is not a usable
        corroboration decision (fail-closed)."""
        action = getattr(row, "action", None)
        if action not in _CORROBORATING_REVIEW_ACTIONS:
            return None
        if not getattr(row, "reviewer", None):
            return None
        new_value = getattr(row, "new_value", None) or {}
        target_type = getattr(row, "target_type", None)
        target_id = getattr(row, "target_id", None)
        hypothesis_id = new_value.get("hypothesis_id") or (
            target_id if target_type == "hypothesis" else None
        )
        if hypothesis_id is None:
            return None
        # A rejection decision does not corroborate support.
        decision = str(new_value.get("decision", "accepted")).lower()
        supports = decision not in {"rejected", "reject", "deny", "denied"}
        return cls(
            review_id=getattr(row, "review_id", ""),
            reviewer=row.reviewer,
            action=action,
            hypothesis_id=str(hypothesis_id),
            premise_id=new_value.get("premise_id"),
            decision_supports=supports,
            evidence_refs=tuple(new_value.get("evidence_refs", [])),
            rationale=getattr(row, "reason", None),
        )


@dataclass
class HypothesisTestingInputs:
    """Controlled input for hypothesis testing.

    ``bundle`` carries the executed enquiries + retrieved passages. ``plan`` is
    the enquiry plan actually used (optional but recommended): when present the
    coverage policy is recomputed against it, so a directly constructed plan
    cannot bypass the coverage safeguard. ``grounding_items`` are the approved
    factual facts/metrics for Layer A -- supplied by the caller from validated
    upstream evidence, NEVER widened by this service.
    """

    hypotheses: CreditHypothesisSet
    bundle: CandidateEvidenceBundle
    plan: EvidenceEnquiryPlan | None = None
    grounding_items: list[EvidenceItem] = field(default_factory=list)
    budget: EnquiryBudget = field(default_factory=EnquiryBudget)
    # Authorised human adjudications (Task 6B §7), each derived from a real
    # HumanReview row via AdjudicationRecord. A bare id / flag / model output can
    # NOT appear here. Absent a matching record, only deterministic premise-level
    # grounding can corroborate.
    adjudications: tuple[AdjudicationRecord, ...] = ()
    # The borrower's names (legal name, short name, ticker, aliases) from the
    # entity record; used only by the verbatim reported-fact check (fix R1a).
    entity_aliases: tuple[str, ...] = ()

    def grounding_item_ids(self) -> set[str]:
        return {item.evidence_id for item in self.grounding_items}

    def adjudication_for(
        self, hypothesis_id: str, premise_id: str | None
    ) -> AdjudicationRecord | None:
        """An authorised, supporting adjudication for this exact premise (or the
        hypothesis as a whole when the review targeted no specific premise)."""
        for rec in self.adjudications:
            if rec.hypothesis_id != hypothesis_id or not rec.decision_supports:
                continue
            if premise_id is not None and rec.premise_id == premise_id:
                return rec
            if rec.premise_id is None:
                return rec
        return None

    def passage_index(self) -> dict[str, str]:
        """Map permitted passage_id -> passage text, from the executed bundle only."""
        index: dict[str, str] = {}
        for executed in self.bundle.executed:
            for ranked in executed.result.ranked_passages:
                index[ranked.passage.passage_id] = ranked.passage.text
        return index

    def to_payload(self) -> dict:
        """The bounded, permitted passages + hypotheses forwarded to the model."""
        passages = [
            {
                "passage_id": ranked.passage.passage_id,
                "document_id": ranked.passage.document_id,
                "pdf_page": ranked.passage.pdf_page,
                "text": ranked.passage.text,
                "retrieved_under_intent": executed.enquiry.search_intent.value,
                "hypothesis_id": executed.enquiry.hypothesis_id,
            }
            for executed in self.bundle.executed
            for ranked in executed.result.ranked_passages
        ]
        return {
            "hypotheses": self.hypotheses.model_dump(mode="json"),
            "candidate_passages": passages,
            "unresolved_enquiries": self.bundle.unresolved_enquiries,
            "incomplete_coverage": self.bundle.incomplete_coverage,
            # The only metric ids a premise may link to (with their trusted
            # concept); without them a model can only invent ids, which never
            # corroborate anything.
            "approved_metrics": [
                {"metric_id": i.evidence_id, "concept": i.concept, "value": i.value,
                 "unit": i.unit, "period": i.period, "entity": i.entity,
                 "scale": i.scale}
                for i in self.grounding_items],
        }


@dataclass
class HypothesisTestingResult:
    """Governed results plus model-run provenance."""

    governed: list[GovernedHypothesisTestResult]
    run: object
    # Quotes that were not verbatim and were repaired or dropped (only when a
    # ``quote_repair`` callable is configured); one audit record each.
    quote_repairs: list[dict] = field(default_factory=list)


class HypothesisTestingService:
    """Evaluate hypotheses against candidate evidence under a conservative gate."""

    def __init__(
        self,
        client,
        *,
        audit: AuditLog | None = None,
        case_id: str | None = None,
        review_lookup=None,
        reviewer_authority=None,
        quote_repair=None,
        model_tier: str | None = None,
    ) -> None:
        self._client = client
        # Optional model tier for the testing call (e.g. a stronger model for
        # the stage that judges evidence); None keeps the default tier.
        self._model_tier = model_tier
        # Optional ``(quote, passage_id) -> record`` recovery hook. When set, a
        # quoted_excerpt that is not verbatim gets ONE bounded repair attempt
        # (layout gaps inside the cited passage only); if that fails the excerpt
        # alone is dropped and recorded, instead of rejecting the whole stage.
        # Unknown passage or premise ids still reject the stage. Default (None):
        # the original fail-closed behaviour.
        self._quote_repair = quote_repair
        self._audit = audit
        self._case_id = case_id
        # Task 6C: a callable review_id -> HumanReview row (or None). Supplying a
        # SQLAlchemy Session is also accepted. Without it, caller-supplied
        # adjudications CANNOT be verified and are rejected (fail-closed).
        self._review_lookup = self._coerce_review_lookup(review_lookup)
        # Optional callable reviewer -> bool stating the reviewer is authorised.
        self._reviewer_authority = reviewer_authority

    @staticmethod
    def _coerce_review_lookup(review_lookup):
        if review_lookup is None:
            return None
        if callable(review_lookup):
            return review_lookup
        # Treat as a SQLAlchemy Session: look up HumanReview by review_id.
        from app.models.orm import HumanReview

        def _lookup(review_id: str):
            return (
                review_lookup.query(HumanReview)
                .filter(HumanReview.review_id == review_id)
                .one_or_none()
            )

        return _lookup

    def test(
        self,
        inputs: HypothesisTestingInputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
    ) -> HypothesisTestingResult:
        run = self._client.analyze(
            inputs.to_payload(),
            case_id=self._case_id,
            case_version=case_version,
            prompt_name=HYPOTHESIS_TESTING_PROMPT_NAME,
            key=key,
            model_tier=self._model_tier,
        )
        if not run.is_valid or run.parsed is None:
            raise HypothesisTestingRejectedError(
                "Hypothesis-testing output failed JSON-Schema validation.",
                stage="json_schema",
                run=run,
            )
        try:
            response = HypothesisTestResponse.model_validate(run.parsed)
        except ValidationError as exc:
            self._record_rejection(run, stage="pydantic_governance", detail=str(exc))
            raise HypothesisTestingRejectedError(
                "Hypothesis-testing output passed JSON Schema but violated a "
                "Pydantic invariant.",
                stage="pydantic_governance",
                run=run,
            ) from exc

        hyp_by_id = {h.hypothesis_id: h for h in inputs.hypotheses.hypotheses}
        passages = inputs.passage_index()
        grounding_ids = inputs.grounding_item_ids()
        quote_repairs: list[dict] = []
        if self._quote_repair is not None:
            response, quote_repairs = self._repair_quotes(response, passages)

        # Structural/reference governance across the whole response.
        violations = self._reference_violations(
            response, hyp_by_id, passages, grounding_ids
        )
        if violations:
            detail = "; ".join(violations)
            self._record_rejection(run, stage="service_governance", detail=detail)
            raise HypothesisTestingRejectedError(
                f"Hypothesis-testing output violated reference governance: {detail}",
                stage="service_governance",
                run=run,
            )

        # Coverage recomputed independently (reusing the Stage 5A policy).
        coverage_by_hyp = self._coverage(inputs)

        # Verify caller-supplied adjudications against real HumanReview records
        # BEFORE any of them can corroborate (Task 6C). Unverified ones are
        # dropped fail-closed.
        verified_adjudications = self._verify_adjudications(inputs.adjudications)

        governed = [
            self._govern(result, hyp_by_id[result.hypothesis_id], inputs,
                         coverage_by_hyp.get(result.hypothesis_id, []), passages,
                         verified_adjudications, run_id=run.run_id)
            for result in response.results
        ]
        return HypothesisTestingResult(governed=governed, run=run,
                                       quote_repairs=quote_repairs)

    def _repair_quotes(self, response, passages):
        """Repair layout-gap quotes in place; drop (and record) unrecoverable ones."""
        records: list[dict] = []
        results = []
        for result in response.results:
            assessments = []
            for a in result.evidence_assessments:
                text = passages.get(a.passage_id)
                if text is None or not a.quoted_excerpt or a.quoted_excerpt in text:
                    assessments.append(a)
                    continue
                rec = self._quote_repair(a.quoted_excerpt, a.passage_id) or {}
                repaired = rec.get("replacement_quote")
                if (rec.get("outcome") == "layout_gap" and repaired
                        and repaired in text):
                    a = a.model_copy(update={"quoted_excerpt": repaired})
                    action = "repaired_layout_gap"
                else:
                    a = a.model_copy(update={"quoted_excerpt": None})
                    action = "dropped_not_verbatim"
                records.append({"hypothesis_id": result.hypothesis_id,
                                "passage_id": a.passage_id, "action": action,
                                "detail": rec.get("detail")})
                assessments.append(a)
            results.append(result.model_copy(update={"evidence_assessments": assessments}))
        return response.model_copy(update={"results": results}), records

    # -- reference / structural governance ------------------------------------

    @staticmethod
    def _reference_violations(response, hyp_by_id, passages, grounding_ids) -> list[str]:
        problems: list[str] = []
        seen: set[str] = set()
        # Valid references for a premise are retrieved passages OR approved
        # grounding items (metrics/facts). Evidence ASSESSMENTS, however, must
        # cite a real retrieved passage (an assessment is about passage meaning).
        valid_premise_refs = set(passages) | set(grounding_ids)
        for result in response.results:
            if result.hypothesis_id not in hyp_by_id:
                problems.append(
                    f"result references unknown hypothesis {result.hypothesis_id!r}"
                )
            if result.hypothesis_id in seen:
                problems.append(
                    f"duplicate result for hypothesis {result.hypothesis_id!r}"
                )
            seen.add(result.hypothesis_id)
            for assessment in result.evidence_assessments:
                text = passages.get(assessment.passage_id)
                if text is None:
                    problems.append(
                        f"assessment cites unknown passage_id "
                        f"{assessment.passage_id!r}"
                    )
                    continue
                # A quoted excerpt must actually appear in the cited passage.
                if (
                    assessment.quoted_excerpt
                    and assessment.quoted_excerpt not in text
                ):
                    problems.append(
                        f"quoted_excerpt not found in passage "
                        f"{assessment.passage_id!r}"
                    )
            for premise in result.mechanism_premises:
                for pid in (
                    *premise.supporting_passage_ids,
                    *premise.contradictory_passage_ids,
                ):
                    if pid not in valid_premise_refs:
                        problems.append(
                            f"premise cites unknown evidence id {pid!r}"
                        )
        return problems

    # -- coverage (reuses Stage 5A policy) ------------------------------------

    def _coverage(self, inputs: HypothesisTestingInputs) -> dict[str, list[dict]]:
        """Return coverage gaps per hypothesis, recomputed independently.

        When a plan is supplied, reuse :func:`evaluate_coverage` directly. When
        not, reconstruct the observed perspectives from the executed bundle and
        compare against :func:`applicable_perspectives` -- so a directly built
        plan cannot bypass the safeguard.
        """
        gaps_by_hyp: dict[str, list[dict]] = {}
        if inputs.plan is not None:
            for gap in evaluate_coverage(
                inputs.plan, inputs.hypotheses, inputs.budget
            ):
                gaps_by_hyp.setdefault(gap.hypothesis_id, []).append(
                    {
                        "missing_perspective": gap.missing_perspective,
                        "budget_permitted": gap.budget_permitted,
                    }
                )
            return gaps_by_hyp

        observed: dict[str, set] = {}
        for executed in inputs.bundle.executed:
            observed.setdefault(executed.enquiry.hypothesis_id, set()).add(
                executed.enquiry.requirement_kind
            )
        for hypothesis in inputs.hypotheses.hypotheses:
            required = applicable_perspectives(hypothesis)
            missing = required - observed.get(hypothesis.hypothesis_id, set())
            for perspective in sorted(missing, key=lambda p: p.value):
                gaps_by_hyp.setdefault(hypothesis.hypothesis_id, []).append(
                    {
                        "missing_perspective": perspective.value,
                        "budget_permitted": True,
                    }
                )
        return gaps_by_hyp

    # -- Layer A: deterministic, PREMISE-SPECIFIC factual grounding -----------

    # Trusted-metadata dimensions a premise may constrain against a metric.
    _METRIC_ATTRS = (
        ("claimed_concept", "concept"),
        ("claimed_entity", "entity"),
        ("claimed_period", "period"),
        ("claimed_scale", "scale"),
        ("claimed_currency", "currency"),
        ("claimed_accounting_basis", "accounting_basis"),
    )

    def _metric_link_notes(
        self, premise: MechanismPremise, inputs: HypothesisTestingInputs
    ) -> tuple[EvidenceItem | None, list[str]]:
        """Validate a premise's explicit metric link (Task 6C).

        A metric corroborates a premise ONLY through ``grounding_metric_id`` and
        only when (a) the id is an approved grounding item, (b) the item carries
        the required ``concept`` metadata, and (c) every attribute the premise
        claims matches the item's TRUSTED metadata. Any failure returns no usable
        item plus the reasons (fail-closed). A premise without a metric link
        returns (None, []) -- it may still ground against text passages.
        """
        if premise.grounding_metric_id is None:
            return None, []
        by_id = {i.evidence_id: i for i in inputs.grounding_items}
        item = by_id.get(premise.grounding_metric_id)
        if item is None:
            return None, [
                f"grounding_metric_id {premise.grounding_metric_id!r} is not an "
                "approved grounding item"
            ]
        notes: list[str] = []
        # Fix R2: the premise must SAY which concept it relies on; a link without
        # a claimed concept would let any approved number corroborate any text.
        if not premise.claimed_concept:
            return None, [
                f"premise links metric {premise.grounding_metric_id!r} without a "
                "claimed_concept; a metric corroborates only the concept it measures"
            ]
        # Required concept metadata: a metric with no declared concept cannot be
        # used to corroborate an economic proposition (fail-closed).
        if not item.concept:
            notes.append(
                f"metric {item.evidence_id!r} lacks required concept metadata; "
                "cannot corroborate concept"
            )
            return None, notes
        for claimed_attr, item_attr in self._METRIC_ATTRS:
            claimed = getattr(premise, claimed_attr)
            if claimed is None:
                continue
            actual = getattr(item, item_attr)
            if actual is None:
                notes.append(
                    f"metric {item.evidence_id!r} has no {item_attr} metadata to "
                    f"confirm claimed {item_attr}={claimed!r}"
                )
                return None, notes
            if str(actual).strip().lower() != str(claimed).strip().lower():
                notes.append(
                    f"metric {item_attr} mismatch: claimed {claimed!r} vs "
                    f"metric {actual!r}"
                )
                return None, notes
        return item, notes

    def _ground_premise(
        self,
        premise: MechanismPremise,
        inputs: HypothesisTestingInputs,
        passages: dict[str, str],
    ) -> tuple[FactualGroundingRecord | None, list[str]]:
        """Deterministically ground ONE premise against ITS OWN cited evidence.

        A metric contributes ONLY via a validated ``grounding_metric_id`` link
        (see :meth:`_metric_link_notes`): a number+unit match is never enough.
        Text passages the premise cites may still produce a (non-numeric)
        verdict. Returns ``(record, notes)``; ``notes`` records why a metric link
        was rejected (routed to the premise corroboration record).
        """
        if premise.grounding_claim is None:
            return None, []
        link_item, notes = self._metric_link_notes(premise, inputs)
        # Derive the grounded statement from the metric's trusted metadata where
        # a valid link exists, rather than trusting the model's wording.
        claim_text = premise.grounding_claim
        cited: list[str] = list(premise.supporting_passage_ids)
        items: list[EvidenceItem] = []
        if link_item is not None:
            items.append(link_item)
            if link_item.evidence_id not in cited:
                cited.append(link_item.evidence_id)
            claim_text = (
                f"{link_item.concept} is {link_item.value}"
                f"{(' ' + link_item.unit) if link_item.unit else ''}"
            )
        # Text passages the premise cites (narrative; never numeric corroboration).
        items += [
            EvidenceItem(evidence_id=pid, text=passages[pid])
            for pid in premise.supporting_passage_ids
            if pid in passages
        ]
        evaluator = GroundingEvaluator(EvidenceIndex(items))
        claim = AnalyticalClaim(
            claim_id=premise.premise_id or f"premise:{premise.premise[:24]}",
            text=claim_text,
            kind=ClaimKind.INTERPRETATION,
            evidence_ids=cited,
        )
        verdict = evaluator.evaluate_claim(claim)
        record = FactualGroundingRecord(
            claim_id=claim.claim_id,
            claim_text=claim_text,
            citation_present=verdict.citation_present,
            entailment_state=verdict.entailment_state,
            evidence_refs=list(verdict.evidence_refs),
            is_grounded=verdict.is_grounded,
            premise_id=premise.premise_id,
        )
        return record, notes

    def _corroborate_premises(
        self,
        result: HypothesisTestResult,
        inputs: HypothesisTestingInputs,
        passages: dict[str, str],
        verified_adjudications: tuple = (),
        hypothesis: CreditHypothesis | None = None,
    ) -> list[PremiseCorroboration]:
        """Compute per-premise corroboration (grounding OR AUTHORISED adjudication).

        Only ``verified_adjudications`` (confirmed against a real HumanReview by
        the service) are eligible -- a caller-constructed record that was not
        verified never corroborates (Task 6C).
        """
        records: list[PremiseCorroboration] = []
        for idx, premise in enumerate(result.mechanism_premises):
            pid = premise.premise_id or f"{result.hypothesis_id}:premise:{idx}"
            grounding, link_notes = self._ground_premise(premise, inputs, passages)
            basis: list[str] = []
            notes: list[str] = list(link_notes)
            if grounding is not None and grounding.is_grounded:
                basis.append("deterministic_grounding")
            # Fix R1 (widened after the live runs): a FACTUAL premise restated
            # from a verified verbatim quote is corroborated by the source
            # itself, whatever the hypothesis' classification. Never an outcome
            # premise. An inference still cannot rise above partially_supported
            # on such premises (R2 and the material-premise rule below).
            if (hypothesis is not None
                    and not premise.is_outcome_premise
                    and any(verbatim_fact_support(premise.premise, premise.grounding_claim,
                                                  passages[pid],
                                                  aliases=inputs.entity_aliases)
                            for pid in premise.supporting_passage_ids if pid in passages)):
                basis.append("verbatim_reported_fact")
            adj = self._verified_adjudication_for(
                verified_adjudications, result.hypothesis_id, premise.premise_id
            )
            if adj is not None:
                basis.append("human_adjudication")
                notes.append(
                    f"adjudicated by {adj.reviewer} via review {adj.review_id}"
                )
            if (
                grounding is not None
                and not grounding.is_grounded
                and premise.grounding_claim
            ):
                notes.append(
                    f"grounding verdict: {grounding.entailment_state.value}"
                )
            records.append(
                PremiseCorroboration(
                    premise_id=pid,
                    premise=premise.premise,
                    material=premise.material,
                    is_outcome_premise=premise.is_outcome_premise,
                    model_status=premise.status,
                    corroborated=bool(basis),
                    corroboration_basis=basis,
                    grounding=grounding,
                    notes=notes,
                )
            )
        return records

    def _verify_adjudications(self, adjudications: tuple) -> tuple:
        """Return only adjudications confirmed against a real HumanReview record.

        Fail-closed (Task 6C §5): without a configured ``review_lookup`` NONE are
        verified -- a bare caller-authored record is never trusted. For each
        record the referenced review must exist, be a corroborating action with a
        supporting decision, belong to the service's case, target the claimed
        hypothesis/premise, name an authorised reviewer, and not be revoked /
        superseded. A mismatch drops that record (and is audit-logged).
        """
        if not adjudications:
            return ()
        if self._review_lookup is None:
            self._record_adjudication_drop(
                [a.review_id for a in adjudications],
                reason="no review_lookup configured; adjudications cannot be "
                "verified (fail-closed)",
            )
            return ()
        verified: list = []
        dropped: list[str] = []
        for adj in adjudications:
            row = self._review_lookup(adj.review_id)
            canonical = AdjudicationRecord.from_human_review(row) if row else None
            ok = (
                canonical is not None
                and canonical.decision_supports
                and canonical.hypothesis_id == adj.hypothesis_id
                and canonical.premise_id == adj.premise_id
                and canonical.reviewer == adj.reviewer
                and (self._case_id is None or getattr(row, "case_id", None)
                     in (None, self._case_id))
                and not self._review_revoked(row)
                and self._reviewer_is_authorised(canonical.reviewer)
            )
            if ok:
                # Trust the DB-derived record, not the caller's copy.
                verified.append(canonical)
            else:
                dropped.append(adj.review_id)
        if dropped:
            self._record_adjudication_drop(
                dropped, reason="adjudication did not match an authorised review"
            )
        return tuple(verified)

    @staticmethod
    def _review_revoked(row) -> bool:
        """True if the review row is marked revoked/superseded (if supported)."""
        new_value = getattr(row, "new_value", None) or {}
        status = str(new_value.get("status", "")).lower()
        return status in {"revoked", "superseded", "withdrawn"}

    def _reviewer_is_authorised(self, reviewer: str) -> bool:
        if self._reviewer_authority is None:
            return bool(reviewer)  # presence required; authority hook optional
        return bool(self._reviewer_authority(reviewer))

    def _record_adjudication_drop(self, review_ids, *, reason: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            HYPOTHESIS_TESTING_REJECTED_EVENT,
            case_id=self._case_id,
            actor_type=ActorType.SYSTEM,
            after={"dropped_adjudications": list(review_ids), "reason": reason},
            reason="Unverified human adjudication(s) dropped (fail-closed).",
            linked_objects=[f"review:{rid}" for rid in review_ids],
        )

    @staticmethod
    def _verified_adjudication_for(
        verified: tuple, hypothesis_id: str, premise_id: str | None
    ):
        for rec in verified:
            if rec.hypothesis_id != hypothesis_id or not rec.decision_supports:
                continue
            if premise_id is not None and rec.premise_id == premise_id:
                return rec
            if rec.premise_id is None:
                return rec
        return None

    # -- governance / effective verdict ---------------------------------------

    def _govern(
        self,
        result: HypothesisTestResult,
        hypothesis: CreditHypothesis,
        inputs: HypothesisTestingInputs,
        coverage_gaps: list[dict],
        passages: dict[str, str],
        verified_adjudications: tuple = (),
        *,
        run_id: str,
    ) -> GovernedHypothesisTestResult:
        proposed = result.proposed_status
        effective = proposed
        reasons: list[str] = []

        has_contradiction = any(
            a.assessment is EvidenceAssessmentKind.CONTRADICTS_PREMISE
            for a in result.evidence_assessments
        )
        unresolved = list(result.unresolved_evidence_requirements)
        premises = result.mechanism_premises
        unproven_premises = [
            p for p in premises
            if p.status in {PremiseStatus.UNVERIFIED, PremiseStatus.CONTRADICTED}
        ]
        outcome_premises = [p for p in premises if p.is_outcome_premise]
        unproven_outcomes = [
            p for p in outcome_premises if p.status is not PremiseStatus.VERIFIED
        ]
        has_support_assessment = any(
            a.assessment is EvidenceAssessmentKind.SUPPORTS_PREMISE
            for a in result.evidence_assessments
        )

        # PREMISE-LEVEL corroboration (Task 6B): each premise is corroborated
        # only by ITS OWN deterministic grounding or an authorised adjudication
        # targeting it. The hypothesis is independently corroborated ONLY when
        # EVERY MATERIAL premise is corroborated -- one unrelated grounded fact
        # can no longer carry the whole hypothesis.
        premise_corroboration = self._corroborate_premises(
            result, inputs, passages, verified_adjudications, hypothesis
        )
        grounding = [pc.grounding for pc in premise_corroboration if pc.grounding]
        material_pcs = [pc for pc in premise_corroboration if pc.material]
        uncorroborated_material = [
            pc.premise for pc in material_pcs if not pc.corroborated
        ]
        any_corroborated = any(pc.corroborated for pc in premise_corroboration)
        all_material_corroborated = bool(material_pcs) and not uncorroborated_material

        corroboration_basis: list[str] = sorted(
            {b for pc in premise_corroboration for b in pc.corroboration_basis}
        )
        independently_corroborated = all_material_corroborated
        # Support resting only on the model's own judgement (no corroborated
        # material premise anywhere).
        model_only_support = has_support_assessment and not any_corroborated

        # 1. REJECTED must rest on real contradictory evidence, never absence.
        if proposed is _SUPPORTING.REJECTED and not has_contradiction:
            effective = _SUPPORTING.UNVERIFIED
            reasons.append(
                "proposed REJECTED without any contradicting-evidence assessment; "
                "missing/no-match is not grounds for rejection -> UNVERIFIED"
            )

        # 2. SUPPORTED is the conservative bar: block on any material gap.
        if proposed is _SUPPORTING.SUPPORTED:
            blockers: list[str] = []
            budget_permitted_gaps = [
                g for g in coverage_gaps if g.get("budget_permitted")
            ]
            if coverage_gaps:
                blockers.append(
                    "applicable investigation coverage is incomplete "
                    f"({[g['missing_perspective'] for g in coverage_gaps]})"
                )
            if has_contradiction:
                blockers.append("unresolved material contradictory evidence present")
            if unproven_premises:
                blockers.append(
                    f"{len(unproven_premises)} mechanism premise(s) unverified/contradicted"
                )
            if unproven_outcomes or (
                result.offset_effectiveness_evidence == [] and outcome_premises == []
                and _mentions_offset(hypothesis)
            ):
                # Existence without a verified outcome premise cannot be SUPPORTED.
                if unproven_outcomes:
                    blockers.append(
                        "outcome/effectiveness premise not verified (existence != effect)"
                    )
            if unresolved:
                blockers.append("unresolved evidence requirements remain")
            # Task 6B core fix: SUPPORTED requires that EVERY MATERIAL premise is
            # independently corroborated (its own grounding or an authorised
            # adjudication). One unrelated grounded fact is not enough; model
            # self-labelling never counts.
            if not material_pcs:
                blockers.append(
                    "no material premise was declared, so none is corroborated"
                )
            elif uncorroborated_material:
                blockers.append(
                    "material premise(s) lack independent corroboration: "
                    f"{uncorroborated_material}"
                )

            if blockers:
                # Downgrade conservatively. PARTIALLY_SUPPORTED needs at least one
                # GENUINELY (independently) corroborated premise; otherwise fall
                # to UNVERIFIED.
                effective = (
                    _SUPPORTING.PARTIALLY_SUPPORTED
                    if any_corroborated
                    else _SUPPORTING.UNVERIFIED
                )
                reasons.append(
                    "proposed SUPPORTED downgraded to "
                    f"{effective.value}: " + "; ".join(blockers)
                )
                _ = budget_permitted_gaps  # (kept explicit for audit clarity)

        # 3. PARTIALLY_SUPPORTED needs at least one INDEPENDENTLY corroborated
        # premise -- never merely a model-labelled one.
        if effective is _SUPPORTING.PARTIALLY_SUPPORTED and not any_corroborated:
            effective = _SUPPORTING.UNVERIFIED
            reasons.append(
                "PARTIALLY_SUPPORTED requires an independently corroborated "
                "premise; only model judgement present -> UNVERIFIED"
            )

        # Fix R1: a reported fact is either in the source or it is not. Partial
        # corroboration of a "fact" means part of it is not a fact.
        if (hypothesis.classification is ClaimClassification.REPORTED_FACT
                and effective is _SUPPORTING.PARTIALLY_SUPPORTED):
            effective = _SUPPORTING.UNVERIFIED
            reasons.append(
                "reported fact only partly corroborated by its sources -> UNVERIFIED")

        # Asymmetric conservatism: a credit STRENGTH must be demonstrated. A
        # positive hypothesis whose material outcome premise is uncorroborated
        # (existence shown, effect not) is unverified; a risk may stand as a
        # qualified possibility.
        uncorroborated_outcome = any(
            pc.is_outcome_premise and pc.material and not pc.corroborated
            for pc in premise_corroboration)
        if (hypothesis.credit_direction is CreditDirection.POSITIVE
                and uncorroborated_outcome
                and effective in (_SUPPORTING.SUPPORTED,
                                  _SUPPORTING.PARTIALLY_SUPPORTED)):
            effective = _SUPPORTING.UNVERIFIED
            reasons.append(
                "positive hypothesis: its outcome premise is not corroborated, so "
                "the strength is not demonstrated -> UNVERIFIED")

        # Fix R2: an inference whose material premises are ALL corroborated facts
        # (and none adjudicated) never states its inferential step as a premise.
        # Correct facts do not establish an unstated inference.
        adjudicated = any("human_adjudication" in pc.corroboration_basis
                          for pc in premise_corroboration)
        if (hypothesis.classification in (ClaimClassification.INFERENCE,
                                          ClaimClassification.ASSUMPTION)
                and all_material_corroborated and not adjudicated
                and effective in (_SUPPORTING.SUPPORTED,
                                  _SUPPORTING.PARTIALLY_SUPPORTED)):
            effective = _SUPPORTING.UNVERIFIED
            reasons.append(
                "inference rests only on corroborated factual premises; the "
                "inferential step is not declared as a premise -> UNVERIFIED")

        # An outcome/causal premise the model labelled established, but which no
        # independent channel corroborated, is a consequential model assertion.
        outcome_corroborated = all(
            pc.corroborated
            for pc in premise_corroboration
            if pc.is_outcome_premise
        ) if outcome_premises else True
        model_asserted_outcome = bool(outcome_premises) and not outcome_corroborated

        human_review = self._needs_human_review(
            hypothesis, result, has_contradiction, unproven_premises, coverage_gaps,
            grounding, effective, model_only_support, model_asserted_outcome,
        )

        return GovernedHypothesisTestResult(
            hypothesis_id=result.hypothesis_id,
            dimension=result.dimension,
            hypothesis_statement=result.hypothesis_statement,
            proposed_status=proposed,
            effective_status=effective,
            downgrade_reasons=reasons,
            factual_grounding=grounding,
            evidence_assessments=list(result.evidence_assessments),
            mechanism_premises=premises,
            alternative_explanations=list(result.alternative_explanations),
            unresolved_evidence_requirements=unresolved,
            coverage_gaps=coverage_gaps,
            independently_corroborated=independently_corroborated,
            corroboration_basis=corroboration_basis,
            model_only_support=model_only_support,
            premise_corroboration=premise_corroboration,
            uncorroborated_material_premises=uncorroborated_material,
            justification=result.justification,
            interpretation_confidence=result.interpretation_confidence,
            human_review_required=human_review,
            testing_run_id=run_id,
            generation_status=hypothesis.verification_status,
        )

    @staticmethod
    def _needs_human_review(
        hypothesis, result, has_contradiction, unproven_premises, coverage_gaps,
        grounding, effective, model_only_support, model_asserted_outcome,
    ) -> bool:
        """Flag material judgement for human credit-analyst review (Task 6 §13)."""
        if has_contradiction:
            return True
        if coverage_gaps:
            return True
        if unproven_premises:
            return True
        # A conclusion resting only on model judgement (no independent
        # corroboration) is consequential and must be reviewed (Task 6A).
        if model_only_support:
            return True
        # An outcome/causal premise established only by model labelling (not
        # independently corroborated) requires review when the finding is
        # material (Task 6A §4C).
        if model_asserted_outcome and effective in {
            HypothesisVerificationStatus.SUPPORTED,
            HypothesisVerificationStatus.PARTIALLY_SUPPORTED,
        }:
            return True
        # A material conclusion resting mainly on management assertions.
        if any(
            a.assessment is EvidenceAssessmentKind.DESCRIBES_INITIATIVE
            for a in result.evidence_assessments
        ) and not any(
            a.assessment is EvidenceAssessmentKind.MEASURED_OUTCOME
            for a in result.evidence_assessments
        ):
            return True
        if hypothesis.severity.value == "high" and effective in {
            HypothesisVerificationStatus.SUPPORTED,
            HypothesisVerificationStatus.PARTIALLY_SUPPORTED,
        }:
            return True
        if result.interpretation_confidence is not None and (
            result.interpretation_confidence < 0.5
        ):
            return True
        return False

    # -- audit ----------------------------------------------------------------

    def _record_rejection(self, run, *, stage: str, detail: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            HYPOTHESIS_TESTING_REJECTED_EVENT,
            case_id=self._case_id,
            actor_type=ActorType.SYSTEM,
            after={
                "run_id": run.run_id,
                "prompt_id": run.prompt_id,
                "stage": stage,
                "detail": detail,
            },
            reason=(
                "Hypothesis-testing output rejected at the service governance "
                f"boundary (stage={stage})."
            ),
            linked_objects=[f"model_run:{run.run_id}"],
        )


def _mentions_offset(hypothesis: CreditHypothesis) -> bool:
    return bool(hypothesis.offsetting_characteristics)
