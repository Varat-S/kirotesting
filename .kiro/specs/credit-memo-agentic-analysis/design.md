# Design — Agentic Credit Analysis + Deterministic Scoring

## Overview

This design extends the existing credit-memo pipeline by replacing only the
**downstream analytical portion** (`CreditMemoPipeline._ai()`) with a bounded,
concurrent multi-agent architecture over a deterministic parameter, validation
and scoring substrate. Everything up to and including the human-reviewed
`CanonicalEvidenceSnapshot` is unchanged.

The architecture is deliberately deterministic-first:

> Code is the calculator, rule engine, scenario engine, scoring engine and policy
> gate. LLMs are interpreters, classifiers, challengers and synthesizers.

### What exists today (baseline, unchanged)

- `CanonicalEvidenceSnapshot` (schema v1.1, `app/schemas/snapshots.py`) — the
  immutable, human-reviewed hand-off object. Persisted in `snapshots`
  (`snapshot_type="canonical_evidence"`).
- Deterministic stack: `MetricEngine` + `MetricDefinitionRegistry`
  (`app/services/metrics/`), `TrendAnalyzer`, `PeerBenchmarker`
  (`app/services/benchmarking/peers.py`), `Reconciler`
  (`app/services/reconciliation/`), `PolicyRuleEvaluator` + `RuleRegistry`
  (`app/services/escalation/rules.py`), `EscalationEngine`
  (`app/services/escalation/engine.py`).
- Single AI choke point: `LLMClient` (`extract`/`analyze`/`challenge`) over
  `LLMBackend` with `FakeLLMBackend` (tests) and `RealProviderBackend`
  (OpenAI/compatible). Every run is schema-validated-before-use and logged to
  `model_runs` + `stage_artifacts` + `audit_events` (`app/services/llm/`).
- Versioned registries: `ConfigRegistry` (9 artifacts in `config/poc/*`),
  `PromptRegistry`, `MetricDefinitionRegistry` — all append-only + content-hashed.
- Immutable finalization: `FinalSnapshotAssembler` with a `before_flush`
  immutability guard; `finalize` requires a persisted human sign-off and no open
  mandatory escalation (`app/services/review/finalization.py`).
- Read-only `WorkbenchService` (`/cases`) and mutating local review surface
  `/inspect` (`app/api/inspection.py`, `app/api/workbench.py`).

### What this feature adds

New service packages (names adapt to repo conventions; prefer extension over
duplication):

```
app/services/agents/        registry, router, executor, runtime, validation, cache
app/services/parameters/    business, financial, structuring, registry (deterministic Parameter Engine)
app/services/scoring/       engine, overlays (deterministic scoring)
app/services/orchestration/ business, financial, structuring, credit, challenge_loop
app/services/structuring/   candidates, engine, stress (deterministic feasibility)
app/services/llm/providers_vertex.py   VertexProviderBackend(LLMBackend)
```

New typed contracts (`app/schemas/agentic.py` or similar): `ParameterResult`,
`AgentTask`, `AgentRun`, `TopicConclusion`, `ChallengeFinding`,
`CandidateStructure`, and the five scores. New ORM tables for persistence. A new
versioned config artifact `scoring` (`config/poc/scoring.json`) and a
`router_version`.

`CreditMemoPipeline._ai()` becomes a thin façade that, under
`analysis_mode="agentic"`, delegates to the orchestration services; under
`analysis_mode="legacy"` it runs the existing path unchanged.

---

## Architecture

### System-level data flow

```text
                 (unchanged baseline)                 |            (this feature)
SOURCE DOCS → INGEST/ENTITY/CUTOFF → PARSE → RECONCILE |
   → CanonicalEvidenceSnapshot (human-reviewed, immutable)
                        │
                        ▼
            AgenticAnalysisRun (analysis_run_id)  ← owns every artifact below
                        │
                        ▼
                EVIDENCE ROUTER  ── router_version, packet_hash ──┐
                        │                                         │
      ┌─────────────────┼───────────────────────────┐            │ cross-cutting:
      ▼                 ▼                             ▼            │  - AgentRun log
 DETERMINISTIC     NARROW LLM AGENTS           EARLY STRUCTURING   │  - append-only audit
 PARAMETER ENGINE  (Business×6, Financial×6)   EXTRACTION ×3       │  - cache/idempotency
 (metrics, HHI,         │                             │           │  - config/prompt/model
  trends, stress)       ▼                             │           │    versions + hashes
      │           deterministic VALIDATION ───────────┤           │  - token/latency usage
      └────────────────►│                             │           │
                        ▼                             │           │
              ParameterResult REGISTRY ───────────────┘           │
                        │                                         │
                        ▼                                         │
              DETERMINISTIC SCORING                                │
       (parameter signals → Business/Financial scores)            │
                        │                                         │
          ┌─────────────┴─────────────┐                           │
          ▼                           ▼                           │
   BUSINESS ORCHESTRATOR       FINANCIAL ORCHESTRATOR              │
          │ → BUSINESS CHALLENGE      │ → FINANCIAL CHALLENGE      │
          │   (targeted rerun ≤1)     │   (targeted rerun ≤1)      │
          ▼                           ▼                           │
   BusinessConclusion          FinancialConclusion                │
          └─────────────┬─────────────┘                           │
                        ▼                                         │
                  OBLIGOR RISK SCORE (deterministic)               │
                        │                                         │
                        ▼                                         │
   RISK-TO-MITIGANT AGENTS ×4 → CANDIDATE STRUCTURES              │
                        ▼                                         │
   DETERMINISTIC STRUCTURING ENGINE (feasibility/stress/policy)   │
                        ▼                                         │
   STRUCTURING ORCHESTRATOR → STRUCTURING CHALLENGE               │
                        ▼                                         │
   StructureProtectionScore + FacilityRiskScore (deterministic)   │
                        │                                         │
                        ▼                                         │
   CREDIT ORCHESTRATOR → CROSS-TOPIC CHALLENGE ───────────────────┘
                        │ (targeted reanalysis or escalation)
                        ▼
   HUMAN CREDIT REVIEW → immutable FinalCaseSnapshot → MEMO
```

### DAG / wave diagram

