# Tasks — Agentic Credit Analysis + Deterministic Scoring

Implementation is dependency-ordered into 22 milestones. Each task lists the code
areas affected, its dependencies, the expected tests, and acceptance conditions.
Do not begin production implementation until `requirements.md`, `design.md` and
this file are reviewed and approved.

Legend: **[DET]** component must never call an LLM; **[LLM]** component may call
an LLM; **[FLAG]** gated behind `analysis_mode="agentic"`.

---

## Milestone 1 — Typed contracts + additive migrations

- [x] 1.1 Add typed contracts (`app/schemas/agentic.py`): `AgenticAnalysisRun`,
  `ParameterResult`, `AgentTask`, `AgentRun`, `AgentExecutionResult`,
  `ConclusionClaim`, `TopicConclusion`, `StructuringConclusion`,
  `ChallengeFinding`, `CandidateStructure`, `CandidateFeasibility`, `RiskScore`,
  `EvidencePacket`, plus `*_JSON_SCHEMA` constants for agent outputs. Every
  analytical contract carries `analysis_run_id` (Remediation 1). Add append-only
  lineage/acceptance fields (`supersedes_id`, `parent_id`, `rerun_of`,
  `acceptance_state`) to `ParameterResult`, `RiskScore`, `TopicConclusion`,
  `CandidateFeasibility` (Remediations 12–13). `RiskScore` carries `status`
  (`final`/`provisional`/`unavailable`/`not_applicable`), nullable `band`,
  `missing_required_parameter_ids`, `critical_missing_parameter_ids`,
  `coverage_weight` (Remediation 3). `TopicConclusion` uses typed
  `ConclusionClaim`s for `overall_assessment` and material items (Remediation 4);
  `CandidateStructure` has NO mutable `selected` flag (Remediation 11). Enforce
  validators: deterministic ⇒ formula_id/version & no agent_run_id; llm/hybrid ⇒
  agent_id/run_id; risk_signal and evidence_quality independent;
  `ParameterResult.status` from the allowed enum.
  - _Areas:_ `app/schemas/agentic.py`, `app/schemas/enums.py` (new enums).
  - _Deps:_ none. _Requirements:_ 2, 3, 10, 15, 27, 31, 32, 35, 36.
  - _Tests:_ `tests/unit/test_agentic_contracts.py` — field presence, validator
    rejections (deterministic-with-agent_run_id, missing formula, bad enum);
    `analysis_run_id` required on every analytical contract; RiskScore allows
    unavailable with `band=None`; `ConclusionClaim` carries parameter/evidence
    IDs; `CandidateStructure` has no `selected` field.
  - _Done:_ models validate/serialize; invariants enforced; schemas importable.

- [x] 1.2 Add ORM tables (`agentic_analysis_runs`, `parameter_results`,
  `agent_runs`, `evidence_packets`, `topic_conclusions`, `challenge_findings`,
  `candidate_structures`, `candidate_feasibility`, `risk_scores`) and extend
  `app/migrate.py` `ADDITIONS` (additive only). Every analytical table carries
  `analysis_run_id` and, where versioned, lineage/acceptance columns. `agent_runs`
  carries `agent_definition_hash`, `response_schema_hash` and `cache_key`;
  `candidate_structures` has NO `selected` column; `risk_scores` has
  status/coverage columns; `topic_conclusions` has `selected_candidate_id`. Add
  new audit `event_type`s (incl. `analysis_run_created`,
  `descendant_invalidated`).
  - _Areas:_ `app/models/orm.py`, `app/migrate.py`, `app/services/audit/`.
  - _Deps:_ 1.1. _Requirements:_ 19, 24.5, 27, 32.
  - _Tests:_ `tests/unit/test_agentic_orm.py`, extend
    `tests/integration/test_hardening_migration.py` — tables created; migration
    idempotent; no existing payload rewritten; `analysis_run_id` FK present.
  - _Done:_ `init_db` creates tables; migration adds them to a legacy DB without
    touching evidence/snapshot/audit/review rows.

