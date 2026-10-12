"""Task 12 adapter: feed the approved real NVIDIA corpus through Module 1.

Tasks 1-11 built and INTEGRATED the Business Overview Module 1 services, and
Task 11 proved the real NVIDIA FY2026 10-K corpus passes through retrieval and
the authoritative metric resolver IN ISOLATION. It did NOT, however, pass that
real corpus through the complete :class:`BusinessOverviewModule1Orchestrator`
(the orchestrator test uses a synthetic two-page fixture). This module is the
SMALLEST adapter that closes that gap: it loads the approved fixture, builds the
real searchable narrative corpus (Task 11's :func:`build_corpus_from_sec_
narrative`), anchors a small set of SCRIPTED hypotheses to ACTUAL retrieved
passages, and runs the EXISTING orchestrator unchanged.

What is REAL here:

* the source documents, their hashes, the reporting/evidence cutoff and the
  admitted/rejected set (produced by the existing SEC pipeline, read-only);
* the narrative passages, their verbatim text, HTML-native section locations and
  stable passage ids;
* the deterministic retrieval, premise grounding, materiality rubric,
  completeness gate, RED reference governance and escalation.

What is SCRIPTED (and must be labelled as such everywhere it surfaces):

* the hypothesis statements, the enquiry queries, the per-premise testing
  verdicts and the RED objections. These come from a :class:`FakeLLMBackend`;
  they are an OFFLINE TEST SCENARIO, not autonomous AI credit analysis and not a
  company insight independently discovered by a model.

The scripted hypotheses are deliberately conservative: they are seeded with the
business characteristics NVIDIA itself discloses in Item 1 / Item 1A, and every
premise's grounding is left to the deterministic grounding + authoritative
metric resolver. Nothing here promotes a verification status, nets an offset or
invents a number; a BLOCKED or escalation outcome is a legitimate result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.hashing import content_hash
from app.models.orm import Case
from app.prompts.registry import PromptRegistry
from app.schemas.evidence import EvidenceCorpus
from app.schemas.llm import CreditDimension
from app.services.analysis.credit_signal_assessment import (
    CreditSignalAssessmentService,
    MaterialityInputs,
)
from app.services.analysis.enquiry_planning import EvidenceEnquiryPlanningService
from app.services.analysis.evidence_completeness import (
    MaterialEvidenceCompletenessService,
)
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_sec_narrative,
)
from app.services.analysis.grounding import EvidenceItem
from app.services.analysis.hypothesis_generation import HypothesisGenerationService
from app.services.analysis.hypothesis_testing import HypothesisTestingService
from app.services.analysis.module1_orchestrator import (
    BusinessOverviewModule1Orchestrator,
    Module1Config,
    Module1Inputs,
)
from app.services.analysis.red_challenge import ModuleRedChallengeService
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.extraction.sec.bundle import SecFilingBundle
from app.services.llm.client import FakeLLMBackend, LLMClient
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.pipeline.sec import sec_source_package

# The one approved real fixture this demo accepts (Task 12 forbids arbitrary
# uploads). The key is the user-facing ``--fixture`` value.
FIXTURES: dict[str, dict] = {
    "nvda-2026": {
        "bundle": "examples/sec/nvda-2026/bundle.json",
        # Evidence extraction (the existing SEC pipeline) persists its own hash-
        # chained artifacts under this case; the Module-1 orchestration persists
        # its LLM-run artifacts under a DISTINCT case so the two append-only
        # chains never interleave. Both describe the same real NVIDIA filing.
        "evidence_case_id": "NVDA_2026_DEMO_EVIDENCE",
        "case_id": "NVDA_2026_DEMO",
        "reporting_entity": "NVIDIA Corporation",
        "as_of_date": "2026-01-25",          # 10-K report (period-end) date
        "evidence_cutoff": "2026-03-01T00:00:00Z",
        "company": "NVIDIA Corporation (CIK 0001045810)",
    },
}

# A fixed, documented scenario label so a scripted hypothesis is NEVER mistaken
# for an independently discovered company insight.
SCRIPTED_SCENARIO_PROVENANCE = (
    "SCRIPTED OFFLINE TEST SCENARIO (FakeLLMBackend) — not an autonomous "
    "model finding; anchored to real NVIDIA 10-K passages for demonstration."
)

# Scripted stage keys (one per generative stage; signals use the deterministic
# non-LLM path so no scripted signal response is needed).
_KEYS = {"generate": "gen", "plan": "plan", "test": "test", "red": "red"}

_DIM = CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value
_DIM_COMPETITION = CreditDimension.COMPETITIVE_DURABILITY.value


class DemoError(RuntimeError):
    """A user-facing demo prerequisite/validation failure (clear message)."""


@dataclass
class RetrievedPassagePreview:
    """A real retrieved passage surfaced in the review report (evidence viewer)."""

    passage_id: str
    document_id: str
    section: str | None
    source_location: str | None
    excerpt: str


@dataclass
class ScriptedHypothesisAnchor:
    """One scripted hypothesis and the REAL passage(s) it is anchored to."""

    hypothesis_id: str
    dimension: str
    statement: str
    business_characteristic: str
    anchor_passage_ids: list[str]
    provenance: str = SCRIPTED_SCENARIO_PROVENANCE


@dataclass
class DemoScenario:
    """Everything a demo run needs: real evidence + the scripted test scenario."""

    fixture_key: str
    company: str
    case_id: str
    reporting_entity: str
    as_of_date: str
    evidence_cutoff: str
    corpus: EvidenceCorpus
    approved_document_ids: list[str]
    primary_document_id: str
    admitted_sources: list[dict]
    rejected_sources: list[dict]
    source_extraction_limitations: list[str]
    grounding_items: list[EvidenceItem]
    validated_metric_ids: frozenset[str]
    materiality_inputs: dict[str, MaterialityInputs]
    not_applicable_dimensions: dict[str, str]
    anchors: list[ScriptedHypothesisAnchor]
    retrieved_preview: list[RetrievedPassagePreview]
    backend: FakeLLMBackend
    config: Module1Config


# ---------------------------------------------------------------------------
# Scenario construction (REAL evidence + scripted overlay)
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def build_demo_scenario(
    fixture_key: str, session, *, data_root: Path, output_root: Path
) -> DemoScenario:
    """Load the approved fixture and assemble a runnable, labelled scenario.

    This runs the EXISTING SEC pipeline once (read-only use) to obtain the
    authoritative admitted sources, hashes and real narrative passages, then
    builds the Task-11 searchable corpus and anchors scripted hypotheses to
    passages that genuinely matched a real retrieval query.
    """
    if fixture_key not in FIXTURES:
        known = ", ".join(sorted(FIXTURES))
        raise DemoError(
            f"Unknown fixture {fixture_key!r}. Available fixtures: {known}."
        )
    spec = FIXTURES[fixture_key]
    bundle_path = _repo_root() / spec["bundle"]
    if not bundle_path.exists():
        raise DemoError(
            f"Approved fixture bundle not found at {bundle_path}. The demo only "
            "runs the pre-approved SEC fixtures; it does not accept uploads."
        )

    cutoff = datetime.fromisoformat(spec["evidence_cutoff"].replace("Z", "+00:00"))
    source = SecFilingBundle.load(bundle_path)
    package = sec_source_package(source, cutoff=cutoff)
    # Read-only use of the existing runner: we never modify it and never finalize.
    # It runs under a dedicated evidence case so its append-only artifact chain
    # stays separate from the Module-1 orchestration's LLM-run chain.
    result = CreditMemoPipeline(
        session, data_root=data_root, output_root=output_root
    ).run_case(spec["evidence_case_id"], package=package)

    snapshot = result.evidence_snapshot
    diagnostics = snapshot.sec_filings[0]
    primary_id = diagnostics["primary_document_id"]
    primary_hash = diagnostics.get("primary_document_hash")

    # Real admitted narrative for the primary 10-K -> searchable corpus.
    primary_narrative = [
        p for p in snapshot.narrative_evidence if p["document_id"] == primary_id
    ]
    corpus = build_corpus_from_sec_narrative(
        primary_narrative, document_id=primary_id, document_hash=primary_hash
    )
    if not corpus.passages:
        raise DemoError(
            "The real 10-K produced no searchable narrative passages; cannot "
            "build a corpus. (Check the fixture and extraction output.)"
        )

    # Admitted / rejected source views (real).
    admitted_sources = [
        {
            "document_id": d.get("document_id"),
            "filename": d.get("filename"),
            "role": d.get("role"),
            "sha256": d.get("sha256") or d.get("document_hash"),
        }
        for d in snapshot.documents
    ]
    rejected_sources = [
        {
            "filename": r.get("filename"),
            "form": r.get("form"),
            "accession": r.get("accession"),
            "reason": r.get("reason") or "after evidence cutoff / out of scope",
        }
        for r in result.rejected_sources
    ]

    # Real source-extraction limitations (no OCR fabrication, deferred linkbases).
    limitations = [
        "SEC facts are extracted but remain status='unverified' until the "
        "approved deterministic reconciliation / human-review process runs; "
        "the demo does NOT upgrade them.",
        "HTML / inline-XBRL filing: passages carry HTML-native section locations; "
        "no PDF page numbers are fabricated.",
    ]
    deferred = diagnostics.get("deferred_linkbases") or []
    if deferred:
        limitations.append(f"Deferred linkbases at parse time: {sorted(deferred)}.")

    # ----- anchor scripted hypotheses to REAL retrieved passages -------------
    search = EvidenceSearchService(corpus)
    anchors, scripted = _build_scripted_scenario(search, corpus, primary_id)
    retrieved_preview = _preview_from_anchors(corpus, anchors)

    # Authoritative metric binding is intentionally EMPTY for the real demo: the
    # real NVIDIA facts are 'unverified' and the resolver (correctly) refuses
    # them, so there are no approved grounding items to supply. Supplying a
    # verified-looking metric here would bypass verification (Task 12 §6).
    grounding_items: list[EvidenceItem] = []
    validated_metric_ids: frozenset[str] = frozenset()

    backend = FakeLLMBackend()
    PromptRegistry(session).register_catalogue()

    materiality_inputs = {
        a.hypothesis_id: MaterialityInputs(
            cash_flow_relevant=True, high_magnitude=True, broad_or_concentrated=True
        )
        for a in anchors
    }
    # Task 12A: a dimension NOT investigated by this limited scripted demo is
    # NOT thereby inapplicable to NVIDIA. We therefore pass NO not-applicable
    # claims: the four uncovered dimensions flow to Stage 12 as
    # APPLICABILITY_UNCERTAIN / NOT_ASSESSED with a visible human-review gap
    # (the completeness service's correct default), never as an unsupported
    # NOT_APPLICABLE. We do not fabricate hypotheses to fill them, and we do not
    # assert an unverified non-applicability rationale.
    not_applicable: dict[str, str] = {}

    config = Module1Config(keys=dict(_KEYS))
    for method, key, payload in scripted:
        backend.register(method, payload, key=key)

    return DemoScenario(
        fixture_key=fixture_key,
        company=spec["company"],
        case_id=spec["case_id"],
        reporting_entity=spec["reporting_entity"],
        as_of_date=spec["as_of_date"],
        evidence_cutoff=spec["evidence_cutoff"],
        corpus=corpus,
        approved_document_ids=[primary_id],
        primary_document_id=primary_id,
        admitted_sources=admitted_sources,
        rejected_sources=rejected_sources,
        source_extraction_limitations=limitations,
        grounding_items=grounding_items,
        validated_metric_ids=validated_metric_ids,
        materiality_inputs=materiality_inputs,
        not_applicable_dimensions=not_applicable,
        anchors=anchors,
        retrieved_preview=retrieved_preview,
        backend=backend,
        config=config,
    )


def _first_match(search: EvidenceSearchService, query: str, doc_id: str) -> str | None:
    """Return the top real passage id for a query, or None if nothing matched."""
    from app.schemas.evidence import EvidenceSearchIntent, EvidenceSearchRequest

    [res] = search.search(EvidenceSearchRequest(
        queries=[query], permitted_document_ids=[doc_id],
        intent=EvidenceSearchIntent.GENERAL_INVESTIGATION, max_results=1,
    ))
    if res.ranked_passages:
        return res.ranked_passages[0].passage.passage_id
    return None


def _build_scripted_scenario(search, corpus, primary_id):
    """Anchor two scripted hypotheses to REAL passages and script every stage.

    Returns ``(anchors, scripted)`` where ``scripted`` is a list of
    ``(method, key, payload)`` to register on the FakeLLMBackend. The scenario is
    deliberately mixed: one hypothesis is driven to a BLOCKED outcome via an
    uncorroborated material premise (demonstrating the governance gate), proving
    the demo does not force a READY result.
    """
    # Two investigative themes NVIDIA actually discloses (Item 1 / Item 1A):
    # competitive durability and supply/manufacturing concentration.
    comp_anchor = _first_match(
        search, "competition competitors rapidly evolving markets", primary_id)
    supply_anchor = _first_match(
        search, "supply manufacturing capacity dependence third party", primary_id)
    # Fall back to the first passage if a specific query did not match, so the
    # demo still runs; the anchor is always a REAL passage id.
    default_pid = corpus.passages[0].passage_id
    comp_pid = comp_anchor or default_pid
    supply_pid = supply_anchor or default_pid

    anchors = [
        ScriptedHypothesisAnchor(
            hypothesis_id="h-comp",
            dimension=_DIM_COMPETITION,
            statement=(
                "Intense and rapidly evolving competition could pressure NVIDIA's "
                "pricing power and sustained demand over the credit horizon."
            ),
            business_characteristic=(
                "Competitive durability in a rapidly evolving semiconductor and "
                "accelerated-computing market."
            ),
            anchor_passage_ids=[comp_pid],
        ),
        ScriptedHypothesisAnchor(
            hypothesis_id="h-supply",
            dimension=_DIM,
            statement=(
                "Dependence on third-party manufacturing and constrained supply "
                "could cap output and compress operating margins."
            ),
            business_characteristic=(
                "Operating-model cost resilience given third-party manufacturing "
                "and supply concentration."
            ),
            anchor_passage_ids=[supply_pid],
        ),
    ]

    # --- Stage B: hypothesis generation (both unverified; no cited evidence so
    # generation governance stays satisfied -- generation never verifies). ----
    gen = {"hypotheses": [
        {
            "hypothesis_id": a.hypothesis_id, "dimension": a.dimension,
            "business_characteristic": a.business_characteristic,
            "statement": a.statement, "credit_direction": "negative",
            "economic_reasoning": "Disclosed exposure with a plausible credit "
            "transmission channel; stated as a scripted scenario for review.",
            "potential_credit_impact": "Potential margin / demand volatility.",
            "offsetting_characteristics": [],
            "classification": "inference", "verification_status": "unverified",
        }
        for a in anchors
    ]}

    # --- Stage C: enquiry planning (supporting + contradictory for each; the
    # negative direction also mandates an offsetting enquiry per Task 5A). -----
    def enq(eid, hid, kind, intent, query):
        return {
            "enquiry_id": eid, "hypothesis_id": hid, "dimension":
            next(a.dimension for a in anchors if a.hypothesis_id == hid),
            "requirement_kind": kind, "search_intent": intent,
            "investigative_question": "?", "search_query": query, "rationale": "r",
            "evidence_requirement": "req", "priority": 2,
            "permitted_document_ids": [primary_id],
        }
    plan = {"enquiries": [
        enq("c-sup", "h-comp", "supporting", "supporting_evidence",
            "competition competitors markets"),
        enq("c-con", "h-comp", "contradictory", "contradictory_evidence",
            "competitive advantages platform ecosystem moat"),
        enq("c-off", "h-comp", "offsetting", "offsetting_capability",
            "platform software ecosystem switching costs"),
        enq("s-sup", "h-supply", "supporting", "supporting_evidence",
            "supply manufacturing capacity dependence"),
        enq("s-con", "h-supply", "contradictory", "contradictory_evidence",
            "multiple suppliers capacity commitments mitigation"),
        enq("s-off", "h-supply", "offsetting", "offsetting_capability",
            "supply agreements capacity reservations inventory"),
    ], "planner_mode": "llm", "incomplete_coverage": []}

    # --- Stage E: hypothesis testing. The competition hypothesis is left
    # PARTIALLY supported with an UNCORROBORATED MATERIAL premise (no approved
    # grounding), which the completeness gate must treat as a material gap ->
    # BLOCKED. This proves the demo does not force a READY result. -------------
    test = {"results": [
        {
            "hypothesis_id": "h-comp", "dimension": _DIM_COMPETITION,
            "hypothesis_statement": anchors[0].statement,
            "evidence_assessments": [
                {"passage_id": comp_pid, "assessment": "supports_premise",
                 "rationale": "Disclosed competitive intensity (real passage)."}
            ],
            "relevant_factual_claims": [],
            "proposed_mechanism": "Competition pressures price/volume.",
            "mechanism_premises": [
                {"premise_id": "comp-exists", "premise":
                 "the company operates in an intensely competitive market",
                 "status": "verified", "material": True,
                 "supporting_passage_ids": [comp_pid],
                 "grounding_claim": "intense competition is disclosed"},
                {"premise_id": "comp-margin", "premise":
                 "competition will materially compress margins over the horizon",
                 "status": "unverified", "material": True,
                 "is_outcome_premise": True},
            ],
            "alternative_explanations": [],
            "offsetting_capabilities": [], "offset_effectiveness_evidence": [],
            "proposed_status": "partially_supported",
            "justification": "Existence disclosed; the material margin-impact "
            "premise is not independently corroborated.",
            "unresolved_evidence_requirements": [
                "measured evidence of competitive margin compression"],
            "interpretation_confidence": 0.5,
        },
        {
            "hypothesis_id": "h-supply", "dimension": _DIM,
            "hypothesis_statement": anchors[1].statement,
            "evidence_assessments": [
                {"passage_id": supply_pid, "assessment": "supports_premise",
                 "rationale": "Disclosed supply/manufacturing dependence (real)."}
            ],
            "relevant_factual_claims": [],
            "proposed_mechanism": "Supply constraints cap output.",
            "mechanism_premises": [
                {"premise_id": "sup-exists", "premise":
                 "the company depends on third-party manufacturing",
                 "status": "verified", "material": True,
                 "supporting_passage_ids": [supply_pid],
                 "grounding_claim": "third-party manufacturing dependence disclosed"},
                {"premise_id": "sup-impact", "premise":
                 "a supply constraint will materially reduce output this horizon",
                 "status": "unverified", "material": True,
                 "is_outcome_premise": True},
            ],
            "alternative_explanations": [],
            "offsetting_capabilities": [], "offset_effectiveness_evidence": [],
            "proposed_status": "partially_supported",
            "justification": "Dependence disclosed; the material output-impact "
            "premise is not independently corroborated.",
            "unresolved_evidence_requirements": [
                "measured evidence of a binding supply constraint"],
            "interpretation_confidence": 0.5,
        },
    ]}

    # --- Stage H: RED challenge. Because Stage 12 is expected to BLOCK (material
    # uncorroborated premises), the normal RED pass is withheld by the gate; the
    # demo keeps allow_diagnostic_red=False so the governance withholding is
    # visible. An (unused unless diagnostic) empty challenge set is registered. -
    red = {"challenges": []}

    scripted = [
        ("analyze", _KEYS["generate"], gen),
        ("analyze", _KEYS["plan"], plan),
        ("analyze", _KEYS["test"], test),
        ("challenge", _KEYS["red"], red),
    ]
    return anchors, scripted


def _preview_from_anchors(corpus, anchors) -> list[RetrievedPassagePreview]:
    by_id = {p.passage_id: p for p in corpus.passages}
    previews: list[RetrievedPassagePreview] = []
    seen: set[str] = set()
    for a in anchors:
        for pid in a.anchor_passage_ids:
            if pid in seen or pid not in by_id:
                continue
            seen.add(pid)
            p = by_id[pid]
            text = p.text.strip()
            excerpt = text if len(text) <= 480 else text[:477].rstrip() + "..."
            previews.append(RetrievedPassagePreview(
                passage_id=p.passage_id, document_id=p.document_id,
                section=p.section, source_location=p.source_location,
                excerpt=excerpt,
            ))
    return previews


# ---------------------------------------------------------------------------
# Orchestration (REAL services + scripted backend)
# ---------------------------------------------------------------------------


def run_demo_orchestration(scenario: DemoScenario, session):
    """Wire the REAL Module-1 services + the scripted backend and run the
    EXISTING orchestrator over the real corpus. Returns the real run object."""
    case_id = scenario.case_id
    audit = AuditLog(session)
    registry = PromptRegistry(session)
    client = LLMClient(scenario.backend, prompts=registry, session=session, audit=audit)

    # The pipeline already persisted the case; the orchestrator attaches audit
    # events to it. Ensure a Case row exists for escalation linkage.
    if session.get(Case, case_id) is None:
        session.add(Case(case_id=case_id, status="open"))
        session.flush()

    escalation = EscalationEngine(
        session, audit=audit, case_id=case_id, deterministic_ids=True)
    orch = BusinessOverviewModule1Orchestrator(
        hypothesis_service=HypothesisGenerationService(client, audit=audit, case_id=case_id),
        planning_service=EvidenceEnquiryPlanningService(client, audit=audit, case_id=case_id),
        search_service=EvidenceSearchService(scenario.corpus),
        testing_service=HypothesisTestingService(
            client, audit=audit, case_id=case_id, review_lookup=session),
        signal_service=CreditSignalAssessmentService(client, audit=audit, case_id=case_id),
        completeness_service=MaterialEvidenceCompletenessService(
            audit=audit, case_id=case_id),
        red_service=ModuleRedChallengeService(
            client, escalation=escalation, audit=audit, case_id=case_id),
        audit=audit,
    )
    inputs = Module1Inputs(
        case_id=case_id, case_version=1, reporting_entity=scenario.reporting_entity,
        as_of_date=scenario.as_of_date, evidence_cutoff=scenario.evidence_cutoff,
        approved_document_ids=list(scenario.approved_document_ids),
        corpus=scenario.corpus, grounding_items=list(scenario.grounding_items),
        validated_metric_ids=scenario.validated_metric_ids,
        materiality_inputs=scenario.materiality_inputs,
        not_applicable_dimensions=scenario.not_applicable_dimensions,
        config=scenario.config,
    )
    return orch.run(inputs)