```mermaid
graph TD
  subgraph Wave0["Wave 0 — concurrent (bounded by AGENT_MAX_CONCURRENCY)"]
    BM[business_model]:::llm
    CP[competition_pricing]:::llm
    CSC[customer_supplier_contract]:::llm
    MG[management_governance]:::llm
    MCE[ma_capex_execution]:::llm
    RME[regulatory_material_events]:::llm
    EA[ebitda_adjustments]:::llm
    CW[cashflow_working_capital]:::llm
    DLT[debt_liquidity_terms]:::llm
    CE[covenant_extraction]:::llm
    AAQ[accounting_audit_quality]:::llm
    FSD[forecast_stress_drivers]:::llm
    FT[facility_terms]:::llm
    CSG[collateral_security_guarantee]:::llm
    LUC[legal_undertakings_conditions]:::llm
    DET[deterministic parameter engine]:::det
  end

  Wave0 --> VAL[deterministic validation]:::det
  VAL --> PR[ParameterResult registry]:::det
  PR --> SC1[Business + Financial scores]:::det

  SC1 --> BO[business_orchestrator]:::llm
  SC1 --> FO[financial_orchestrator]:::llm
  BO --> BC[business_challenge]:::llm
  FO --> FC[financial_challenge]:::llm
  BC -->|targeted rerun ≤1 + descendant invalidation| PR
  FC -->|targeted rerun ≤1 + descendant invalidation| PR
  BC --> BConc[BusinessConclusion]:::det
  FC --> FConc[FinancialConclusion]:::det

  BConc --> OBL[ObligorRiskScore]:::det
  FConc --> OBL

  OBL --> RM1[liquidity_refinancing_mitigant]:::llm
  OBL --> RM2[leverage_coverage_mitigant]:::llm
  OBL --> RM3[business_concentration_mitigant]:::llm
  OBL --> RM4[governance_information_mitigant]:::llm
  FT --> CAND
  CSG --> CAND
  LUC --> CAND
  RM1 --> CAND[CandidateStructure proposals]:::det
  RM2 --> CAND
  RM3 --> CAND
  RM4 --> CAND
  CAND --> STRENG[deterministic structuring engine]:::det
  STRENG --> SO[structuring_orchestrator]:::llm
  SO --> SCH[structuring_challenge]:::llm
  SCH --> SCONC[StructuringConclusion + Facility/Protection scores]:::det

  BConc --> CO[credit_orchestrator]:::llm
  FConc --> CO
  SCONC --> CO
  OBL --> CO
  CO --> XC[cross_topic_challenge]:::llm
  XC -->|targeted reanalysis + descendant invalidation, or escalation| PR
  XC --> DRAFT[draft FinalCaseSnapshot]:::det
  DRAFT --> HR[human review + finalize]:::human

  classDef llm fill:#e8f0ff,stroke:#3b6fd4;
  classDef det fill:#eafbe8,stroke:#2e8b2e;
  classDef human fill:#fff3e0,stroke:#d4873b;
```

Peak logically-ready jobs ≈ 15 (Wave 0). Actual provider concurrency is bounded
by `AGENT_MAX_CONCURRENCY` (default 6, configurable ≥15).

### Analysis-run lifecycle and accepted-artifact flow

Every execution is wrapped in one `AgenticAnalysisRun`; targeted reruns stay
inside the run, append new versions and invalidate dependent descendants; the
`FinalCaseSnapshot` freezes exactly one accepted run and its accepted artifacts.

```mermaid
graph TD
  CES[CanonicalEvidenceSnapshot]:::det --> AR[AgenticAnalysisRun<br/>analysis_run_id]:::det
  AR --> DAG[DAG execution]:::det
  DAG --> VA[versioned append-only artifacts<br/>parameters/scores/conclusions/candidates]:::det
  VA --> TR[targeted rerun<br/>on challenge]:::det
  TR --> DI[descendant invalidation + recompute]:::det
  DI --> VA
  VA --> ACC[accepted artifact set<br/>ResolutionService, acceptance_state]:::det
  ACC --> FCS[FinalCaseSnapshot<br/>accepted_analysis_run_id + frozen IDs]:::det
  FCS --> HR[human sign-off]:::human
  classDef det fill:#eafbe8,stroke:#2e8b2e;
  classDef human fill:#fff3e0,stroke:#d4873b;
```

### Who may call an LLM — explicit boundary

**MAY call an LLM:** the 15 narrow agents (Business×6, Financial×6, early
Structuring extraction×3), the 4 risk-to-mitigant agents, the 4 orchestrators
(Business, Financial, Structuring, Credit) and the 4 challenge agents (Business,
Financial, Structuring, Cross-Topic).

**MUST NEVER call an LLM:** the Evidence Router, the deterministic Parameter
Engine, the deterministic validation layer, the scoring subsystem, the
candidate-structure feasibility/stress engine, the cache layer and the
`FinalCaseSnapshot` assembler. All arithmetic, ratios, trends, concentration,
scenario/stress, covenant headroom, policy tests, scores, structural-graph
calculations and feasibility live here.

---

## Components and Interfaces

### Typed contracts (`app/schemas/agentic.py`)

Pydantic v2 models, `extra="forbid"`, mirroring the baseline style
(cf. `MetricResult`, `AnalysisResponse`). Each gets a `*_JSON_SCHEMA` for
agent-response validation where it is an agent output.