- [x] 1.3 Extend `FinalCaseSnapshot` (schema v1.1→v1.2, additive) with
  `accepted_analysis_run_id`, frozen accepted artifact IDs/hashes
  (`accepted_parameter_result_ids`, `accepted_score_ids`,
  `accepted_topic_conclusion_ids`, `selected_candidate_id`,
  `accepted_challenge_finding_ids`), `scores`, `challenge_outcomes`,
  `router_version`, `agent_registry_version/hash`, `scoring_config_version/hash`,
  `analysis_mode`; update JSON Schema.
  - _Areas:_ `app/schemas/snapshots.py`, `app/schemas/json_schema.py`.
  - _Deps:_ 1.1. _Requirements:_ 19.5, 20, 27.6, 33.3.
  - _Tests:_ `tests/unit/` snapshot round-trip; existing final-snapshot tests
    still pass with new optional fields absent; exactly one
    `accepted_analysis_run_id`.
  - _Done:_ old payloads validate unchanged; new fields optional and validated.

## Milestone 2 — Generic agent runtime [LLM choke point preserved]

- [x] 2.1 Add async-friendly, DB-free provider path to `LLMClient`
  (`generate_only(...)`) separating HTTP from logging; keep existing `_run`
  behavior for legacy.
  - _Areas:_ `app/services/llm/client.py`.
  - _Deps:_ 1.1. _Requirements:_ 6.5, 17.1.
  - _Tests:_ `tests/unit/test_llm_client.py` additions — `generate_only` performs
    no DB writes; logging still available separately.
  - _Done:_ provider call and persistence are separable; no session touched
    during `generate_only`.

- [x] 2.2 Agent runtime (`app/services/agents/runtime.py`): execute one
  `AgentTask` → raw result → parsed; build `AgentRun` (no persistence here).
  - _Areas:_ `app/services/agents/runtime.py`.
  - _Deps:_ 2.1. _Requirements:_ 19.2, 25.1.
  - _Tests:_ `tests/unit/test_agent_runtime.py` with `FakeLLMBackend`.
  - _Done:_ runtime produces an `AgentRun` with usage/latency placeholders and
    parsed response, no DB mutation.

## Milestone 3 — Evidence Router [DET]

- [x] 3.1 `EvidencePacket` builder (`app/services/agents/router.py`) with
  per-agent `evidence_selectors`, exclusion guarantees, `packet_hash` via
  `content_hash`, persistence to `evidence_packets`.
  - _Areas:_ `app/services/agents/router.py`, `app/core/hashing.py` (reuse).
  - _Deps:_ 1.1, 1.2. _Requirements:_ 4, 26.
  - _Tests:_ `tests/unit/test_evidence_router.py` — business-model packet has no
    covenant terms; covenant packet has no management bios; packet hash stable &
    changes with content; irrelevant evidence excluded; evidence treated as data.
  - _Done:_ packets are immutable, minimal, hashed, reconstructable; exclusions
    proven.

## Milestone 4 — Agent registry [DET]

- [x] 4.1 Registry (`app/services/agents/registry.py`) declaring all **27**
  logical LLM jobs (15 narrow / early extraction + 4 risk-to-mitigant + 4
  orchestrators + 4 challengers) with topic, task_type, deps, selectors, owned
  parameters, prompt refs, response-schema refs, model tier, and **declared input
  dependencies for descendant traversal** (Remediation 2). Compute a per-agent
  `agent_definition_hash` (owned params + selectors + deps + task type + response
  schema) and a `response_schema_hash` (Remediation 7); carry
  `agent_registry_version`/`agent_registry_hash`; load-time validation.
  - _Areas:_ `app/services/agents/registry.py`.
  - _Deps:_ 1.1. _Requirements:_ 5, 28, 29.
  - _Tests:_ `tests/unit/test_agent_registry.py` — reject dup ids, unknown deps,
    cycles, multi-owned parameters; **roster count = 27 logical jobs**; descendant
    traversal returns the correct dependent set for a given node; a semantic
    change moves `agent_definition_hash`; skip-reason recording.
  - _Done:_ registry validates; dependency graph acyclic; parameter ownership
    unique; definition/schema hashes stable and change on semantic edits.

