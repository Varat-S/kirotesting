# Tasks — Agentic Credit Analysis + Deterministic Scoring

Implementation is dependency-ordered into 22 milestones. Each task lists the code
areas affected, its dependencies, the expected tests, and acceptance conditions.
Do not begin production implementation until `requirements.md`, `design.md` and
this file are reviewed and approved.

Legend: **[DET]** component must never call an LLM; **[LLM]** component may call
an LLM; **[FLAG]** gated behind `analysis_mode="agentic"`.

---

## Milestone 1 — Typed contracts + additive migrations

- [ ] 1.1 Add typed contracts (`app/schemas/agentic.py`): `ParameterResult`,
  `AgentTask`, `AgentRun`, `TopicConclusion`, `ChallengeFinding`,
  `CandidateStructure`, `RiskScore`, `EvidencePacket`, plus `*_JSON_SCHEMA`
  constants for agent outputs. Enforce validators: deterministic ⇒ formula_id/
  version & no agent_run_id; llm/hybrid ⇒ agent_id/run_id; risk_signal and
  evidence_quality independent.
  - _Areas:_ `app/schemas/agentic.py`, `app/schemas/enums.py` (new enums).
  - _Deps:_ none. _Requirements:_ 2, 3, 10, 15.
  - _Tests:_ `tests/unit/test_agentic_contracts.py` — field presence, validator
    rejections (deterministic-with-agent_run_id, missing formula, enum).
  - _Done:_ models validate/serialize; invariants enforced; schemas importable.

- [ ] 1.2 Add ORM tables (`parameter_results`, `agent_runs`, `evidence_packets`,
  `topic_conclusions`, `challenge_findings`, `candidate_structures`,
  `risk_scores`) and extend `app/migrate.py` `ADDITIONS` (additive only). Add new
  audit `event_type`s.
  - _Areas:_ `app/models/orm.py`, `app/migrate.py`, `app/services/audit/`.
  - _Deps:_ 1.1. _Requirements:_ 19, 24.5.
  - _Tests:_ `tests/unit/test_agentic_orm.py`, extend
    `tests/integration/test_hardening_migration.py` — tables created; migration
    idempotent; no existing payload rewritten.
  - _Done:_ `init_db` creates tables; migration adds them to a legacy DB without
    touching evidence/snapshot/audit/review rows.

- [ ] 1.3 Extend `FinalCaseSnapshot` (schema v1.1→v1.2, additive) with
  `parameter_results`, `topic_conclusions`, `scores`, `challenge_outcomes`,
  `candidate_structures`, `router_version`, `agent_registry_version`,
  `scoring_config_version/hash`, `analysis_mode`; update JSON Schema.
  - _Areas:_ `app/schemas/snapshots.py`, `app/schemas/json_schema.py`.
  - _Deps:_ 1.1. _Requirements:_ 19.5, 20.
  - _Tests:_ `tests/unit/` snapshot round-trip; existing final-snapshot tests
    still pass with new optional fields absent.
  - _Done:_ old payloads validate unchanged; new fields optional and validated.

## Milestone 2 — Generic agent runtime [LLM choke point preserved]

- [ ] 2.1 Add async-friendly, DB-free provider path to `LLMClient`
  (`generate_only(...)`) separating HTTP from logging; keep existing `_run`
  behavior for legacy.
  - _Areas:_ `app/services/llm/client.py`.
  - _Deps:_ 1.1. _Requirements:_ 6.5, 17.1.
  - _Tests:_ `tests/unit/test_llm_client.py` additions — `generate_only` performs
    no DB writes; logging still available separately.
  - _Done:_ provider call and persistence are separable; no session touched
    during `generate_only`.

- [ ] 2.2 Agent runtime (`app/services/agents/runtime.py`): execute one
  `AgentTask` → raw result → parsed; build `AgentRun` (no persistence here).
  - _Areas:_ `app/services/agents/runtime.py`.
  - _Deps:_ 2.1. _Requirements:_ 19.2, 25.1.
  - _Tests:_ `tests/unit/test_agent_runtime.py` with `FakeLLMBackend`.
  - _Done:_ runtime produces an `AgentRun` with usage/latency placeholders and
    parsed response, no DB mutation.

## Milestone 3 — Evidence Router [DET]

- [ ] 3.1 `EvidencePacket` builder (`app/services/agents/router.py`) with
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