```python
Topic = Literal["business", "financial", "structuring", "cross_topic"]
Method = Literal["deterministic", "llm", "hybrid"]
RiskBand = Literal[1, 2, 3, 4]          # 1 strong … 4 high (ILLUSTRATIVE)
ParameterStatus = Literal["ok", "provisional", "unavailable", "not_applicable",
                          "proposed_new_calculation", "requires_review"]
EvidenceQuality = Literal["high", "medium", "low", "insufficient"]
AcceptanceState = Literal["accepted", "superseded", "rejected"]

class AgenticAnalysisRun(BaseModel):
    analysis_run_id: str
    case_id: str
    evidence_snapshot_version: int
    status: Literal["created","running","partially_failed","completed",
                    "blocked","superseded"]
    analysis_mode: Literal["agentic"]
    started_at: datetime
    completed_at: datetime | None = None
    parent_analysis_run_id: str | None = None
    supersedes_analysis_run_id: str | None = None
    router_version: str
    router_hash: str | None = None
    agent_registry_version: str
    agent_registry_hash: str
    scoring_config_version: int
    scoring_config_hash: str
    model_configuration: dict
    config_versions: dict
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None
    failure_state: str | None = None

class ParameterResult(BaseModel):
    parameter_result_id: str
    analysis_run_id: str                 # owning run (Remediation 1)
    parameter_id: str
    topic: Topic
    value: Any | None
    value_type: str                      # number|ratio|category|enum|text|schedule|bool
    method: Method
    status: ParameterStatus
    risk_signal: RiskBand | None         # credit risk — SEPARATE from evidence quality
    evidence_quality: EvidenceQuality | None
    confidence: float | None             # 0..1
    materiality: Literal["low","medium","high"] | None
    source_fact_ids: list[str] = []
    source_parameter_ids: list[str] = []
    evidence_ids: list[str] = []
    formula_id: str | None = None
    formula_version: str | None = None
    agent_id: str | None = None
    agent_run_id: str | None = None
    prompt_id: str | None = None
    model_id: str | None = None
    input_hash: str | None = None
    contradictions: list[str] = []
    missing_information: list[str] = []
    notes: str | None = None
    # append-only lineage / acceptance (Remediations 12–13)
    supersedes_id: str | None = None
    parent_id: str | None = None
    rerun_of: str | None = None
    acceptance_state: AcceptanceState = "accepted"
```

Invariants enforced by validators: a `deterministic` result requires
`formula_id` + `formula_version` and must not carry `agent_run_id`; an `llm`/
`hybrid` result requires `agent_id` + `agent_run_id`; `risk_signal` and
`evidence_quality` are independent (no validator derives one from the other).

```python
class AgentTask(BaseModel):
    agent_id: str
    analysis_run_id: str
    topic: Topic
    task_type: str                       # extract|interpret|classify|orchestrate|challenge|mitigant
    dependencies: list[str] = []
    packet_ref: str                      # evidence packet hash/id
    prompt_name: str
    response_schema_ref: str
    model_tier: Literal["narrow","orchestrator","challenge"]
    case_id: str
    snapshot_version: int
    rerun_of: str | None = None
    rerun_reason: str | None = None

class AgentRun(BaseModel):
    run_id: str
    analysis_run_id: str                 # owning run (Remediation 1)
    agent_id: str
    agent_definition_hash: str           # semantic identity (Remediation 7)
    topic: Topic
    case_id: str
    snapshot_version: int
    prompt_id: str; prompt_version: int; prompt_hash: str
    model_id: str; model_config: dict
    input_hash: str
    input_evidence_ids: list[str]
    raw_response: str | None
    parsed_response: dict | None
    validation_status: Literal["valid","rejected","error","skipped"]
    validation_detail: str | None
    parent_run_ids: list[str] = []
    execution_wave: int
    latency_ms: int | None
    usage: dict | None                   # {input_tokens, output_tokens, total_tokens}
    error_state: str | None
    rerun_reason: str | None
    reused_from_cache: bool = False

# Typed per-task execution result (Remediation 5): the executor child task
# ALWAYS returns one of these; provider exceptions are caught inside the task
# and never escape to cancel siblings in the TaskGroup.
class AgentExecutionResult(BaseModel):
    agent_id: str
    analysis_run_id: str
    status: Literal["ok","rejected","error","timeout","skipped"]
    parsed: dict | None = None
    raw_response: str | None = None
    error_type: str | None = None
    usage: dict | None = None
    latency_ms: int | None = None

class ConclusionClaim(BaseModel):          # claim-level provenance (Remediation 4)
    claim_id: str
    category: Literal["strength","weakness","driver","risk",
                      "assessment","mitigant","limitation"]
    text: str
    parameter_result_ids: list[str]
    evidence_ids: list[str]
    materiality: Literal["low","medium","high"]
    uncertainty: str | None = None

class TopicConclusion(BaseModel):
    topic: Topic
    analysis_run_id: str
    overall_assessment: ConclusionClaim    # typed claim, not a bare string
    strengths: list[ConclusionClaim]
    weaknesses: list[ConclusionClaim]
    key_drivers: list[ConclusionClaim]
    material_risks: list[ConclusionClaim]
    open_questions: list[str]
    unresolved_contradictions: list[str]
    score_reference: str                   # RiskScore id/version
    orchestrator_run_id: str
    challenge_status: Literal["clean","reran","escalated"]
    # append-only lineage
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = "accepted"

class StructuringConclusion(TopicConclusion):
    selected_candidate_id: str | None = None   # selection lives here, not on the candidate

class ChallengeFinding(BaseModel):
    challenge_id: str
    analysis_run_id: str
    target: str                          # exact ConclusionClaim.claim_id where possible
    affected_agent_ids: list[str]
    affected_parameter_ids: list[str]
    issue_type: str
    severity: Literal["low","medium","high","material"]
    reason: str
    evidence_ids: list[str]
    requires_reanalysis: bool
    requested_rerun_scope: list[str]     # agent_ids and/or parameter_ids
    rerun_of: str | None = None

class CandidateStructure(BaseModel):     # IMMUTABLE proposal (Remediation 11)
    candidate_id: str
    analysis_run_id: str
    facility_amount: float | None; tenor_months: int | None
    amortization: dict | None; covenant_package: list[dict] = []
    collateral: list[dict] = []; guarantees: list[dict] = []
    liquidity_protection: dict | None; cash_sweep: dict | None
    hedge_requirements: list[dict] = []; reporting_requirements: list[dict] = []
    other_protections: list[dict] = []
    # NO mutable `selected` flag — selection is recorded on StructuringConclusion

class CandidateFeasibility(BaseModel):   # versioned/append-only feasibility result
    feasibility_id: str
    analysis_run_id: str
    candidate_id: str
    feasible: bool                       # set only by the deterministic engine
    feasibility_detail: dict
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = "accepted"
```

Scores are typed objects produced only by the scoring subsystem. A score may be
`unavailable`/`provisional` with a `None` band (Remediation 3); missing weighted
dimensions are tracked explicitly and are never silently renormalized:

```python
class RiskScore(BaseModel):
    score_id: str
    analysis_run_id: str
    kind: Literal["business","financial","obligor","structure_protection","facility"]
    status: Literal["final","provisional","unavailable","not_applicable"]
    band: RiskBand | None                # None when not final
    scoring_config_version: int
    scoring_config_hash: str
    contributing_parameter_ids: list[str]
    missing_required_parameter_ids: list[str] = []
    critical_missing_parameter_ids: list[str] = []
    coverage_weight: float | None = None # share of required weight actually covered
    applied_overlays: list[str] = []     # floors/overrides that fired
    detail: dict                         # weighted components, pre/post-overlay
    method: Literal["deterministic"]     # frozen
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = "accepted"
```

### Evidence Router (`app/services/agents/router.py`) — never calls an LLM

Input: the validated `CanonicalEvidenceSnapshot`, derived deterministic
parameters/metrics, and the agent registry. Output: one immutable
`EvidencePacket` per agent.

```python
class EvidencePacket(BaseModel):
    case_id: str; snapshot_version: int; router_version: str
    agent_id: str
    facts: list[dict]; parameters: list[dict]
    narrative_evidence: list[dict]; entity_relationships: list[dict]
    facility_terms: list[dict]; covenant_terms: list[dict]
    open_conflicts: list[dict]; data_limitations: list[dict]
    evidence_ids: list[str]
    packet_hash: str                     # content_hash over the above (excl. packet_hash)
```

Routing is driven by each agent's declared `evidence_selectors` in the registry
(e.g. by `QualitativeTopic`, fact name patterns, metric names, entity-relation
kinds). The router guarantees exclusion (business-model packet has no covenant
terms; covenant packet has no management bios; EBITDA-adjustments packet has no
marketing text; collateral packet has no full revenue history). `packet_hash`
uses the existing `app/core/hashing.content_hash` and is persisted so a run is
reconstructable. Evidence is embedded as structured data under an explicit
`evidence` key in the prompt input; prompts instruct the model to treat it as
data only (prompt-injection boundary, Req 26).

### Deterministic Parameter Engine (`app/services/parameters/`) — never calls an LLM

Reuses `MetricEngine`/`MetricDefinitionRegistry` for the 11 existing financial
metrics and adds versioned formulas for the new deterministic parameters. Each
module (`business.py`, `financial.py`, `structuring.py`) registers formulas in a
`ParameterDefinitionRegistry` (same append-only + content-hash pattern as
`MetricDefinitionRegistry`), producing `ParameterResult`s with
`method="deterministic"`, `formula_id`, `formula_version`, `source_fact_ids`.
Zero-safe guards reuse the `MetricState` explicit-state pattern. The engine never
runs LLM-generated code; a model-proposed calculation with no registered formula
yields a `ParameterResult(status="proposed_new_calculation")` that cannot enter
scoring.

### Deterministic Validation (`app/services/agents/validation.py`) — never calls an LLM

Runs before any agent output becomes a `ParameterResult`. The layer makes an
explicit distinction (Remediation 10):

**A. Deterministic evidence validation** (genuinely deterministic; the only
checks labelled "validation" that gate storage): schema validity (reuse the
`jsonschema.validate` already in `LLMClient`); evidence-ID existence against the
packet + canonical snapshot; admitted-source membership; entity/period/unit
compatibility (reuse reconciliation compatibility helpers); citation validity;
source/page existence; **exact numeric/structured-term correspondence** — a
quoted number must equal its referenced `ParameterResult` or deterministic
evidence (also enforces orchestrator quote-not-calculate, Remediation 8);
allowed-enum checks; and parameter-ownership (agent may only emit parameters it
owns in the registry). Each returns a typed `ValidationIssue`; any failure marks
the result rejected with a reason and routes to targeted rerun or escalation; it
is never used.

**B. Narrative semantic support** (NOT labelled deterministic unless the check
truly is): for qualitative claims the layer requires exact evidence
references/spans where possible and reuses the existing `GroundingEvaluator`.
Semantic support may be judged by a structured LLM judge, by the topic challenge
agent, by human adjudication, or marked `not_verifiable`. Citation presence alone
is never sufficient support (baseline grounding principle). Deterministic numeric
entailment (value equality with unit/scale handling) stays in bucket A; arbitrary
narrative entailment lives here in bucket B.

### Scoring subsystem (`app/services/scoring/`) — never calls an LLM

`ScoringEngine(session, registry)` loads the versioned `scoring` config artifact
and computes, deterministically and reproducibly: parameter-level risk signals,
`BusinessRiskScore`, `FinancialRiskScore`, `ObligorRiskScore`,
`StructureProtectionScore`, `FacilityRiskScore`. `overlays.py` applies configured
floors/overrides (e.g. base-case covenant breach caps `FinancialRiskScore`; DSCR
< minimum forces repayment capacity `insufficient`). Overlays dominate weighted
averages so severe risks are never washed out. Evidence quality is read only to
mark results provisional/unavailable and to drive escalation — it never adjusts a
risk band (Req 10). Same inputs + config version ⇒ identical scores.

**Coverage and missing dimensions (Remediation 3).** When a weighted dimension is
missing, the engine SHALL NOT silently renormalize the remaining weights to 100%.
Instead it computes `coverage_weight` (the share of required weight actually
covered), records `missing_required_parameter_ids` and
`critical_missing_parameter_ids`, and sets `RiskScore.status` from the config:
`final` when coverage ≥ the configured minimum and no critical dimension is
missing; `provisional` when partial scoring is permitted below full coverage;
`unavailable` when coverage is below the configured minimum or a critical
dimension is missing. A `band` is produced only for `final`/`provisional`; an
`unavailable`/`not_applicable` score has `band = None`. For the Delta case, when
facility terms are absent, `StructureProtectionScore` and `FacilityRiskScore` are
`unavailable` rather than fabricated.

Example `config/poc/scoring.json` (illustrative):