## Milestone 5 — Async DAG executor + concurrency safety [DET orchestration of LLM calls]

- [x] 5.1 Executor (`app/services/agents/executor.py`): topological waves,
  `asyncio.TaskGroup` + `asyncio.Semaphore(AGENT_MAX_CONCURRENCY)`, per-call
  `asyncio.wait_for(AGENT_TIMEOUT_SECONDS)`; provider I/O awaited via
  `backend.generate_async(...)` where supported, else
  `asyncio.to_thread(backend.generate, ...)` so sync I/O never blocks the event
  loop (Remediation 5); each child task **catches its own provider exceptions**
  and returns a typed `AgentExecutionResult` (never raises into the TaskGroup, so
  siblings survive); flow build-inputs → concurrent-HTTP → gather typed results →
  validate → persist-serially; opens/updates the owning `AgenticAnalysisRun` and
  tags every artifact with `analysis_run_id`; supports descendant-invalidation
  recompute (Remediation 2) via the registry traversal.
  - _Areas:_ `app/services/agents/executor.py`, `app/core/config.py` (new
    settings), `app/services/agents/resolution.py` (accepted-artifact resolver).
  - _Deps:_ 2.2, 3.1, 4.1. _Requirements:_ 6, 25.3, 25.4, 27, 29, 30, 33.
  - _Tests:_ `tests/integration/test_dag_executor.py` — dependency ordering;
    concurrent-ready scheduling; semaphore bound respected; **no shared Session
    mutation during concurrent calls** (assert session untouched mid-wave);
    **event loop not blocked** (sync backend offloaded); **one provider failure
    while siblings complete** (sibling results preserved; failed `AgentRun`
    persisted); timeout → typed error; resume; two runs on the same snapshot stay
    isolated by `analysis_run_id`.
  - _Done:_ DAG runs with bounded concurrency; session safety and event-loop
    non-blocking proven; sibling isolation proven; failures explicit and
    resumable; runs isolated.

## Milestone 6 — Cache / idempotency [DET]

- [x] 6.1 Cache (`app/services/agents/cache.py`) keyed by `content_hash(analysis
  run context, case, snapshot, agent_id, agent_definition_hash, prompt_hash,
  response_schema_hash, model_id, model_config, router_version, packet_hash)`
  over `agent_runs.cache_key`; `reused_from_cache`; `force_regenerate`
  (Remediation 7).
  - _Areas:_ `app/services/agents/cache.py`.
  - _Deps:_ 1.2, 4.1, 5.1. _Requirements:_ 18, 28.
  - _Tests:_ `tests/unit/test_agent_cache.py` — reuse on unchanged inputs;
    invalidation on evidence/prompt/model/router/packet change **and on
    agent-definition/response-schema change** even when prompt text is unchanged;
    `force_regenerate` bypass; cached result had passed validation.
  - _Done:_ valid results reused; all invalidation triggers covered.

## Milestone 7 — Deterministic Parameter Engine [DET]

- [x] 7.1 `ParameterDefinitionRegistry` + business/financial/structuring formula
  modules (`app/services/parameters/`), reusing `MetricEngine`/
  `MetricDefinitionRegistry`/`TrendAnalyzer`/`PeerBenchmarker`; emit
  deterministic `ParameterResult`s with versioned `formula_id`; zero-safe states;
  `proposed_new_calculation` handling.
  - _Areas:_ `app/services/parameters/{registry,business,financial,structuring}.py`.
  - _Deps:_ 1.1. _Requirements:_ 7, 3.1, 3.4, 3.5.
  - _Tests:_ `tests/unit/test_parameter_engine.py` — HHI/concentration/trend/
    stress formulas; deterministic replay identical; zero/near-zero explicit
    states; proposed-new-calculation cannot enter scoring.
  - _Done:_ all listed deterministic parameters computed, versioned, replayable.

## Milestone 8 — Deterministic validation layer [DET]