- [ ] 4.1 Registry (`app/services/agents/registry.py`) declaring all 23 agent
  entries (15 narrow + 4 orchestrators + 4 challengers) with topic, task_type,
  deps, selectors, owned parameters, prompt refs, response-schema refs, model
  tier; load-time validation; registry version/hash.
  - _Areas:_ `app/services/agents/registry.py`.
  - _Deps:_ 1.1. _Requirements:_ 5.
  - _Tests:_ `tests/unit/test_agent_registry.py` — reject dup ids, unknown deps,
    cycles, multi-owned parameters; roster count = 27 base jobs; skip-reason
    recording.
  - _Done:_ registry validates; dependency graph acyclic; parameter ownership
    unique.

## Milestone 5 — Async DAG executor + concurrency safety [DET orchestration of LLM calls]

- [ ] 5.1 Executor (`app/services/agents/executor.py`): topological waves,
  `asyncio.TaskGroup` + `asyncio.Semaphore(AGENT_MAX_CONCURRENCY)`, per-call
  `asyncio.wait_for(AGENT_TIMEOUT_SECONDS)`; build-inputs → concurrent-HTTP →
  gather → validate → persist-serially.
  - _Areas:_ `app/services/agents/executor.py`, `app/core/config.py` (new
    settings).
  - _Deps:_ 2.2, 3.1, 4.1. _Requirements:_ 6, 25.2, 25.3.
  - _Tests:_ `tests/integration/test_dag_executor.py` — dependency ordering;
    concurrent-ready scheduling; semaphore bound respected; **no shared Session
    mutation during concurrent calls** (assert session untouched mid-wave);
    partial failure preserves siblings; timeout → explicit error; resume.
  - _Done:_ DAG runs with bounded concurrency; session safety proven; failures
    explicit and resumable.

## Milestone 6 — Cache / idempotency [DET]

- [ ] 6.1 Cache (`app/services/agents/cache.py`) keyed by case/snapshot/agent/
  prompt_hash/model/packet_hash over `agent_runs`; `reused_from_cache`;
  `force_regenerate`.
  - _Areas:_ `app/services/agents/cache.py`.
  - _Deps:_ 1.2, 5.1. _Requirements:_ 18.
  - _Tests:_ `tests/unit/test_agent_cache.py` — reuse on unchanged inputs;
    invalidation on evidence/prompt/model/router/packet change; `force_regenerate`
    bypass; cached result had passed validation.
  - _Done:_ valid results reused; all invalidation triggers covered.

## Milestone 7 — Deterministic Parameter Engine [DET]

- [ ] 7.1 `ParameterDefinitionRegistry` + business/financial/structuring formula
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

- [ ] 8.1 Validation pipeline (`app/services/agents/validation.py`) reusing
  `jsonschema.validate`, reconciliation compatibility helpers and
  `GroundingEvaluator`; checks: schema, evidence-ID existence, admitted-source,
  entity/period/unit compatibility, citation validity, value/source consistency,
  unsupported-numeric-invention, contradiction, grounding/entailment, enum,
  parameter-ownership.
  - _Areas:_ `app/services/agents/validation.py`.
  - _Deps:_ 1.1, 3.1, 4.1, 7.1. _Requirements:_ 8, 3.3, 3.6.
  - _Tests:_ `tests/unit/test_agent_validation.py` — reject schema-invalid,
    unsupported evidence IDs, invalid PDF/page refs, hallucinated numeric,
    wrong-entity, wrong-period, contradictory, non-owned parameter; hybrid numeric
    term passes only after evidence validation; financial-statement numeric cannot
    bypass canonical reconciliation.
  - _Done:_ no result enters storage/orchestration without passing; every failure
    recorded with reason.

## Milestone 9 — Scoring engine [DET]

- [ ] 9.1 `config/poc/scoring.json` (`ILLUSTRATIVE — NOT BANK POLICY`) + register
  `scoring` artifact kind in `ConfigRegistry` (`ARTIFACT_KINDS`) + bootstrap.
  - _Areas:_ `config/poc/scoring.json`, `app/core/config_registry.py`,
    `app/core/bootstrap.py`.
  - _Deps:_ 1.1. _Requirements:_ 9.3, 24.5.
  - _Tests:_ `tests/unit/test_config_registry.py` additions — scoring artifact
    loads, versions, hashes; label guard.
  - _Done:_ scoring config is a versioned/hashed registry artifact.