```json
{
  "label": "ILLUSTRATIVE - NOT BANK POLICY",
  "scale": {"1": "low/strong", "2": "acceptable", "3": "elevated", "4": "high"},
  "parameter_bands": {
    "net_leverage": [{"max": 2.0, "band": 1}, {"max": 3.0, "band": 2},
                      {"max": 3.5, "band": 3}, {"band": 4}],
    "fcf_conversion": [{"min": 0.9, "band": 1}, {"min": 0.6, "band": 2},
                        {"min": 0.3, "band": 3}, {"band": 4}]
  },
  "business_weights": {"customer_concentration": 0.3, "competitive_position": 0.4,
                        "management_governance": 0.3},
  "financial_weights": {"net_leverage": 0.3, "interest_coverage": 0.2,
                         "fcf_conversion": 0.2, "liquidity": 0.3},
  "coverage": {
    "business": {"min_coverage": 0.6, "allow_partial": true,
                  "critical_dimensions": ["competitive_position"]},
    "financial": {"min_coverage": 0.7, "allow_partial": true,
                   "critical_dimensions": ["net_leverage", "liquidity"]},
    "renormalize_missing_weights": false
  },
  "obligor": {"business_weight": 0.4, "financial_weight": 0.6,
               "floors": [{"if": "base_case_covenant_breach", "financial_min_band": 3}]},
  "facility": {"obligor_weight": 0.7, "protection_weight": 0.3}
}
```

### Orchestration (`app/services/orchestration/`) — calls an LLM, computes nothing

Each orchestrator receives only validated inputs for its topic (parameters,
score, limitations/conflicts) via a routed packet and the `LLMClient`, and emits
a `TopicConclusion` of typed `ConclusionClaim`s through schema validation.
Orchestrators **may quote exact validated numbers but may never calculate new
ones** (Remediation 8): a quoted number must already exist in a validated
`ParameterResult`, the claim must reference that `parameter_result_id`, the value
must be copied exactly, and validation (bucket A) rejects any invented or
mismatched number — a forward/forecast figure is allowed only if it already
exists as a deterministic forecast `ParameterResult`. `challenge_loop.py`
implements the bounded targeted-rerun controller shared by all topics (see
below).

### Structuring engine (`app/services/structuring/`) — never calls an LLM

`candidates.py` assembles immutable `CandidateStructure`s from extracted terms +
bounded mitigant proposals. `engine.py` runs deterministic feasibility and policy
tests, emitting a versioned append-only `CandidateFeasibility` per candidate;
`stress.py` runs base/downside performance. Candidates whose accepted
`CandidateFeasibility.feasible` is `False` cannot be selected. **Selection is not
a mutable flag on the candidate** (Remediation 11): the Structuring Orchestrator
records the chosen candidate as `StructuringConclusion.selected_candidate_id`, and
choosing a different candidate later creates a new `StructuringConclusion` version
while preserving the prior one. The orchestrator chooses among feasible candidates
only and cannot override a deterministic failure.

---

## Concurrency semantics and DB/session safety

The executor (`app/services/agents/executor.py`) uses `asyncio` with a shared
`asyncio.Semaphore(AGENT_MAX_CONCURRENCY)` and `asyncio.TaskGroup`.

**Critical rule — no shared mutable `Session` across concurrent tasks.** The
sequence per wave:

1. **Build immutable inputs** (sync, single session): router builds packets;
   deterministic parameters computed; `AgentTask`s constructed. These are plain
   immutable data (packet dicts + hashes), not ORM-attached objects.
2. **Execute provider calls concurrently** (async, NO DB): each task awaits the
   provider via `backend.generate_async(request)` where supported, otherwise
   `await asyncio.to_thread(backend.generate, request)` — synchronous provider
   I/O never runs on the event loop (Remediation 5). The backend performs HTTP
   only; no task touches the SQLAlchemy session. **Each child task catches its own
   provider exceptions** and returns a typed `AgentExecutionResult` (status
   `ok`/`rejected`/`error`/`timeout`/`skipped`); exceptions do **not** escape to
   the `TaskGroup` where an unhandled child error could cancel siblings.
3. **Gather** the typed `AgentExecutionResult`s. Because failures are values, not
   exceptions, successful siblings always survive.
4. **Validate** gathered results serially (sync) — deterministic bucket A.
5. **Persist** serially on the main session (or via short-lived isolated
   sessions with explicit `begin()/commit()`), writing `AgentRun`,
   `ParameterResult` and audit rows (every artifact tagged with the owning
   `analysis_run_id`). This mirrors the baseline `_log_run` persistence that
   already happens serially.

Because `LLMClient._log_run` currently writes to the session inline, the agentic
executor uses a **deferred-logging** variant: the backend call and the logging
are split so HTTP happens off-session and logging happens in step 5. The
`LLMClient` gains an async-friendly `generate_only(...)`/`generate_async(...)`
path (no DB) plus the existing `_log_run(...)` reused in the serial persist phase.

### Failure states

- **Partial failure:** a child task returns `AgentExecutionResult(status="error")`
  (never raises); its `AgentRun` is persisted with `validation_status="error"` and
  `error_state`. Sibling results are preserved. Dependent descendants that
  required the failed output are skipped (recorded) or escalated.
- **Cancellation / timeout:** per-call `AGENT_TIMEOUT_SECONDS` via
  `asyncio.wait_for`; a timeout is captured in-task as
  `AgentExecutionResult(status="timeout")`.
- **Resume / idempotency:** on resume, the cache (below) returns previously valid
  results for unchanged identity inputs within the same `analysis_run_id`; only
  missing/invalidated jobs re-run.
- **Targeted rerun with descendant invalidation (Remediation 2):** see the
  dedicated section below — the rerun re-executes requested node(s) and then
  invalidates/recomputes dependent descendants only.
- **No indefinite retry:** paid calls are not auto-retried; reruns are explicit
  and audited.

### Caching / idempotency (`app/services/agents/cache.py`)

Cache key (Remediation 7) =
`content_hash(analysis_run context, case_id, evidence_snapshot_version, agent_id,
agent_definition_hash, prompt_hash, response_schema_hash, model_id, model_config,
router_version, packet_hash)`. A hit returns the prior **valid** parsed result and
is recorded as `AgentRun(reused_from_cache=True)`. Any change to evidence
snapshot, prompt, model, `router_version`, response schema, packet **or agent
definition semantics** (owned parameters, evidence selectors, dependencies, task
type, output schema — all folded into `agent_definition_hash`) changes the key and
invalidates reuse. `force_regenerate` bypasses the cache. The cache is backed by
the `agent_runs` table (query by key) — no separate store needed for the PoC.

### Accepted / current artifact resolution (Remediation 13)