- [x] 8.1 Validation pipeline (`app/services/agents/validation.py`) with an
  explicit split (Remediation 10). **Bucket A — deterministic evidence
  validation** (gates storage), reusing `jsonschema.validate` and reconciliation
  compatibility helpers: schema, evidence-ID existence, admitted-source, entity/
  period/unit compatibility, citation validity, source/page existence, exact
  numeric/structured-term correspondence (quoted number equals its referenced
  `ParameterResult`/deterministic evidence — also enforces orchestrator
  quote-not-calculate), allowed-enum, parameter-ownership. **Bucket B — narrative
  semantic support** (NOT labelled deterministic): reuse `GroundingEvaluator` with
  exact spans where possible; LLM-judge / challenge-agent / human / `not_verifiable`;
  citation presence alone insufficient.
  - _Areas:_ `app/services/agents/validation.py`.
  - _Deps:_ 1.1, 3.1, 4.1, 7.1. _Requirements:_ 8, 3.3, 3.6, 10, 34.
  - _Tests:_ `tests/unit/test_agent_validation.py` — reject schema-invalid,
    unsupported evidence IDs, invalid PDF/page refs, hallucinated numeric,
    wrong-entity, wrong-period, contradictory, non-owned parameter; hybrid numeric
    term passes only after evidence validation; financial-statement numeric cannot
    bypass canonical reconciliation; **quoted orchestrator number must match its
    referenced ParameterResult**; semantic support is not treated as a
    deterministic gate.
  - _Done:_ no result enters storage/orchestration without passing bucket A; every
    failure recorded with reason; bucket B kept distinct.

## Milestone 9 — Scoring engine [DET]

- [x] 9.1 `config/poc/scoring.json` (`ILLUSTRATIVE — NOT BANK POLICY`) + register
  `scoring` artifact kind in `ConfigRegistry` (`ARTIFACT_KINDS`) + bootstrap.
  - _Areas:_ `config/poc/scoring.json`, `app/core/config_registry.py`,
    `app/core/bootstrap.py`.
  - _Deps:_ 1.1. _Requirements:_ 9.3, 24.5.
  - _Tests:_ `tests/unit/test_config_registry.py` additions — scoring artifact
    loads, versions, hashes; label guard.
  - _Done:_ scoring config is a versioned/hashed registry artifact.

- [x] 9.2 `ScoringEngine` + `overlays` (`app/services/scoring/`): parameter risk
  signals, Business/Financial/Obligor/StructureProtection/Facility scores with
  `status`/`band`/`coverage_weight`/missing-dimension tracking (Remediation 3);
  floors/overrides; averaging cannot wash out severe risk; evidence quality never
  moves a band; **no silent weight renormalization** — coverage/critical-dimension
  config decides `final`/`provisional`/`unavailable`.
  - _Areas:_ `app/services/scoring/{engine,overlays}.py`.
  - _Deps:_ 7.1, 9.1. _Requirements:_ 9, 10, 31.
  - _Tests:_ `tests/unit/test_scoring_engine.py` — weighted scoring; floor on
    base-case covenant breach; DSCR-below-min forces insufficient; severe-risk not
    averaged away; LLM cannot modify score; evidence-quality separation; identical
    scores on replay; **missing dimension does NOT renormalize weights**; below
    min-coverage ⇒ `unavailable`; critical dimension missing ⇒ `unavailable`;
    partial-but-sufficient ⇒ `provisional` with `band`.
  - _Done:_ all five scores deterministic, reproducible, overlay-governed,
    config-driven; status/coverage honored; no silent renormalization.

## Milestone 10 — Business agents [LLM]

- [x] 10.1 Six Business agent prompts in `PROMPT_CATALOGUE`; wire agents to
  registry + router + runtime; owned parameters validated.
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 3.1, 4.1, 7.1, 8.1. _Requirements:_ 11.1, 5.
  - _Tests:_ `tests/unit/test_business_agents.py` with `FakeLLMBackend` — each
    agent emits only owned parameters; validation gates output; skip-on-absent.
  - _Done:_ six agents produce validated Business `ParameterResult`s offline.

## Milestone 11 — Financial agents [LLM]

