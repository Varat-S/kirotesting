"""Task 13 — controlled LIVE evaluation of Module 1 Stages 1-3.

This adapter runs the FIRST live-model evaluation of the Business Overview
Module 1 workflow against the approved real NVIDIA FY2026 10-K, limited to:

* **Stage 1 — autonomous hypothesis generation** (LIVE model via the existing
  :class:`HypothesisGenerationService`);
* **Stage 2 — autonomous evidence-enquiry planning** (LIVE model via the
  existing :class:`EvidenceEnquiryPlanningService`, Task 5A coverage enforced);
* **Stage 3 — deterministic evidence retrieval** (the existing
  :class:`EvidenceEnquiryCoordinator` + :class:`EvidenceSearchService`; NO model
  call -- retrieval is deterministic lexical search over the real corpus).

Stages after enquiry/retrieval (hypothesis testing, signals, completeness, RED)
are NOT run live here. There is NO autonomous loop and NO retry-with-more-calls:
a per-run model-call budget is enforced and the run fails visibly when exceeded.

What the model receives (source-context design, §7): a BOUNDED set of real
narrative passage excerpts drawn from the admitted primary 10-K, each tagged
with its exact passage id and HTML section, PLUS the permitted-evidence
allow-list restricted to exactly those passage ids. The model is NOT given the
whole filing, the scripted Task-12 hypotheses, scripted queries, any intended
findings, the HCA benchmark, or any answer key. It cannot cite a passage it was
not given (the existing generation-governance allow-list rejects out-of-scope
citations), and selection coverage / omissions are recorded for the report.

The real backend is injected (never constructed here with live credentials in
tests): callers pass a configured :class:`LLMBackend`. The default execution
path for tests remains the offline FakeLLMBackend; nothing here calls a paid
provider unless the user explicitly runs live mode with a configured provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.schemas.evidence import EvidenceCorpus, EvidencePassage
from app.schemas.llm import CreditDimension, CreditHypothesisSet
from app.services.analysis.enquiry_planning import (
    EnquiryBudget,
    EnquiryPlanningInputs,
    EvidenceEnquiryCoordinator,
    EvidenceEnquiryPlanningRejectedError,
    EvidenceEnquiryPlanningService,
)
from app.services.analysis.evidence_retrieval import EvidenceSearchService
from app.services.analysis.hypothesis_generation import (
    HypothesisGenerationInputs,
    HypothesisGenerationRejectedError,
    HypothesisGenerationService,
)
from app.services.analysis.module1_demo import (
    FIXTURES,
    DemoError,
    build_demo_scenario,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import LLMBackend, LLMClient, LLMRawResult, LLMRequest
from app.prompts.registry import PromptRegistry

# A clear banner for every live rendering; distinct from the offline banner.
LIVE_WARNING_BANNER = (
    "LIVE MODEL EVALUATION — REAL LLM OUTPUT FOR HYPOTHESIS GENERATION AND "
    "ENQUIRY PLANNING ONLY. RETRIEVAL IS DETERMINISTIC. NOT AN AUTONOMOUS "
    "CREDIT ASSESSMENT OR LENDING RECOMMENDATION."
)

# The explicit phrase a user must pass to acknowledge possible API charges.
COST_ACK_REQUIRED = True


class LiveEvaluationError(RuntimeError):
    """A user-facing live-evaluation failure (clear, credential-free message)."""


class CallBudgetExceededError(RuntimeError):
    """Raised when a live run would exceed its configured model-call budget."""


# ---------------------------------------------------------------------------
# Budget-enforcing backend wrapper (no recursion, visible failure)
# ---------------------------------------------------------------------------


class BudgetedBackend(LLMBackend):
    """Wrap a backend and HARD-CAP the number of model calls for one run.

    This is the §10 safety control: the first live evaluation permits only the
    bounded calls needed for hypothesis generation + enquiry planning. A call
    beyond the budget raises :class:`CallBudgetExceededError` -- the run fails
    visibly instead of looping. The wrapper also records per-call usage (token
    counts) exposed by the underlying provider's ``model_config``.
    """

    def __init__(self, inner: LLMBackend, *, max_calls: int) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self._inner = inner
        self._max_calls = max_calls
        self.calls = 0
        self.usage_log: list[dict] = []

    @property
    def model_id(self) -> str:
        return getattr(self._inner, "model_id", "unknown")

    def generate(self, request: LLMRequest) -> LLMRawResult:
        if self.calls >= self._max_calls:
            raise CallBudgetExceededError(
                f"Model-call budget of {self._max_calls} exhausted; refusing a "
                f"further call for method {request.method!r}. No automatic retry."
            )
        self.calls += 1
        raw = self._inner.generate(request)
        usage = (raw.model_config or {}).get("usage")
        self.usage_log.append({
            "call": self.calls,
            "method": request.method,
            "prompt_id": request.prompt.prompt_id,
            "model_id": raw.model_id,
            "usage": usage,
        })
        return raw

    def usage_totals(self) -> dict:
        prompt = completion = total = 0
        have_any = False
        for entry in self.usage_log:
            u = entry.get("usage")
            if not u:
                continue
            have_any = True
            prompt += u.get("prompt_tokens") or 0
            completion += u.get("completion_tokens") or 0
            total += u.get("total_tokens") or 0
        if not have_any:
            return {"reported": False}
        return {
            "reported": True,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }


# ---------------------------------------------------------------------------
# Live evaluation configuration + result
# ---------------------------------------------------------------------------


@dataclass
class LiveEvaluationConfig:
    """Bounds for the first live run (conservative defaults; §10)."""

    # Stage 1 (generation) + Stage 2 (planning) = 2 model calls. The default
    # cap allows exactly those with no slack for retries or extra stages.
    max_model_calls: int = 2
    # How many real passage excerpts to supply as source context, and how long
    # each excerpt may be. Bounded to respect the model-context budget (§7).
    max_context_passages: int = 48
    max_excerpt_chars: int = 900
    enquiry_budget: EnquiryBudget = field(default_factory=EnquiryBudget)
    # Optional configured per-1k-token USD prices (prompt, completion). When
    # absent, usage is reported WITHOUT inventing a dollar cost (§10).
    price_per_1k_prompt_usd: float | None = None
    price_per_1k_completion_usd: float | None = None


@dataclass
class SuppliedPassage:
    """One real passage excerpt actually supplied to the model."""

    passage_id: str
    section: str | None
    source_location: str | None
    char_offset: int | None
    excerpt: str


@dataclass
class SourceContext:
    """The bounded source context handed to the model, with coverage notes."""

    supplied: list[SuppliedPassage]
    supplied_passage_ids: list[str]
    omitted_passage_count: int
    total_passage_count: int
    sections_supplied: list[str]
    sections_in_corpus: list[str]
    selection_process: str
    coverage_limitations: list[str]
    max_excerpt_chars: int

    def allow_list(self) -> list[str]:
        return list(self.supplied_passage_ids)


@dataclass
class LiveEvaluationResult:
    """Everything the live report renders (real evidence + LIVE Stage 1-2)."""

    fixture_key: str
    company: str
    reporting_entity: str
    as_of_date: str
    evidence_cutoff: str
    primary_document_id: str
    admitted_sources: list[dict]
    rejected_sources: list[dict]
    source_extraction_limitations: list[str]
    corpus: EvidenceCorpus
    source_context: SourceContext
    model_id: str
    provider: str
    hypotheses: CreditHypothesisSet
    hypothesis_run_id: str | None
    hypothesis_prompt_id: str | None
    enquiry_plan: object
    enquiry_run_id: str | None
    enquiry_prompt_id: str | None
    candidate_evidence: object
    model_calls: int
    usage_totals: dict
    usage_log: list[dict]
    estimated_cost_usd: float | None
    unassessed_dimensions: list[str]


# ---------------------------------------------------------------------------
# Source-context construction (bounded, documented, allow-listed)
# ---------------------------------------------------------------------------


def build_source_context(
    corpus: EvidenceCorpus, cfg: LiveEvaluationConfig
) -> SourceContext:
    """Select a BOUNDED, section-diverse set of real passages for the model.

    Selection process (deterministic, documented): group the admitted primary
    10-K passages by HTML section (Item 1, Item 1A, Item 7, ...), then take
    passages round-robin across sections -- in each section preserving the
    source order (char_offset) -- until the passage cap is reached. This gives
    the model breadth across the filing so it can reason about ALL six credit
    dimensions, rather than dumping one uncontrolled blob or over-weighting a
    single section. Each supplied excerpt is truncated to the configured length
    and tagged with its exact passage id + section; the allow-list is restricted
    to exactly these ids so the model cannot cite anything it did not receive.
    """
    passages = list(corpus.passages)
    total = len(passages)

    by_section: dict[str, list[EvidencePassage]] = {}
    for p in passages:
        by_section.setdefault(p.section or "(unsectioned)", []).append(p)
    for sec in by_section:
        by_section[sec].sort(
            key=lambda x: (x.char_offset if x.char_offset is not None else 0)
        )

    # Round-robin across sections (sorted for determinism).
    ordered_sections = sorted(by_section)
    selected: list[EvidencePassage] = []
    idx = 0
    while len(selected) < cfg.max_context_passages:
        progressed = False
        for sec in ordered_sections:
            bucket = by_section[sec]
            if idx < len(bucket):
                selected.append(bucket[idx])
                progressed = True
                if len(selected) >= cfg.max_context_passages:
                    break
        if not progressed:
            break
        idx += 1

    supplied = [
        SuppliedPassage(
            passage_id=p.passage_id,
            section=p.section,
            source_location=p.source_location,
            char_offset=p.char_offset,
            excerpt=_excerpt(p.text, cfg.max_excerpt_chars),
        )
        for p in selected
    ]
    supplied_ids = [s.passage_id for s in supplied]
    sections_supplied = sorted({s.section or "(unsectioned)" for s in supplied})
    sections_all = sorted(by_section)

    limitations = []
    omitted = total - len(supplied)
    if omitted > 0:
        limitations.append(
            f"{omitted} of {total} admitted narrative passages were NOT supplied "
            "to the model (context budget); the model did not review the entire "
            "filing. Its reasoning is limited to the supplied excerpts.")
    truncated = [s for s in supplied
                 if len(s.excerpt) >= cfg.max_excerpt_chars]
    if truncated:
        limitations.append(
            f"{len(truncated)} supplied excerpt(s) were truncated to "
            f"{cfg.max_excerpt_chars} characters; the model saw a bounded excerpt, "
            "not the full passage.")
    missing_sections = sorted(set(sections_all) - set(sections_supplied))
    if missing_sections:
        limitations.append(
            f"Sections with no supplied passage: {missing_sections}.")
    limitations.append(
        "Only the primary 10-K narrative is supplied; exhibits, linkbases and "
        "numeric XBRL facts are admitted evidence but not part of the narrative "
        "context handed to the model for Stage 1.")

    return SourceContext(
        supplied=supplied,
        supplied_passage_ids=supplied_ids,
        omitted_passage_count=omitted,
        total_passage_count=total,
        sections_supplied=sections_supplied,
        sections_in_corpus=sections_all,
        selection_process=(
            "Round-robin across HTML sections (deterministic), preserving source "
            f"order within each section, capped at {cfg.max_context_passages} "
            f"passages and {cfg.max_excerpt_chars} characters per excerpt."),
        coverage_limitations=limitations,
        max_excerpt_chars=cfg.max_excerpt_chars,
    )


def _excerpt(text: str, limit: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Live evaluation runner (Stages 1-3)
# ---------------------------------------------------------------------------


def run_live_evaluation(
    fixture_key: str,
    session,
    *,
    backend: LLMBackend,
    provider: str,
    data_root,
    output_root,
    config: LiveEvaluationConfig | None = None,
) -> LiveEvaluationResult:
    """Run Stages 1-3 of Module 1 live for ``fixture_key``.

    ``backend`` is the (already-configured) model backend; the caller decides
    whether that is a real provider or a mock. This function NEVER constructs a
    live provider itself, so importing/running it is always network-free unless
    the caller passes a real backend."""
    if fixture_key not in FIXTURES:
        known = ", ".join(sorted(FIXTURES))
        raise LiveEvaluationError(
            f"Unknown fixture {fixture_key!r}. Available: {known}.")
    cfg = config or LiveEvaluationConfig()

    # Reuse the EXISTING real evidence preparation (admitted sources, hashes,
    # narrative corpus) -- but we DISCARD the scenario's scripted hypotheses,
    # scripted queries and scripted backend. Only the real evidence is used.
    try:
        scenario = build_demo_scenario(
            fixture_key, session, data_root=data_root, output_root=output_root)
    except DemoError as exc:
        raise LiveEvaluationError(str(exc)) from exc

    corpus = scenario.corpus
    primary_id = scenario.primary_document_id
    source_context = build_source_context(corpus, cfg)

    # One budgeted client over the injected backend; the SAME governance,
    # schema and audit path as every other run (no parallel framework).
    budgeted = BudgetedBackend(backend, max_calls=cfg.max_model_calls)
    audit = AuditLog(session)
    registry = PromptRegistry(session)
    registry.register_catalogue()
    case_id = scenario.case_id
    client = LLMClient(budgeted, prompts=registry, session=session, audit=audit)

    # ---- Stage 1: autonomous hypothesis generation (LIVE) -------------------
    gen_service = HypothesisGenerationService(client, audit=audit, case_id=case_id)
    gen_inputs = HypothesisGenerationInputs(
        # The allow-list is EXACTLY the supplied passage ids: the model may cite
        # only passages it received; the service rejects any other citation.
        evidence_ids=source_context.allow_list(),
        calculation_refs=[],  # no approved metrics supplied for the live Stage 1
        snippets=[
            {
                "evidence_id": s.passage_id,
                "document_id": primary_id,
                "section": s.section,
                "location": s.source_location,
                "text": s.excerpt,
            }
            for s in source_context.supplied
        ],
        data_limitations=source_context.coverage_limitations,
    )
    try:
        gen_result = gen_service.generate(gen_inputs, case_version=1)
    except HypothesisGenerationRejectedError as exc:
        raise LiveEvaluationError(
            f"Live hypothesis generation was rejected by governance at stage "
            f"'{exc.stage}'. The model output did not satisfy the schema / "
            "generation invariants; no hypotheses were accepted."
        ) from exc

    hypotheses = gen_result.hypotheses

    # ---- Stage 2: autonomous enquiry planning (LIVE) ------------------------
    plan_service = EvidenceEnquiryPlanningService(
        client, budget=cfg.enquiry_budget, audit=audit, case_id=case_id)
    plan_inputs = EnquiryPlanningInputs(
        hypotheses=hypotheses,
        permitted_document_ids=list(scenario.approved_document_ids),
    )
    try:
        plan_result = plan_service.plan(plan_inputs, case_version=1)
    except EvidenceEnquiryPlanningRejectedError as exc:
        raise LiveEvaluationError(
            f"Live enquiry planning was rejected by governance at stage "
            f"'{exc.stage}' (Task 5A coverage is enforced and was not weakened)."
        ) from exc

    # ---- Stage 3: deterministic evidence retrieval (NO model call) ----------
    coordinator = EvidenceEnquiryCoordinator(
        EvidenceSearchService(corpus), budget=cfg.enquiry_budget)
    bundle = coordinator.execute(plan_result.plan)

    # Dimensions the model did NOT generate a hypothesis for are UNASSESSED
    # (never NOT_APPLICABLE just because the model stayed silent, §6).
    covered = {h.dimension.value for h in hypotheses.hypotheses}
    unassessed = [d.value for d in CreditDimension if d.value not in covered]

    usage_totals = budgeted.usage_totals()
    estimated_cost = _estimate_cost(usage_totals, cfg)

    return LiveEvaluationResult(
        fixture_key=fixture_key,
        company=scenario.company,
        reporting_entity=scenario.reporting_entity,
        as_of_date=scenario.as_of_date,
        evidence_cutoff=scenario.evidence_cutoff,
        primary_document_id=primary_id,
        admitted_sources=scenario.admitted_sources,
        rejected_sources=scenario.rejected_sources,
        source_extraction_limitations=scenario.source_extraction_limitations,
        corpus=corpus,
        source_context=source_context,
        model_id=getattr(budgeted, "model_id", "unknown"),
        provider=provider,
        hypotheses=hypotheses,
        hypothesis_run_id=getattr(gen_result.run, "run_id", None),
        hypothesis_prompt_id=getattr(gen_result.run, "prompt_id", None),
        enquiry_plan=plan_result.plan,
        enquiry_run_id=getattr(plan_result.run, "run_id", None),
        enquiry_prompt_id=getattr(plan_result.run, "prompt_id", None),
        candidate_evidence=bundle,
        model_calls=budgeted.calls,
        usage_totals=usage_totals,
        usage_log=budgeted.usage_log,
        estimated_cost_usd=estimated_cost,
        unassessed_dimensions=unassessed,
    )


def _estimate_cost(usage_totals: dict, cfg: LiveEvaluationConfig) -> float | None:
    """Estimate USD cost ONLY when prices are configured; else None (§10)."""
    if not usage_totals.get("reported"):
        return None
    if cfg.price_per_1k_prompt_usd is None or cfg.price_per_1k_completion_usd is None:
        return None
    prompt = usage_totals.get("prompt_tokens") or 0
    completion = usage_totals.get("completion_tokens") or 0
    return round(
        prompt / 1000 * cfg.price_per_1k_prompt_usd
        + completion / 1000 * cfg.price_per_1k_completion_usd,
        6,
    )