Within an `analysis_run_id`, artifacts are append-only and may have multiple
versions. The accepted/current artifact is resolved **only** by explicit lineage/
status fields (`acceptance_state == "accepted"`, with `supersedes_id` chaining),
via a small deterministic `ResolutionService` — never by latest timestamp,
maximum primary key or insertion order. Orchestrators and finalization consume
only the `accepted` version; superseded versions remain queryable history. The
`FinalCaseSnapshot` freezes the exact accepted artifact IDs/hashes.

---

## Persistence model (additive migrations)

New ORM tables in `app/models/orm.py`, created via `app/models/base.init_db` and
back-filled by extending `app/migrate.py` `ADDITIONS` (additive only; no existing
payload rewritten). All follow the baseline pattern: append-only, content-hashed
where identity matters, with an accompanying audit event.

Every analytical table carries `analysis_run_id` (Remediation 1) and, where the
artifact is versioned by rerun, lineage/acceptance columns (`supersedes_id`,
`parent_id`, `rerun_of`, `acceptance_state`) (Remediations 12–13).

| Table | Purpose | Key columns |
|---|---|---|
| `agentic_analysis_runs` | one row per complete agentic execution (Remediation 1) | `analysis_run_id` PK, `case_id`, `evidence_snapshot_version`, `status`, `analysis_mode`, `started_at`, `completed_at`, `parent_analysis_run_id`, `supersedes_analysis_run_id`, `router_version`, `router_hash`, `agent_registry_version`, `agent_registry_hash`, `scoring_config_version`, `scoring_config_hash`, `model_configuration` JSON, `config_versions` JSON, `total_input_tokens`, `total_output_tokens`, `total_tokens`, `failure_state` |
| `parameter_results` | one row per `ParameterResult` version | `parameter_result_id` PK, `analysis_run_id` FK, `case_id`, `snapshot_version`, `parameter_id`, `topic`, `method`, `value`, `value_type`, `status`, `risk_signal`, `evidence_quality`, `confidence`, `materiality`, `source_fact_ids` JSON, `source_parameter_ids` JSON, `evidence_ids` JSON, `formula_id`, `formula_version`, `agent_id`, `agent_run_id`, `prompt_id`, `model_id`, `input_hash`, `contradictions` JSON, `missing_information` JSON, `notes`, `supersedes_id`, `parent_id`, `rerun_of`, `acceptance_state`, `created_at` |
| `agent_runs` | one row per agent execution (mirrors `model_runs`); also the cache store | `run_id` PK, `analysis_run_id` FK, `agent_id`, `agent_definition_hash`, `topic`, `case_id`, `snapshot_version`, `prompt_id/version/hash`, `response_schema_hash`, `model_id`, `model_config` JSON, `input_hash`, `cache_key`, `input_evidence_ids` JSON, `raw_response`, `parsed_response` JSON, `validation_status`, `validation_detail`, `parent_run_ids` JSON, `execution_wave`, `latency_ms`, `usage` JSON, `error_state`, `rerun_reason`, `reused_from_cache`, `created_at` |
| `evidence_packets` | routed packet record for reconstruction | `packet_hash` PK (per run/agent), `analysis_run_id` FK, `case_id`, `snapshot_version`, `router_version`, `agent_id`, `payload` JSON, `evidence_ids` JSON, `created_at` |
| `topic_conclusions` | conclusion versions (incl. `selected_candidate_id` for structuring) | `id` PK, `analysis_run_id` FK, `case_id`, `snapshot_version`, `topic`, `payload` JSON (typed `ConclusionClaim`s), `selected_candidate_id`, `challenge_status`, `orchestrator_run_id`, `supersedes_id`, `acceptance_state`, `created_at` |
| `challenge_findings` | challenge outputs | `challenge_id` PK, `analysis_run_id` FK, `case_id`, `snapshot_version`, `topic`, `target` (claim_id), `affected_agent_ids` JSON, `affected_parameter_ids` JSON, `issue_type`, `severity`, `reason`, `evidence_ids` JSON, `requires_reanalysis`, `requested_rerun_scope` JSON, `rerun_of`, `created_at` |
| `candidate_structures` | **immutable** structuring proposals (no `selected` flag) | `candidate_id` PK, `analysis_run_id` FK, `case_id`, `snapshot_version`, `payload` JSON, `created_at` |
| `candidate_feasibility` | versioned/append-only feasibility results | `feasibility_id` PK, `analysis_run_id` FK, `candidate_id` FK, `feasible`, `feasibility_detail` JSON, `supersedes_id`, `acceptance_state`, `created_at` |
| `risk_scores` | all five score kinds, with status/coverage | `score_id` PK, `analysis_run_id` FK, `case_id`, `snapshot_version`, `kind`, `status`, `band` (nullable), `scoring_config_version`, `scoring_config_hash`, `contributing_parameter_ids` JSON, `missing_required_parameter_ids` JSON, `critical_missing_parameter_ids` JSON, `coverage_weight`, `applied_overlays` JSON, `detail` JSON, `supersedes_id`, `acceptance_state`, `created_at` |

`agent_runs` doubles as the cache store (query by `cache_key`). `AgentRun` also
records a `StageArtifact` and an `AuditEvent` consistent with the baseline
`model_run_<id>` recording and `LLM_RUN` event, so the hash-chained processing
log remains complete. New audit `event_type`s: `analysis_run_created`,
`agent_run`, `parameter_computed`, `parameter_validated`, `score_computed`,
`challenge_created`, `rerun_triggered`, `descendant_invalidated`,
`candidate_evaluated` (added to the baseline enumeration in Req 18). Analysis-run
token totals are aggregated onto `agentic_analysis_runs` from the per-`AgentRun`
`usage` (Remediation 14); estimated cost, if shown, derives from a versioned rate
card and is never stored as the canonical usage record nor used in scoring.

### FinalCaseSnapshot extension