- [x] 11.1 Six Financial agent prompts + wiring; forecast/stress agent emits
  drivers only (no arithmetic).
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 3.1, 4.1, 7.1, 8.1. _Requirements:_ 12.1, 12.2.
  - _Tests:_ `tests/unit/test_financial_agents.py` — covenant extraction yields
    hybrid numeric term (validated before deterministic use); forecast agent does
    no arithmetic.
  - _Done:_ six agents produce validated Financial `ParameterResult`s offline.

## Milestone 12 — Business/Financial orchestrators [LLM]

- [x] 12.1 `business.py` + `financial.py` orchestrators
  (`app/services/orchestration/`): consume only validated params + score +
  limitations; emit a `TopicConclusion` of typed `ConclusionClaim`s (each with
  `parameter_result_ids` + `evidence_ids`, Remediation 4). Reject **newly
  calculated/invented** numbers; **allow exact validated `ParameterResult` values
  with explicit `parameter_result_id` references** (Remediation 8) — validation
  bucket A compares quoted numbers to their referenced parameter.
  - _Areas:_ `app/services/orchestration/{business,financial}.py`,
    `app/prompts/templates.py`.
  - _Deps:_ 9.2, 10.1, 11.1. _Requirements:_ 11.2–11.3, 12.3–12.4, 14, 34, 35.
  - _Tests:_ `tests/integration/test_orchestrators.py` — conclusion claims carry
    specific parameter/evidence IDs; invented number rejected; exact referenced
    number accepted; contradictions surfaced not resolved.
  - _Done:_ both orchestrators emit valid claim-level conclusions; quote-not-
    calculate enforced.

## Milestone 13 — Business/Financial challengers + targeted rerun [LLM + DET loop]

- [x] 13.1 `challenge_loop.py` controller + Business/Financial challenge agents;
  `ChallengeFinding`s target an exact `claim_id`; targeted rerun that
  **(a) re-executes requested node(s), (b) compares changed output,
  (c) invalidates affected descendants via registry traversal, (d) recomputes the
  minimal dependent subgraph** (Remediation 2); outputs are append-only new
  versions with lineage (Remediation 12); accepted version resolved by
  `ResolutionService` (Remediation 13); `MAX_CHALLENGE_RERUN_ROUNDS=1`; unresolved
  → MANDATORY escalation.
  - _Areas:_ `app/services/orchestration/challenge_loop.py`,
    `app/services/agents/resolution.py`, `app/prompts/templates.py`,
    `app/services/escalation/` (new rule ids).
  - _Deps:_ 5.1, 12.1. _Requirements:_ 11.4–11.5, 12.5, 15, 29, 32, 33.
  - _Tests:_ `tests/integration/test_challenge_rerun.py` — challenge mutates
    nothing; changing one Business agent does NOT rerun unrelated Financial narrow
    agents; affected Business score/conclusion recompute; `ObligorRiskScore`
    recomputes; downstream Structuring nodes depending on the changed Business
    conclusion recompute; **no stale descendant survives**; Credit Orchestrator
    cannot consume stale descendants; prior versions preserved; one round max;
    unresolved material → escalation blocks finalize.
  - _Done:_ bounded targeted rerun with descendant invalidation works; no DAG-wide
    rerun; append-only lineage; no unbounded debate.

## Milestone 14 — Early Structuring extraction agents [LLM]

- [x] 14.1 Three extraction agents (`facility_terms`,
  `collateral_security_guarantee`, `legal_undertakings_conditions`) runnable in
  Wave 0 concurrently with Business/Financial.
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 3.1, 4.1, 8.1. _Requirements:_ 13.1.
  - _Tests:_ `tests/integration/test_structuring_extraction.py` — these agents
    schedule in Wave 0; produce validated extracted-term parameters.
  - _Done:_ early extraction concurrent and validated.

## Milestone 15 — Risk-to-mitigant agents [LLM]

- [x] 15.1 Four risk-to-mitigant agents gated on accepted Business/Financial
  conclusions + `ObligorRiskScore`; produce bounded mitigant proposals.
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 12.1, 13.1, 9.2. _Requirements:_ 13.2.
  - _Tests:_ `tests/integration/test_mitigant_agents.py` — do not schedule until
    conclusions + obligor score accepted; proposals bounded.
  - _Done:_ mitigant agents respect dependency gating.