- [ ] 9.2 `ScoringEngine` + `overlays` (`app/services/scoring/`): parameter risk
  signals, Business/Financial/Obligor/StructureProtection/Facility scores; floors/
  overrides; averaging cannot wash out severe risk; evidence quality never moves a
  band.
  - _Areas:_ `app/services/scoring/{engine,overlays}.py`.
  - _Deps:_ 7.1, 9.1. _Requirements:_ 9, 10.
  - _Tests:_ `tests/unit/test_scoring_engine.py` — weighted scoring; floor on
    base-case covenant breach; DSCR-below-min forces insufficient; severe-risk not
    averaged away; LLM cannot modify score; evidence-quality separation; identical
    scores on replay.
  - _Done:_ all five scores deterministic, reproducible, overlay-governed,
    config-driven.

## Milestone 10 — Business agents [LLM]

- [ ] 10.1 Six Business agent prompts in `PROMPT_CATALOGUE`; wire agents to
  registry + router + runtime; owned parameters validated.
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 3.1, 4.1, 7.1, 8.1. _Requirements:_ 11.1, 5.
  - _Tests:_ `tests/unit/test_business_agents.py` with `FakeLLMBackend` — each
    agent emits only owned parameters; validation gates output; skip-on-absent.
  - _Done:_ six agents produce validated Business `ParameterResult`s offline.

## Milestone 11 — Financial agents [LLM]

- [ ] 11.1 Six Financial agent prompts + wiring; forecast/stress agent emits
  drivers only (no arithmetic).
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 3.1, 4.1, 7.1, 8.1. _Requirements:_ 12.1, 12.2.
  - _Tests:_ `tests/unit/test_financial_agents.py` — covenant extraction yields
    hybrid numeric term (validated before deterministic use); forecast agent does
    no arithmetic.
  - _Done:_ six agents produce validated Financial `ParameterResult`s offline.

## Milestone 12 — Business/Financial orchestrators [LLM]

- [ ] 12.1 `business.py` + `financial.py` orchestrators
  (`app/services/orchestration/`): consume only validated params + score +
  limitations; emit `TopicConclusion`; reject any emitted number/score.
  - _Areas:_ `app/services/orchestration/{business,financial}.py`,
    `app/prompts/templates.py`.
  - _Deps:_ 9.2, 10.1, 11.1. _Requirements:_ 11.2–11.3, 12.3–12.4, 14.
  - _Tests:_ `tests/integration/test_orchestrators.py` — conclusion fields
    present; orchestrator cannot introduce numbers/scores; contradictions
    surfaced not resolved.
  - _Done:_ both orchestrators emit valid conclusions computing nothing.

## Milestone 13 — Business/Financial challengers + targeted rerun [LLM + DET loop]

- [ ] 13.1 `challenge_loop.py` controller + Business/Financial challenge agents;
  `ChallengeFinding`s; targeted rerun of only `requested_rerun_scope`;
  `MAX_CHALLENGE_RERUN_ROUNDS=1`; unresolved → MANDATORY escalation.
  - _Areas:_ `app/services/orchestration/challenge_loop.py`,
    `app/prompts/templates.py`, `app/services/escalation/` (new rule ids).
  - _Deps:_ 12.1. _Requirements:_ 11.4–11.5, 12.5, 15.
  - _Tests:_ `tests/integration/test_challenge_rerun.py` — challenge mutates
    nothing; only affected agents/params rerun; scores recompute; one round max;
    unresolved material → escalation blocks finalize.
  - _Done:_ bounded targeted rerun works; no DAG-wide rerun; no unbounded debate.

## Milestone 14 — Early Structuring extraction agents [LLM]

- [ ] 14.1 Three extraction agents (`facility_terms`,
  `collateral_security_guarantee`, `legal_undertakings_conditions`) runnable in
  Wave 0 concurrently with Business/Financial.
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 3.1, 4.1, 8.1. _Requirements:_ 13.1.
  - _Tests:_ `tests/integration/test_structuring_extraction.py` — these agents
    schedule in Wave 0; produce validated extracted-term parameters.
  - _Done:_ early extraction concurrent and validated.

## Milestone 15 — Risk-to-mitigant agents [LLM]

- [ ] 15.1 Four risk-to-mitigant agents gated on accepted Business/Financial
  conclusions + `ObligorRiskScore`; produce bounded mitigant proposals.
  - _Areas:_ `app/prompts/templates.py`, `app/services/agents/*`.
  - _Deps:_ 12.1, 13.1, 9.2. _Requirements:_ 13.2.
  - _Tests:_ `tests/integration/test_mitigant_agents.py` — do not schedule until
    conclusions + obligor score accepted; proposals bounded.
  - _Done:_ mitigant agents respect dependency gating.

## Milestone 16 — Deterministic structuring engine [DET]