The `FinalCaseSnapshot` payload (schema v1.1 → v1.2, additive, `extra="forbid"`
relaxed only to add named fields) gains: `accepted_analysis_run_id` (exactly one,
Remediation 1), the frozen **accepted** artifact IDs/hashes
(`accepted_parameter_result_ids`, `accepted_score_ids`,
`accepted_topic_conclusion_ids`, `selected_candidate_id`,
`accepted_challenge_finding_ids`), `router_version`, `agent_registry_version`/
`agent_registry_hash`, `scoring_config_version`/`scoring_config_hash`, and
`analysis_mode`. The existing `prompt_model_versions`, `config_versions`,
`metric_definition_versions`, `rule_versions` continue to be recorded.
`FinalSnapshotAssembler` is extended to accept these; the immutability guard,
finalize gating (human sign-off + no open mandatory escalation) and supersede
behavior are unchanged, and a new **finalization consistency check**
(Remediation 15) runs before freezing — it rejects stale descendants, unresolved
material challenges/mandatory escalations, cross-run score references, conclusions
citing superseded parameters, a selection pointing to an infeasible candidate, a
`FacilityRiskScore` present when structuring inputs are unavailable unless its
status permits it, and unrecorded mixed router/prompt/scoring/registry versions.

---

## Model provider / Vertex (`app/services/llm/providers_vertex.py`)

`VertexProviderBackend(LLMBackend)` implements `generate(request) -> LLMRawResult`
using the officially supported Google Gen AI / Vertex SDK available at
implementation time, authenticating via ADC/service account (never committed
credentials). It requests structured JSON against the prompt's response schema,
uses temperature 0 where supported, enforces input-byte limits like
`RealProviderBackend`, and populates `model_config` with backend id, model id,
input/schema sha256 and token limits. Usage (input/output/total tokens, latency)
is captured into `LLMRawResult.model_config` / `AgentRun.usage`.

Provider/model selection extends `Settings` (`app/core/config.py`) and
`.env.example`:

```
LLM_PROVIDER=vertex
GOOGLE_CLOUD_PROJECT=...
GOOGLE_CLOUD_LOCATION=...
VERTEX_MODEL_NARROW=...
VERTEX_MODEL_ORCHESTRATOR=...
VERTEX_MODEL_CHALLENGE=...
AGENT_MAX_CONCURRENCY=6
AGENT_TIMEOUT_SECONDS=...
MAX_CHALLENGE_RERUN_ROUNDS=1
```

Model-tier routing maps each agent's registry `model_tier`
(`narrow|orchestrator|challenge`) to the corresponding `VERTEX_MODEL_*`. Model
names are never hard-coded. Optional cost estimation reads a versioned rate card;
token usage remains the canonical stored record. The abstraction stays
provider-neutral: OpenAI/compatible backends keep working unchanged, and the
`FakeLLMBackend` continues to drive all tests.

---

## Prompts and agent registry

Each agent has a versioned prompt in `PROMPT_CATALOGUE` (`app/prompts/templates.py`)
registered via the existing `PromptRegistry` (content-hashed, regression-flagged).
Prompt names follow the agent ids (e.g. `business_model`, `covenant_extraction`,
`financial_orchestrator`, `cross_topic_challenge`). The agent registry
(`app/services/agents/registry.py`) declares the full roster, dependencies,
`evidence_selectors`, owned parameters, response-schema refs and model tiers, and
is validated at load (no dup ids, no unknown/cyclic deps, no multi-owned
parameters). The registry has its own version/hash recorded in the final snapshot.

### Agent roster (27 registered logical LLM jobs)

The roster is **27 logical LLM jobs**: 15 narrow / early extraction + 4
risk-to-mitigant + 4 orchestrators + 4 challengers. Peak initially-ready Wave 0
jobs are approximately 15.

Business narrow (6): `business_model`, `competition_pricing`,
`customer_supplier_contract`, `management_governance`, `ma_capex_execution`,
`regulatory_material_events`. Financial narrow (6): `ebitda_adjustments`,
`cashflow_working_capital`, `debt_liquidity_terms`, `covenant_extraction`,
`accounting_audit_quality`, `forecast_stress_drivers`. Early Structuring
extraction (3): `facility_terms`, `collateral_security_guarantee`,
`legal_undertakings_conditions`. Risk-to-mitigant (4):
`liquidity_refinancing_mitigant`, `leverage_coverage_mitigant`,
`business_concentration_mitigant`, `governance_information_mitigant`.
Orchestrators (4): `business_orchestrator`, `financial_orchestrator`,
`structuring_orchestrator`, `credit_orchestrator`. Challengers (4):
`business_challenge`, `financial_challenge`, `structuring_challenge`,
`cross_topic_challenge`.

An agent with absent/not-applicable evidence is skipped with a recorded reason.
Its owned parameters follow the strict distinction (Remediation 9): status
`not_applicable` only when deterministic/config-driven applicability logic
determines the parameter genuinely does not apply (e.g. inventory turnover with no
inventory concept; collateral LTV for an explicitly unsecured facility);
otherwise, for relevant-but-missing evidence, the default is `unavailable` (e.g.
undisclosed customer concentration, undeterminable revolver availability, facility
terms not provided). A skip never silently drops a dimension.

---

## Challenge / rerun loop

```mermaid
sequenceDiagram
  participant C as Challenge Agent
  participant R as Rerun Controller
  participant V as Validation
  participant G as DAG (descendants)
  participant S as Scoring
  participant O as Orchestrator(s)
  C-->>R: ChallengeFinding[] (target=claim_id; no mutation)
  alt material & round < MAX_CHALLENGE_RERUN_ROUNDS
    R->>G: re-execute requested_rerun_scope node(s)
    R->>V: validate NEW outputs (append-only versions)
    V->>G: compute descendant set from declared input deps
    G->>G: invalidate affected descendants (mark superseded)
    G->>S: recompute deterministic descendants (scores, feasibility)
    S->>O: rerun dependent orchestrators/challengers/mitigants only
    O-->>R: internally consistent accepted state
  else material & round == MAX
    R->>O: escalate to human review (unresolved)
  else not material
    R->>O: accept (challenge_status="clean")
  end
```

`challenge_loop.py` enforces `MAX_CHALLENGE_RERUN_ROUNDS` (default 1). The rerun
does **not** simply return to the ParameterResult registry; it **propagates
through affected descendants** (Remediation 2). Unaffected ancestors, unrelated
sibling branches and unrelated narrow agents are not rerun; the full DAG never
re-runs. Unresolved material findings create a MANDATORY escalation via the
existing `EscalationEngine` (reusing the `R-CHALLENGE-HIGH-01` pattern, with new
agentic rule ids), which blocks finalization until a human resolves it.

### Targeted rerun with descendant invalidation (Remediation 2)