## Milestone 16 — Deterministic structuring engine [DET]

- [x] 16.1 `candidates.py` + `engine.py` + `stress.py` (`app/services/
  structuring/`): assemble **immutable** `CandidateStructure`s; emit versioned
  append-only `CandidateFeasibility` per candidate (feasibility + policy tests);
  base/downside stress; infeasible candidates unselectable. **No mutable
  `selected` flag on candidates** (Remediation 11) — selection is recorded later
  on `StructuringConclusion`.
  - _Areas:_ `app/services/structuring/{candidates,engine,stress}.py`.
  - _Deps:_ 7.1, 14.1, 15.1. _Requirements:_ 13.3–13.5, 13.8, 7.4, 36.
  - _Tests:_ `tests/integration/test_structuring_engine.py` — candidate
    feasibility; rejection on policy violation; downside stress; infeasible cannot
    be selected; candidate objects are immutable (no `selected` write);
    feasibility results are versioned/append-only.
  - _Done:_ feasibility deterministic; candidates immutable; no invented/infeasible
    structure selectable.

## Milestone 17 — Structuring orchestrator/challenge + final cross-topic [LLM]

- [x] 17.1 Structuring orchestrator (selects feasible only via
  `StructuringConclusion.selected_candidate_id`, cannot override a deterministic
  failure; re-selection creates a new conclusion version preserving the prior,
  Remediation 11) + Structuring challenge; `StructureProtectionScore` +
  `FacilityRiskScore` via scoring engine (may be `unavailable` when inputs absent,
  Remediation 3).
  - _Areas:_ `app/services/orchestration/structuring.py`,
    `app/services/scoring/`, `app/prompts/templates.py`.
  - _Deps:_ 16.1, 9.2. _Requirements:_ 13.6–13.9, 9.6, 9.12, 36.
  - _Tests:_ `tests/integration/test_structuring_orchestration.py` — cannot select
    infeasible; selection recorded on the conclusion (not the candidate);
    re-selection preserves prior; unmitigated risks visible; facility vs obligor
    separation; `FacilityRiskScore` unavailable when inputs absent.
  - _Done:_ structuring conclusion + facility/protection scores produced;
    selection append-only.

- [x] 17.2 Credit orchestrator + cross-topic challenge
  (`app/services/orchestration/credit.py`): consume only accepted conclusions +
  scores + escalations; detect cross-topic contradictions; bounded reanalysis or
  escalation.
  - _Areas:_ `app/services/orchestration/credit.py`, `app/prompts/templates.py`.
  - _Deps:_ 13.1, 17.1. _Requirements:_ 16.
  - _Tests:_ `tests/integration/test_cross_topic.py` — recurring-vs-volatile,
    tight-liquidity-vs-no-protection, supplier-dependency-vs-no-provision
    contradictions caught; recomputes nothing.
  - _Done:_ coherent cross-topic draft; contradictions surfaced.

## Milestone 18 — Vertex backend + usage telemetry [LLM provider]

- [x] 18.1 `VertexProviderBackend(LLMBackend)` (`app/services/llm/
  providers_vertex.py`) via Google Gen AI/Vertex SDK + ADC; model-tier routing;
  usage capture; new `Settings`/`.env.example` keys; no hard-coded models; no
  indefinite retry.
  - _Areas:_ `app/services/llm/providers_vertex.py`, `app/core/config.py`,
    `.env.example`.
  - _Deps:_ 2.1, 5.1. _Requirements:_ 17, 25.
  - _Tests:_ `tests/unit/test_vertex_provider.py` — **mocked** request
    construction + structured-response parse; usage recorded; tier routing;
    missing-config error; **no live calls**.
  - _Done:_ Vertex selectable via config; abstraction intact; OpenAI/fake
    unaffected; CI has no live calls.

## Milestone 19 — Workbench / UI [DET, read-only]