- [ ] 16.1 `candidates.py` + `engine.py` + `stress.py` (`app/services/
  structuring/`): assemble `CandidateStructure`s; feasibility + policy tests;
  base/downside stress; infeasible rejected and unselectable.
  - _Areas:_ `app/services/structuring/{candidates,engine,stress}.py`.
  - _Deps:_ 7.1, 14.1, 15.1. _Requirements:_ 13.3–13.5, 7.4.
  - _Tests:_ `tests/integration/test_structuring_engine.py` — candidate
    feasibility; rejection on policy violation; downside stress; infeasible cannot
    be selected.
  - _Done:_ feasibility deterministic; no invented/infeasible structure selectable.

## Milestone 17 — Structuring orchestrator/challenge + final cross-topic [LLM]

- [ ] 17.1 Structuring orchestrator (selects feasible only, cannot override
  deterministic failure) + Structuring challenge; `StructureProtectionScore` +
  `FacilityRiskScore` via scoring engine.
  - _Areas:_ `app/services/orchestration/structuring.py`,
    `app/services/scoring/`, `app/prompts/templates.py`.
  - _Deps:_ 16.1, 9.2. _Requirements:_ 13.6–13.7, 9.6.
  - _Tests:_ `tests/integration/test_structuring_orchestration.py` — cannot select
    infeasible; unmitigated risks visible; facility vs obligor separation.
  - _Done:_ structuring conclusion + facility/protection scores produced.

- [ ] 17.2 Credit orchestrator + cross-topic challenge
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

- [ ] 18.1 `VertexProviderBackend(LLMBackend)` (`app/services/llm/
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

- [ ] 19.1 Extend `WorkbenchService` + Jinja2 partials with read-only agentic
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

- [ ] 20.1 Extend `FakeLLMBackend` to the multi-agent graph (keyed by
  `(agent_id, key)`); add the full Req 22.2 test matrix not already covered.
  - _Areas:_ `app/services/llm/client.py` (fake), `tests/unit`,
    `tests/integration`, `tests/end_to_end`.
  - _Deps:_ 5.1–17.2. _Requirements:_ 22.
  - _Tests:_ historical reproducibility; `FinalCaseSnapshot` immutability under
    agentic mode; provider refusal/error; resume after partial failure; all
    remaining Req 22.2 items.
  - _Done:_ full matrix green; no network in CI.

## Milestone 21 — Pipeline façade + mode switch + Delta acceptance [FLAG]

- [ ] 21.1 `analysis_mode` (`legacy`|`agentic`) threaded through
  `CreditMemoPipeline` + `continue_preview` + inspection continue route; `_ai()`
  becomes a thin façade delegating to `AgenticAnalysisOrchestrator`; legacy path
  unchanged; `FinalSnapshotAssembler` records agentic fields.
  - _Areas:_ `app/services/pipeline/runner.py`,
    `app/services/pipeline/review.py`, `app/api/inspection.py`,
    `app/services/review/finalization.py`.
  - _Deps:_ 17.2, 20.1. _Requirements:_ 20, 24.
  - _Tests:_ `tests/integration/test_analysis_mode.py` — legacy default unchanged;
    agentic produces draft requiring sign-off; finalize blocked on open mandatory
    escalation; baseline suite still green.
  - _Done:_ both modes coexist; legacy intact; agentic produces finalizable draft.

- [ ] 21.2 Delta end-to-end acceptance (`tests/end_to_end/test_delta_agentic.py`):
  full agentic path on the Delta fixture; missing structuring inputs → explicit
  Not Available; prove agents consume saved reviewed evidence, not re-parsed
  source.
  - _Areas:_ `tests/end_to_end/`, Delta fixture.
  - _Deps:_ 21.1. _Requirements:_ 23.
  - _Tests:_ Delta runs end-to-end offline; Not-Available where inputs absent; a
    guard asserts no source re-parse (e.g. parser not invoked after snapshot).
  - _Done:_ Delta acceptance passes with correct missing-data behavior.

## Milestone 22 — Documentation / hardening

- [ ] 22.1 Update `README.md`, add an agentic analysis report doc, `.env.example`
  Vertex keys, config docs; verify audit/provenance chain end-to-end; final
  immutability/reproducibility checks.
  - _Areas:_ `README.md`, `docs/`/report md, `.env.example`.
  - _Deps:_ 21.2. _Requirements:_ 17, 19, 20, 25.
  - _Tests:_ `tests/end_to_end` provenance-chain assertion (memo claim →
    conclusion → parameter → formula/agent run → evidence → source).
  - _Done:_ docs current; provenance chain proven; Definition of Done
    (`requirements.md` DoD / handoff §26) satisfied.

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