```text
challenge → requested_rerun_scope
  → re-execute requested node(s)              (append-only new versions)
  → validate new outputs                      (deterministic bucket A)
  → compare new vs prior output identity/content
  → compute descendant set D = all DAG nodes whose declared inputs
      transitively depend on a changed output
  → mark the prior accepted descendant versions `superseded`
  → recompute deterministic descendants in D (scores, candidate feasibility)
  → rerun dependent orchestrators/challengers/mitigant agents in D
  → ResolutionService exposes exactly one `accepted` version per artifact
  → produce internally consistent accepted state (no stale descendant)
```

The registry declares each node's input dependencies, so the executor supports
dependency traversal, descendant invalidation, stale-state marking and minimal
recomputation. Example: if `customer_supplier_contract_agent` changes a material
dependency finding, `D` includes `BusinessRiskScore`, the Business
orchestrator/challenge, `ObligorRiskScore`, the `business_concentration_mitigant`
agent, affected candidate structures and their feasibility, the
`StructuringConclusion`, `StructureProtectionScore`, `FacilityRiskScore`, the
Credit Orchestrator and Cross-Topic Challenge — while unrelated Financial narrow
agents are untouched. The Credit Orchestrator can only ever consume `accepted`
(non-stale) descendants.

---

## Audit / provenance chain

```
AgenticAnalysisRun (analysis_run_id)          ← owns every artifact below
 └─ memo sentence
     └─ ConclusionClaim.claim_id (topic_conclusions)
         └─ ParameterResult ID(s) (parameter_results, accepted version)
             ├─ deterministic: formula_id + formula_version (parameter/metric registry)
             └─ llm/hybrid: agent_run_id (agent_runs) → evidence_packets (packet_hash)
                 └─ canonical evidence IDs (facts / narrative_evidence)
                     └─ fact_source_refs → document/page/table/cell
```

Every object in the chain carries its owning `analysis_run_id`, and conclusion
provenance is claim-level (Remediation 4): a memo sentence maps to a specific
`ConclusionClaim`, which cites specific `parameter_result_ids` and `evidence_ids`,
and `ChallengeFinding.target` points to an exact `claim_id`. The
`FinalCaseSnapshot` records the exact accepted `analysis_run_id` and the frozen
accepted artifact IDs/hashes alongside the versions/hashes of evidence snapshot,
parameter/formula definitions, scoring config, policy/rules, router, prompts,
model identities and agent registry, so a finalized case reproduces even after
prompts/config change.

---

## UI / Workbench

Extend `WorkbenchService` (read-only) with new section keys and assemble methods:
`analysis_runs` (select among multiple runs on the same snapshot; show accepted
run), `parameters`, `parameter_provenance`, `formulas`, `scores` (with
status/coverage/missing dimensions), `obligor_score`, `facility_score`,
`evidence_quality`, `agent_runs`, `evidence_packets`, `prompt_model_identity`,
`token_usage` (per run and per agent run), `challenges`, `reruns` (lineage/
descendant invalidation), `candidate_structures`, `policy_failures`,
`structure_results`, `topic_conclusions` (claim-level), `cross_topic`,
`exceptions`, `approvals`. All views are scoped to an explicit `analysis_run_id`
(never inferred from snapshot version alone). New Jinja2 partials under
`app/api/templates/`. Every new route is GET and never mutates.
The mutating `/inspect` surface gains a read view of the agentic draft but keeps
all mutation behind the existing `mutation_check` (same-origin, reviewer+reason+
confirmation, blocked after finalization).

---

## Migration strategy

`analysis_mode` (`legacy` | `agentic`), threaded through `CreditMemoPipeline`
(constructor/`run_case`/`continue_preview`), default `legacy` until the agentic
path passes regression. `_ai()` becomes a façade:

```python
def _ai(self, ...):
    if self.analysis_mode == "agentic":
        # creates exactly one AgenticAnalysisRun (analysis_run_id) and tags
        # every artifact it produces with that id
        return AgenticAnalysisOrchestrator(...).run(evidence, params,
                                                     force_regenerate=...)
    return self._legacy_ai(...)   # existing path, byte-for-byte unchanged
```

Each `AgenticAnalysisOrchestrator.run(...)` opens a new `AgenticAnalysisRun`
(status `created`→`running`→`completed`/`partially_failed`/`blocked`), so
re-running the same `CanonicalEvidenceSnapshot` (force-regenerate, A/B, config
experiment, resume) yields a distinct `analysis_run_id` and never co-mingles
artifacts. Both modes begin after the same `CanonicalEvidenceSnapshot`. Parsing/
reconciliation are untouched. Baseline tests keep passing against `legacy`;
agentic gets its own regression suite. Schema changes are additive via
`app/migrate.py`. Legacy removal is explicitly out of scope for this feature.

---

## Error handling (summary)

- Missing inputs never coerced to zero; propagate explicit states → caveat/
  escalation (baseline Req 8.4 / 21).
- Validation failures reject the result, record the reason, route to rerun/
  escalation — never used.
- Provider refusal/error/timeout → explicit `AgentRun.error_state`, no infinite
  retry.
- Partial DAG failure preserves valid results and is resumable.
- Insufficient evidence → provisional/unavailable `ParameterResult` + escalation;
  evidence quality never moves a risk band.
- Prompt-injection: evidence is data; agent output constrained to packet evidence
  IDs; secrets via env, never logged.

## Security & data hygiene

Preserve the baseline posture: public/synthetic data only unless authorized;
secrets via env (`SecretStr`), never hardcoded/logged; sanitized filenames;
restricted types; JSON validation; path-traversal prevention; recorded provider
calls; no external-model egress unless permitted. Vertex credentials via ADC/
service account only, never committed. Routed packets reduce prompt-injection
surface by excluding irrelevant content.

## Testing strategy

Driven by `FakeLLMBackend` extended to the multi-agent graph (keyed by
`(agent_id, key)` so each agent returns a deterministic canned structured
response). No network in CI. Coverage enumerated in requirements Req 22; mapped to
`tests/unit` (contracts, router, validation, scoring, parameter formulas, cache),
`tests/integration` (DAG waves, concurrency/session safety, challenge/rerun,
structuring feasibility, migration, mode switch) and `tests/end_to_end`
(reproducibility, Delta acceptance). Mocked Vertex request/response tests live in
`tests/unit`; live Vertex is never in CI.