- [x] 19.1 Extend `WorkbenchService` + Jinja2 partials with read-only agentic
  sections (parameters, provenance, formulas, scores, obligor/facility, evidence
  quality, agent runs, packets, prompt/model identity, token usage, challenges,
  reruns, candidate structures, policy failures, structure results, topic/cross-
  topic conclusions, exceptions, approvals).
  - _Areas:_ `app/services/workbench/service.py`, `app/api/workbench.py`,
    `app/api/templates/*.j2`, `app/api/inspection.py` (read view only).
  - _Deps:_ 1.2, 17.2. _Requirements:_ 21.
  - _Tests:_ `tests/integration/test_workbench_api.py` additions — new sections
    render; routes are GET; no mutation; `/inspect` mutation still gated.
  - _Done:_ full agentic inspection, genuinely read-only.

## Milestone 20 — Full evaluation / regression suite [DET]

- [x] 20.1 Extend `FakeLLMBackend` to the multi-agent graph (keyed by
  `(agent_id, key)`); add the full Req 22.2 test matrix not already covered, plus
  the remediation-specific cases below.
  - _Areas:_ `app/services/llm/client.py` (fake), `tests/unit`,
    `tests/integration`, `tests/end_to_end`.
  - _Deps:_ 5.1–17.2. _Requirements:_ 22, 27–37.
  - _Tests:_ historical reproducibility; `FinalCaseSnapshot` immutability under
    agentic mode; provider refusal/error; resume after partial failure; plus:
    **two analysis runs on the same evidence snapshot**; **no artifact leakage
    between runs**; **rerun descendant propagation**; **stale-output rejection**;
    **missing-score coverage** (unavailable/provisional, no renormalization);
    **unavailable vs not_applicable**; **claim-level provenance**; **cache
    invalidation on registry/agent-definition change**; **sibling provider
    failure** isolation; **orchestrator numeric-reference validation**;
    **immutable candidate selection**; all remaining Req 22.2 items.
  - _Done:_ full matrix green; no network in CI.

## Milestone 21 — Pipeline façade + mode switch + Delta acceptance [FLAG]

- [x] 21.1 `analysis_mode` (`legacy`|`agentic`) threaded through
  `CreditMemoPipeline` + `continue_preview` + inspection continue route; `_ai()`
  becomes a thin façade delegating to `AgenticAnalysisOrchestrator` (which opens
  exactly one `AgenticAnalysisRun`); legacy path unchanged; `FinalSnapshotAssembler`
  records the exact accepted `analysis_run_id` + frozen accepted artifact IDs and
  runs the **finalization consistency check** (Remediation 15).
  - _Areas:_ `app/services/pipeline/runner.py`,
    `app/services/pipeline/review.py`, `app/api/inspection.py`,
    `app/services/review/finalization.py`.
  - _Deps:_ 17.2, 20.1. _Requirements:_ 20, 24, 27, 37.
  - _Tests:_ `tests/integration/test_analysis_mode.py` — legacy default unchanged;
    agentic produces draft requiring sign-off; finalize blocked on open mandatory
    escalation; **finalize rejects stale/superseded/mixed-run/cross-run
    artifacts**; exactly one `accepted_analysis_run_id` frozen; baseline suite
    still green.
  - _Done:_ both modes coexist; legacy intact; agentic produces finalizable draft;
    consistency checks enforced.

- [x] 21.2 Delta end-to-end acceptance (`tests/end_to_end/test_delta_agentic.py`):
  full agentic path on the Delta fixture; missing structuring inputs → explicit
  Not Available / `unavailable` scores; prove agents consume saved reviewed
  evidence, not re-parsed source.
  - _Areas:_ `tests/end_to_end/`, Delta fixture.
  - _Deps:_ 21.1. _Requirements:_ 23, 9.12, 27.6, 33.3.
  - _Tests:_ Delta runs end-to-end offline; asserts the **exact accepted
    `analysis_run_id`**, the **exact accepted artifact IDs**, **no stale
    descendants**, the **complete claim-level provenance chain**, and
    **`unavailable` Structuring/Facility scores** when facility inputs are absent;
    a guard asserts no source re-parse (parser not invoked after snapshot).
  - _Done:_ Delta acceptance passes with correct missing-data and run-identity
    behavior.

## Milestone 22 — Documentation / hardening

- [x] 22.1 Update `README.md`, add an agentic analysis report doc, `.env.example`
  Vertex keys, config docs; verify audit/provenance chain end-to-end; final
  immutability/reproducibility checks; run-level usage/cost summary surfaced
  (Remediation 14).
  - _Areas:_ `README.md`, `docs/`/report md, `.env.example`.
  - _Deps:_ 21.2. _Requirements:_ 17, 19, 20, 25.
  - _Tests:_ `tests/end_to_end` provenance-chain assertion (memo sentence →
    `ConclusionClaim` → parameter → formula/agent run → evidence → source), all
    tagged by `analysis_run_id`; analysis-run usage summary (calls, cache hits,
    reruns, models, tokens, latency) available.
  - _Done:_ docs current; provenance chain proven; Definition of Done
    (below) satisfied.

---

## Dependency summary

```
M1 → M2 → M5
M1 → M3 → M5
M1 → M4 → M5
M1 → M7 → M8 → {M10, M11}
M1 → M9.1 → M9.2
{M9.2, M10, M11} → M12 → M13
{M3, M4, M8} → M14
{M12, M13, M9.2} → M15
{M7, M14, M15} → M16 → M17.1
{M13, M17.1} → M17.2
{M2, M5} → M18
{M1.2, M17.2} → M19
M5…M17.2 → M20 → M21.1 → M21.2 → M22
M6 depends on M1.2 + M5
```

## Notes for implementers

- Prefer extending existing modules (`MetricEngine`, `PromptRegistry`,
  `ConfigRegistry`, `FinalSnapshotAssembler`, `WorkbenchService`, `LLMClient`)
  over duplicating. Do not move existing functionality just to match the folder
  proposal.
- `_ai()` must not become a 27-call monolith; it is a façade over dedicated
  services.
- Every milestone keeps the baseline suite green (run against `analysis_mode=
  legacy`). Agentic tests use `FakeLLMBackend` only.
- Keep `[DET]` components LLM-free and `[LLM]` components arithmetic-free; the
  reviewer should be able to grep each service for the boundary.

## Consistency fixes applied (remediation pass)

1. Agent roster is **27 logical LLM jobs** (15 narrow/early + 4 risk-to-mitigant
   + 4 orchestrators + 4 challengers), never 23. Peak Wave 0 ≈ 15.
2. Orchestrators **cannot calculate** numbers; they may **cite exact validated**
   `ParameterResult` values by ID, verified in validation bucket A.
3. Missing evidence defaults to **`unavailable`**; **`not_applicable`** requires a
   real, deterministic/config-driven applicability decision.
4. **Narrative semantic entailment is not automatically deterministic** —
   deterministic bucket A vs semantic bucket B are separated.
5. **Candidate selection is append-only** (recorded on `StructuringConclusion`),
   not a mutable `selected` field.
6. **Targeted rerun includes dependent descendants** (invalidate + recompute),
   not merely the named node.
7. **Final snapshots reference exactly one accepted `analysis_run_id`** and freeze
   the exact accepted artifact IDs.

## Updated Definition of Done (additions to handoff §26)

Beyond the baseline DoD, implementation is NOT complete unless:

- the same evidence snapshot can support multiple isolated analysis runs;
- every downstream analytical object belongs to exactly one `analysis_run_id`;
- the final snapshot identifies one exact accepted analysis run;
- challenge reruns invalidate/recompute every affected descendant;
- stale descendants cannot enter orchestration or finalization;
- scores can be `final`/`provisional`/`unavailable`/`not_applicable`;
- missing weighted inputs are never silently renormalized;
- every material topic claim has an individual `claim_id`;
- every material claim links to specific `ParameterResult` IDs and evidence IDs;
- cache invalidates when an agent definition changes;
- async provider calls do not block the event loop;
- one provider failure does not discard successful sibling calls;
- candidate selection preserves all previous candidate states;
- rerun outputs are append-only;
- current/accepted artifacts are explicitly resolved (not by timestamp/PK/order);
- Delta with absent Structuring inputs returns `unavailable` Structuring/Facility
  scores rather than invented scores;
- finalization rejects mixed-run, stale or superseded analytical artifacts.
