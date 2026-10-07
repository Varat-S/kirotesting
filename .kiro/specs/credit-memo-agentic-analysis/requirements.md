# Requirements — Agentic Credit Analysis + Deterministic Scoring

## Introduction

This feature replaces and reorganizes the **downstream analytical portion** of the
existing AI-Assisted Credit Memo Generator (PoC). It introduces a bounded,
concurrent multi-agent credit-analysis architecture layered on top of a
deterministic parameter, validation and scoring substrate.

The baseline system (`.kiro/specs/credit-memo-poc/`) already converts
heterogeneous borrower evidence into a validated, human-reviewed
`CanonicalEvidenceSnapshot` and, today, runs a single downstream path:

```
qualitative extraction → one general analysis → one challenge pass → grounding → draft
```

This spec keeps everything up to and including the `CanonicalEvidenceSnapshot`
boundary unchanged, and replaces only the single-pass analytical path with:

```
Validated CanonicalEvidenceSnapshot
  → Evidence Router
  → deterministic parameter engines + concurrent narrow LLM agents
  → ParameterResult registry
  → deterministic validation
  → deterministic scoring
  → topic orchestration (Business, Financial concurrent)
  → topic challenge → targeted rerun (bounded)
  → topic conclusions
  → Structuring (early extraction concurrent; mitigants after Business+Financial)
  → deterministic candidate-structure feasibility
  → Cross-Topic Credit Orchestrator → Cross-Topic Challenge
  → human credit review → immutable FinalCaseSnapshot → memo
```

The guiding principle is unchanged and non-negotiable: **code is the calculator,
rule engine, scenario engine, scoring engine and policy gate; LLMs are
interpreters, classifiers, challengers and synthesizers.** No LLM performs
official arithmetic when deterministic inputs exist, and no LLM assigns or
modifies an official score.

### How to read this document

These are **behavioral guarantees**. Technology choices (Python/asyncio, SQLite,
SQLAlchemy, Vertex AI as one provider) and tunable values (concurrency limits,
scoring weights/thresholds, model tiers, agent rosters) are **design and
configuration** recorded in `design.md` and versioned configuration artifacts.
All initial scoring values, weights, thresholds and bands are
`ILLUSTRATIVE — NOT BANK POLICY`.

### Relationship to the baseline spec

The baseline requirements in `.kiro/specs/credit-memo-poc/requirements.md`
remain in force. This document **adds** requirements and references the baseline
by number where it constrains new behavior (e.g. baseline Req 5 — canonical
evidence; Req 8 — deterministic metrics; Req 16 — immutable final snapshot;
Req 18 — audit trail; Req 19 — prompt/model/config registries). Where this
document and the baseline appear to conflict, the stricter guarantee wins.

### Non-Goals

No autonomous lending decision; no learned credit-policy thresholds; no
unbounded agent debate or swarm voting; no LLM-assigned scores; no live provider
calls in automated tests; no destructive removal of the legacy analysis path as
part of this feature (deprecation is a separate later change).

---

## Requirements

### Requirement 1 — Canonical Evidence Boundary Preserved

**User Story:** As a credit analyst, I want the agentic analysis to start from the
same validated, human-reviewed evidence as today, so that document processing
integrity and provenance are never weakened by the new architecture.

#### Acceptance Criteria

1. WHEN the agentic path runs THEN it SHALL consume a validated, persisted
   `CanonicalEvidenceSnapshot` and the deterministic facts, metrics, trends and
   benchmarks derived from it, and SHALL NOT reparse original source documents.
2. WHEN the agentic path runs THEN ingestion, parsing, normalization,
   reconciliation, provenance, evidence versioning, human evidence review and
   immutable snapshots SHALL behave exactly as in the baseline.
3. WHEN agents or deterministic parameter engines require evidence THEN they
   SHALL reference `CanonicalEvidenceSnapshot` facts, metrics and narrative
   evidence by their existing IDs.
4. WHEN the agentic path reads evidence THEN it SHALL NOT mutate the
   `CanonicalEvidenceSnapshot` or any upstream record.
5. WHEN a new agentic run occurs for an existing case THEN it SHALL bind to a
   specific `CanonicalEvidenceSnapshot` snapshot version and to a specific
   `AgenticAnalysisRun` (Requirement 27).

### Requirement 2 — Typed Parameter Layer (`ParameterResult`)

**User Story:** As an engineer, I want every analytical datum represented as one
typed, traceable result, so that deterministic calculations and agent outputs
flow through a single auditable contract.

#### Acceptance Criteria

1. WHEN any parameter is produced THEN the system SHALL represent it as a
   `ParameterResult` carrying at least: `parameter_result_id`, `analysis_run_id`,
   `parameter_id`, `topic`, `value`, `value_type`,
   `method` (`deterministic` | `llm` | `hybrid`),
   `status`, `risk_signal`, `evidence_quality`, `confidence`, `materiality`,
   `source_fact_ids`, `source_parameter_ids`, `evidence_ids`, `formula_id`,
   `formula_version`, `agent_id`, `agent_run_id`, `prompt_id`, `model_id`,
   `input_hash`, `contradictions`, `missing_information`, `notes`, and the
   append-only lineage fields `supersedes_id`, `rerun_of` and `acceptance_state`
   (Requirements 32–33). `status` SHALL be one of `ok`, `provisional`,
   `unavailable`, `not_applicable`, `proposed_new_calculation` or
   `requires_review`.
2. WHEN a `ParameterResult` is produced THEN `method` SHALL correctly reflect its
   origin, and a `deterministic` result SHALL carry `formula_id` and
   `formula_version` and SHALL NOT carry an `agent_run_id`.
3. WHEN a `ParameterResult` is produced THEN **credit risk** (`risk_signal`) and
   **evidence quality** (`evidence_quality`/`confidence`) SHALL be stored as
   separate concepts (see Requirement 10).
4. WHEN a `ParameterResult` is persisted THEN it SHALL be reconstructable to its
   origin: source fact IDs, formula/version and/or agent run, and evidence IDs.
5. WHEN a field is not applicable to a given result THEN it MAY be unpopulated,
   but required identity fields (`parameter_result_id`, `parameter_id`, `topic`,
   `method`, `status`) SHALL always be present.
6. WHEN a `ParameterResult` reaches orchestration THEN it SHALL have passed the
   deterministic validation layer (Requirement 8).

### Requirement 3 — Deterministic / LLM / Hybrid Classification

**User Story:** As an auditor, I want each computation explicitly classified by
how it was produced, so that no official number is ever silently produced by an
LLM.

#### Acceptance Criteria

1. WHEN a value is arithmetic, a ratio, a trend, a concentration measure, a
   scenario/stress result, covenant headroom, a policy test, a score, a
   structural-graph calculation or a candidate-structure feasibility result THEN
   it SHALL be produced by `deterministic` method only.
2. WHEN a value is a textual interpretation, classification, business-model /
   management / competitive / accounting-quality judgment, semantic contractual
   term extraction, risk-to-mitigant reasoning, narrative synthesis or
   adversarial challenge THEN it SHALL be produced by an `llm` or `hybrid`
   method.
3. WHEN a numeric contractual term is extracted from text by an LLM (e.g.
   `max_net_leverage = 3.50x`) THEN it SHALL be `method = hybrid`, tagged
   LLM-extracted, and SHALL pass evidence validation before any deterministic
   engine consumes it.
4. WHEN deterministic inputs for a calculation exist THEN no LLM SHALL be asked
   to perform that calculation.
5. WHEN an LLM proposes a calculation not implemented by a registered
   deterministic engine THEN the system SHALL classify it as
   `PROPOSED_NEW_CALCULATION` and SHALL NOT let it affect official analysis until
   it is implemented and reviewed.
6. WHEN an LLM-extracted numerical **financial-statement** value appears THEN it
   SHALL route through the baseline canonical reconciliation/evidence-review
   process and SHALL NOT bypass it.

### Requirement 4 — Evidence Router

**User Story:** As an engineer, I want each agent to receive only the narrowly
relevant evidence, so that token cost, correlated hallucination and prompt-
injection exposure are reduced and runs are reconstructable.

#### Acceptance Criteria

1. WHEN an agent is dispatched THEN it SHALL receive an immutable routed
   **evidence packet**, NOT the entire `CanonicalEvidenceSnapshot`.
2. WHEN a packet is built THEN it SHALL conceptually include: `case_id`,
   `snapshot_version`, `router_version`, `packet_hash`, selected canonical facts,
   selected deterministic parameters/metrics, selected narrative evidence,
   selected entity relationships, selected facility/covenant terms, open
   conflicts relevant to the agent, data limitations relevant to the agent, and
   the exact evidence IDs.
3. WHEN a packet is built for an agent THEN it SHALL exclude evidence outside the
   agent's remit (e.g. the business-model agent SHALL NOT receive raw covenant
   terms; the covenant extraction agent SHALL NOT receive management
   biographies).
4. WHEN a packet is built THEN its content SHALL be deterministically hashed and
   persisted so a historical agent run can be reconstructed.
5. WHEN evidence is placed in a packet THEN it SHALL be treated as data, never as
   instructions; packet content SHALL NOT be able to alter agent control flow,
   prompts or tool selection.
6. WHEN the router selects evidence THEN the selection SHALL be driven by a
   versioned router configuration/logic identified by `router_version`.

### Requirement 5 — Agent Registry

**User Story:** As an engineer, I want a declared registry of narrow agents, so
that the DAG, routing, model tiers and tests are driven by data, not scattered
code.

#### Acceptance Criteria

1. WHEN agents are defined THEN the system SHALL maintain a registry declaring,
   per agent: `agent_id`, `topic`, `task_type`, owned parameters, dependencies,
   routing requirements (evidence selectors), prompt reference, response schema,
   model tier, and the declared input dependencies needed for descendant
   traversal (Requirement 29).
2. WHEN the registry is validated THEN it SHALL reject duplicate `agent_id`s,
   unknown dependencies, cyclic dependencies, and parameters owned by more than
   one agent.
3. WHEN a full normal case runs THEN the base roster SHALL be exactly 27
   registered logical LLM jobs before targeted reruns: 15 narrow / early
   extraction (6 Business narrow + 6 Financial narrow + 3 early Structuring
   extraction) + 4 risk-to-mitigant + 4 orchestrators (Business, Financial,
   Structuring, Credit) + 4 challengers (Business, Financial, Structuring,
   Cross-Topic). Peak initially-ready Wave 0 jobs are approximately 15.
4. WHEN an agent's required evidence is completely absent or genuinely not
   applicable THEN the system SHALL skip that agent and SHALL record the skip
   reason; a skip SHALL NOT silently drop a required analytical dimension without
   surfacing it. The resulting owned-parameter status SHALL follow the
   `unavailable` vs `not_applicable` distinction of Requirement 31.
5. WHEN agents are registered THEN there SHALL NOT be one LLM agent per
   parameter; agents SHALL be coherent and narrow.
6. WHEN an agent is registered THEN the registry SHALL compute an
   `agent_definition_hash` over its semantic definition (owned parameters,
   evidence selectors, dependencies, task type and response schema) and the
   registry SHALL carry an `agent_registry_version` and `agent_registry_hash`; a
   semantic change SHALL change these hashes and SHALL invalidate cached output
   (Requirement 28).

### Requirement 6 — Concurrency and DAG Execution

**User Story:** As an engineer, I want agents executed as a real dependency DAG
with bounded concurrency and safe persistence, so that execution is fast,
correct, resumable and never corrupts shared state.

#### Acceptance Criteria

1. WHEN the analytical stage runs THEN it SHALL execute as a dependency DAG, NOT
   as 27 manually sequenced function calls.
2. WHEN independent agents are ready THEN Business and Financial narrow agents
   SHALL be eligible to execute concurrently, and early Structuring extraction
   agents SHALL be eligible to execute concurrently with them.
3. WHEN substantive Structuring (risk-to-mitigant, candidate structures) is
   scheduled THEN it SHALL wait for accepted `BusinessConclusion`,
   `FinancialConclusion` and `ObligorRiskScore`.
4. WHEN concurrency is applied THEN the maximum number of concurrent provider
   calls SHALL be configurable (`AGENT_MAX_CONCURRENCY`), default 6, supporting
   values up to at least 15.
5. WHEN provider calls execute concurrently THEN no single SQLAlchemy `Session`
   SHALL be mutated by more than one concurrent task; results SHALL be gathered
   and then validated and persisted serially or via isolated sessions with
   defined transaction semantics.
6. WHEN the DAG executes THEN the system SHALL handle partial failures,
   cancellation, timeout, resume, idempotency, targeted reruns, cache reuse and
   audit logging without leaving the case in an inconsistent state.
7. WHEN a provider call times out THEN the timeout SHALL be governed by a
   configurable `AGENT_TIMEOUT_SECONDS` and the affected job SHALL fail
   explicitly rather than hang the DAG.
8. WHEN provider I/O occurs THEN it SHALL NOT block the asyncio event loop; the
   provider call SHALL be awaited via a native async method
   (`backend.generate_async(...)`) where supported, otherwise offloaded via
   `asyncio.to_thread(backend.generate, ...)` (Requirement 30).
9. WHEN one agent task fails (provider refusal, error or timeout) THEN its
   failure SHALL be captured inside the child task as a typed execution result
   and SHALL NOT escape to cancel sibling tasks; successful sibling results SHALL
   be preserved and persisted, and only dependent descendants SHALL be skipped or
   escalated (Requirement 30).

### Requirement 7 — Deterministic Parameter Engine

**User Story:** As a credit analyst, I want non-financial-metric calculations
(concentration, trends, structural, stress) computed deterministically with
versioned formulas, so that every number is reproducible and traceable.

#### Acceptance Criteria

1. WHEN deterministic parameters are computed THEN the system SHALL reuse the
   existing `MetricEngine`, `MetricDefinitionRegistry`, `TrendAnalyzer`,
   `PeerBenchmarker`, `PolicyRuleEvaluator` and reconciliation/data-quality
   structures where applicable, extending rather than replacing them.
2. WHEN non-financial-metric calculations are required THEN a deterministic
   Parameter Engine SHALL compute them, including at least: segment/geographic
   share and HHI, Top-N customer concentration and customer HHI, seasonality,
   revenue volatility, KPI growth, management tenure/turnover, acquisition
   frequency/spend and ratios to EBITDA/FCF, goodwill growth, capex ratios, and
   price-volume decomposition where data permit.
3. WHEN financial deterministic parameters are required THEN the engine SHALL
   compute at least: growth/CAGR, margins and margin trend, FCF and FCF
   conversion, DSO/DIO/DPO and cash-conversion cycle, gross/net debt and
   leverage, interest coverage, DSCR, liquidity, maturity profile and ratios,
   covenant headroom, rate/FX/commodity sensitivities where structured inputs
   exist, base/downside stress, and debt capacity.
4. WHEN structuring deterministic parameters are required THEN the engine SHALL
   compute at least: amortization schedule, bullet amount and ratios, LTV,
   collateral coverage, DSCR/leverage/liquidity under a candidate structure,
   covenant headroom, policy compliance, stressed candidate performance, and
   maximum feasible facility size.
5. WHEN any deterministic formula runs THEN it SHALL be versioned and traceable
   to its exact inputs (fact IDs and/or source parameter IDs).
6. WHEN a denominator is zero, near-zero, missing, conflicting or economically
   misleading THEN the engine SHALL return an explicit state (consistent with
   baseline Req 8.4) rather than a misleading ratio.
7. WHEN the system runs in production THEN it SHALL NOT execute arbitrary
   LLM-generated Python; unsupported proposed calculations follow Requirement
   3.5.

### Requirement 8 — Deterministic Validation Layer

**User Story:** As a reviewer, I want every LLM result validated before it enters
analysis, so that unsupported or malformed model output can never pollute the
case.

#### Acceptance Criteria

1. WHEN an LLM result is produced THEN it SHALL pass deterministic validation
   before entering `ParameterResult` storage or orchestration.
2. WHEN **deterministic evidence validation** runs THEN it SHALL perform only
   genuinely deterministic checks: JSON/schema validity, evidence-ID existence,
   admitted-source membership, entity/period/unit compatibility, citation
   validity, exact numeric/structured-term correspondence (a quoted number
   matches its referenced `ParameterResult` or deterministic evidence),
   source/page existence, allowed-enum checks and parameter-ownership checks.
3. WHEN a qualitative claim requires **narrative semantic support** THEN that
   check SHALL NOT be labelled deterministic unless the actual check is
   deterministic; semantic support SHALL require exact evidence references/spans
   where possible, reuse the existing grounding infrastructure, and MAY be
   evaluated by a structured LLM judge, the topic challenge agent, human
   adjudication, or be marked `not_verifiable`. Citation presence alone SHALL
   NEVER be sufficient support (baseline grounding principle preserved).
4. WHEN a result fails deterministic validation THEN it SHALL NOT be used, SHALL
   be recorded with its failure reason, and SHALL route to targeted rerun or
   escalation.
5. WHEN an LLM result includes a numeric contractual term THEN it SHALL be tagged
   LLM-extracted and SHALL pass deterministic evidence validation before a
   deterministic engine uses it.
6. WHEN an LLM result includes a financial-statement numeric value THEN
   validation SHALL reject it from bypassing canonical reconciliation (per
   Requirement 3.6).
7. WHEN an agent emits a parameter it does not own THEN validation SHALL reject
   that parameter (ownership check).

### Requirement 9 — Deterministic Scoring Subsystem

**User Story:** As a credit analyst, I want all scores computed deterministically
from versioned configuration, so that scoring is reproducible, explainable and
never assigned by an LLM.

#### Acceptance Criteria

1. WHEN any official score is produced THEN it SHALL be computed deterministically
   by the scoring subsystem; no LLM SHALL assign or modify an official score.
2. WHEN scores are computed THEN the system SHALL produce, from validated
   `ParameterResult`s: parameter-level risk signals, `BusinessRiskScore`,
   `FinancialRiskScore`, `ObligorRiskScore`, `StructureProtectionScore` and
   `FacilityRiskScore`.
3. WHEN scoring is applied THEN it SHALL use a versioned, hashed, reproducible
   scoring configuration artifact; all thresholds, weights, bands and overlays
   SHALL be configuration, not hard-coded, and SHALL be labelled
   `ILLUSTRATIVE — NOT BANK POLICY`.
4. WHEN a risk band is used THEN it SHALL use a simple normalized scale (initially
   1 = low/strong, 2 = acceptable, 3 = elevated, 4 = high).
5. WHEN `ObligorRiskScore` is computed THEN it SHALL derive primarily from
   `BusinessRiskScore` + `FinancialRiskScore` + configured overlays/floors, and
   structuring SHALL NOT make a weak borrower appear fundamentally stronger.
6. WHEN `FacilityRiskScore` is computed THEN it SHALL derive from
   `ObligorRiskScore` + validated transaction protections, and borrower risk and
   facility risk SHALL remain separate concepts.
7. WHEN configured floors/overrides apply THEN they SHALL hold: e.g. IF a
   base-case covenant breach exists THEN `FinancialRiskScore` SHALL NOT be better
   than the configured floor; IF DSCR < configured minimum THEN repayment
   capacity SHALL be `insufficient` regardless of weighted average.
8. WHEN many benign parameters coexist with a severe risk THEN averaging SHALL
   NOT wash out the severe risk; floors/overlays SHALL dominate as configured.
9. WHEN the same inputs and scoring-config version are re-run THEN scores SHALL be
   identical.
10. WHEN a `RiskScore` is produced THEN it SHALL carry a `status` of `final`,
    `provisional`, `unavailable` or `not_applicable`, a nullable `band`,
    `contributing_parameter_ids`, `missing_required_parameter_ids`,
    `critical_missing_parameter_ids` and a nullable `coverage_weight`; a score
    SHALL NOT be forced to a numeric band when required evidence is insufficient.
11. WHEN a weighted dimension is missing THEN the scoring subsystem SHALL NOT
    silently renormalize the remaining weights to 100%; renormalization SHALL
    occur only when the versioned scoring policy explicitly permits it. The
    scoring config SHALL define minimum required coverage, critical required
    dimensions, whether partial scoring is permitted, when a score becomes
    `provisional`, and when a score becomes `unavailable`.
12. WHEN required structuring inputs are absent (e.g. the Delta case lacks
    facility terms) THEN `StructureProtectionScore` and `FacilityRiskScore` MAY be
    `unavailable` and SHALL NOT be fabricated.

### Requirement 10 — Evidence Quality Separate From Risk Score

**User Story:** As a reviewer, I want evidence quality kept distinct from credit
risk, so that weak evidence never silently makes a borrower look safer or riskier.

#### Acceptance Criteria

1. WHEN a result carries both a credit risk signal and an evidence-quality/
   confidence value THEN they SHALL be stored and reported as separate fields.
2. WHEN evidence quality/confidence is low THEN it SHALL NOT mechanically improve
   or worsen a credit-risk score.
3. WHEN required underlying evidence is insufficient THEN the affected result
   SHALL be marked provisional or unavailable and SHALL raise review/escalation,
   rather than adjusting the risk score to compensate.
4. WHEN a final conclusion depends on insufficient evidence THEN the system SHALL
   block an unsupported confident conclusion and surface the limitation.

### Requirement 11 — Business Workflow

**User Story:** As a credit analyst, I want business questions answered by narrow
agents, deterministic concentration/stability measures and a deterministic
business score, so that "who is the borrower" is interpreted but never fabricated.

#### Acceptance Criteria

1. WHEN the Business workflow runs THEN it SHALL dispatch the six narrow Business
   agents (business model; competition/pricing; customer/supplier/contract;
   management/governance; M&A/capex/execution; regulatory/material events), each
   owning only its declared parameters.
2. WHEN the Business Orchestrator runs THEN it SHALL consume only validated
   Business `ParameterResult`s, the `BusinessRiskScore` and relevant evidence
   limitations/conflicts, SHALL NOT calculate new numbers, invent values, change
   scores or silently resolve conflicts, and MAY quote an existing validated
   number only when it already exists in a validated `ParameterResult`, the claim
   explicitly references that `parameter_result_id`, the value is copied exactly
   and no LLM arithmetic/transformation was performed (Requirement 34).
3. WHEN the Business Orchestrator produces a `BusinessConclusion` THEN its
   `overall_assessment` and every material strength, weakness, key driver and
   material risk SHALL be a typed `ConclusionClaim` (Requirement 35) carrying its
   own `claim_id`, specific `parameter_result_ids` and specific `evidence_ids`,
   alongside open questions, unresolved contradictions, a score reference, the
   orchestrator run ID and challenge status.
4. WHEN the Business Challenge Agent runs THEN it SHALL test for unsupported
   claims, omitted risks, contradictory parameter results, alternative
   explanations, overstated certainty, missing material evidence and
   score/narrative inconsistency.
5. WHEN a material business challenge is raised THEN it SHALL return the affected
   parameter/agent IDs for targeted rerun and SHALL NOT itself modify parameters
   or scores.

### Requirement 12 — Financial Workflow

**User Story:** As a credit analyst, I want the financial question "can they
repay?" answered from deterministic metrics/stress and a deterministic financial
score, interpreted but never recomputed by an LLM.

#### Acceptance Criteria

1. WHEN the Financial workflow runs THEN it SHALL dispatch the six narrow
   Financial agents (EBITDA adjustments; cash flow/working capital; debt/
   liquidity/terms; covenant extraction; accounting/audit quality; forecast/
   stress drivers), each owning only its declared parameters.
2. WHEN the forecast/stress-drivers agent runs THEN it SHALL identify evidence-
   supported drivers, downside vulnerabilities and sensitivities to stress-test,
   and SHALL NOT perform arithmetic itself.
3. WHEN the Financial Orchestrator runs THEN it SHALL consume only validated
   Financial `ParameterResult`s, the `FinancialRiskScore`, deterministic stress
   outputs and data limitations/conflicts, SHALL NOT calculate ratios or any new
   numbers itself, and MAY quote existing validated numbers only under the
   constraints of Requirement 34.
4. WHEN the Financial Orchestrator produces a `FinancialConclusion` THEN its
   material claims SHALL be typed `ConclusionClaim`s (Requirement 35) and SHALL
   address repayment capacity, earnings quality, cash generation, leverage,
   coverage, liquidity, refinancing, covenant profile, downside resilience and
   accounting-quality issues, with supporting parameter IDs, evidence IDs and a
   score reference.
5. WHEN the Financial Challenge Agent runs THEN it SHALL perform the adversarial
   checks of Requirement 11.4 and return affected parameter IDs for targeted
   deterministic recomputation and score recomputation when material.

### Requirement 13 — Structuring Workflow

**User Story:** As a credit analyst, I want "how do we protect the bank?" answered
by extracted terms, bounded mitigant proposals and a deterministic feasibility
engine, so that no infeasible or invented structure can be recommended.

#### Acceptance Criteria

1. WHEN the Structuring extraction agents run (facility terms; collateral/
   security/guarantee; legal undertakings/conditions) THEN they MAY execute
   concurrently with Business and Financial because they do not depend on those
   conclusions.
2. WHEN the four risk-to-mitigant agents run (liquidity/refinancing; leverage/
   coverage; business concentration; governance/information) THEN they SHALL wait
   for accepted `BusinessConclusion`, `FinancialConclusion` and `ObligorRiskScore`
   and SHALL propose only bounded mitigants.
3. WHEN candidate structures are formed THEN they SHALL be typed
   `CandidateStructure` proposals containing proposed facility amount, tenor,
   amortization, covenant package, collateral/security, guarantees, liquidity
   protection, cash sweep, hedge requirements, reporting requirements and other
   structural protections.
4. WHEN candidate structures are evaluated THEN the deterministic structuring
   engine SHALL test each candidate (amount, tenor, amortization, bullet
   exposure, LTV, collateral coverage, guarantees, seniority, covenants, minimum
   liquidity, cash sweep, restrictions, hedge/reporting requirements, policy
   compliance, base-case and downside performance).
5. WHEN a candidate fails a deterministic feasibility or policy test THEN it SHALL
   be deterministically rejected and SHALL NOT be selectable.
6. WHEN the Structuring Orchestrator runs THEN it SHALL select and explain among
   feasible candidates only and SHALL NOT override a deterministic failure.
7. WHEN the Structuring Challenge Agent runs THEN it SHALL verify that every
   material risk has an adequate protection or an explicitly accepted residual
   risk; unmitigated risks SHALL remain visible.
8. WHEN a `CandidateStructure` is persisted THEN it SHALL be an immutable
   proposal/result and SHALL NOT carry a mutable `selected` flag; selection SHALL
   be recorded separately as `StructuringConclusion.selected_candidate_id` or an
   immutable decision record, and candidate feasibility results SHALL be
   versioned/append-only (Requirement 36).
9. WHEN a different candidate is later selected THEN the system SHALL create a new
   `StructuringConclusion`/decision version and SHALL preserve the previous
   selection.

### Requirement 14 — Orchestrators Do Not Compute or Score

**User Story:** As an auditor, I want orchestrators restricted to synthesis, so
that no number or score originates in a narrative component.

#### Acceptance Criteria

1. WHEN any orchestrator (Business, Financial, Structuring, Credit) runs THEN it
   SHALL consume only validated upstream results/conclusions/scores and SHALL
   NOT calculate new numbers, invent values, assign/modify scores or silently
   resolve conflicts. An orchestrator MAY cite an exact validated number subject
   to Requirement 34; it SHALL NEVER produce LLM-created numerical analysis.
2. WHEN an orchestrator encounters a contradiction or open question THEN it SHALL
   surface it rather than resolve it silently.

### Requirement 15 — Challenge Loop and Bounded Targeted Rerun

**User Story:** As an engineer, I want adversarial challenge to drive bounded,
targeted correction, so that defects are fixed without unbounded debate.

#### Acceptance Criteria

1. WHEN a challenge agent runs THEN it SHALL emit `ChallengeFinding`s containing
   at least: challenge ID, target claim/conclusion, affected agent IDs, affected
   parameter IDs, issue type, severity, reason, evidence IDs,
   `requires_reanalysis` and requested rerun scope.
2. WHEN a challenge agent runs THEN it SHALL NOT directly modify parameters or
   scores; it SHALL only identify defects.
3. WHEN a material challenge requests rerun THEN the system SHALL re-execute the
   requested node(s) and THEN invalidate and recompute all DAG descendants whose
   declared inputs depend on the changed outputs — including affected scores,
   orchestrators, challengers, mitigant agents, candidate structures and
   downstream conclusions. It SHALL NOT rerun unaffected ancestors, unrelated
   sibling branches or unrelated narrow agents, and SHALL NOT rerun the entire
   DAG (Requirement 29).
4. WHEN a targeted rerun occurs THEN new outputs SHALL be revalidated and
   deterministic descendants SHALL be recomputed before any orchestrator consumes
   them; the Credit Orchestrator SHALL NEVER consume a stale descendant.
5. WHEN automatic challenge reruns occur THEN they SHALL be bounded by
   `MAX_CHALLENGE_RERUN_ROUNDS`, default 1.
6. WHEN a material issue remains unresolved after the bounded rerun THEN it SHALL
   escalate to human review and SHALL NOT loop further.
7. WHEN challenge/rerun occurs THEN there SHALL be no unbounded recursive agent
   debate.
8. WHEN a targeted rerun produces corrected outputs THEN those outputs SHALL be
   append-only: new `AgentRun`, `ParameterResult`, `RiskScore`, `TopicConclusion`,
   `ChallengeFinding` and candidate-feasibility versions SHALL be created with
   lineage (`supersedes_id`/`parent_id`/`rerun_of`/`analysis_run_id`) and prior
   versions SHALL be preserved, never updated in place (Requirement 32).

### Requirement 16 — Final Cross-Topic Orchestration

**User Story:** As a credit analyst, I want a coherent cross-topic credit view
built only from accepted conclusions, so that inconsistencies between topics are
caught before human review.

#### Acceptance Criteria

1. WHEN the Credit Orchestrator runs THEN it SHALL receive only accepted/
   reconciled `BusinessConclusion`, `FinancialConclusion`, `ObligorRiskScore`,
   `StructuringConclusion`, `FacilityRiskScore`, remaining escalations and
   evidence/data limitations, and SHALL NOT recompute anything.
2. WHEN the Cross-Topic Challenge Agent runs THEN it SHALL detect cross-topic
   contradictions (e.g. "revenues highly recurring" vs "revenue volatility
   unusually high"; "downside liquidity tight" vs "no minimum-liquidity
   protection"; "single-source supplier dependency material" vs "no monitoring/
   contingency provision").
3. WHEN a material unresolved cross-topic challenge is raised THEN it SHALL
   trigger targeted reanalysis (bounded per Requirement 15) or human escalation.

### Requirement 17 — Model Provider Abstraction and Vertex Backend

**User Story:** As an engineer, I want Vertex AI available as one provider behind
the existing abstraction, so that the architecture is not coupled to any single
vendor.

#### Acceptance Criteria

1. WHEN a provider backend is added THEN it SHALL implement the existing
   `LLMBackend` interface, and the existing `LLMClient` abstraction SHALL remain
   the single model choke point (baseline Req 19.3).
2. WHEN Vertex is configured THEN a `VertexProviderBackend(LLMBackend)` SHALL use
   the officially supported Google Gen AI / Vertex mechanism and Google Cloud
   authentication via ADC/service account; credentials SHALL NEVER be committed.
3. WHEN provider/model configuration is read THEN it SHALL come from
   configuration/env (`LLM_PROVIDER=vertex`, `GOOGLE_CLOUD_PROJECT`,
   `GOOGLE_CLOUD_LOCATION`, `VERTEX_MODEL_NARROW`, `VERTEX_MODEL_ORCHESTRATOR`,
   `VERTEX_MODEL_CHALLENGE`), and model names SHALL NOT be hard-coded into
   business logic.
4. WHEN agents are dispatched THEN the system SHALL support model-tier routing:
   narrow extraction/classification agents to a cheaper/faster tier;
   orchestrators/challengers to a stronger tier.
5. WHEN a provider call completes THEN the system SHALL store provider usage
   metadata where available (input tokens, output tokens, total tokens, latency,
   model ID); token usage SHALL be the canonical stored usage record.
6. WHEN estimated cost is derived THEN it SHALL come from a versioned,
   configurable rate card, and SHALL be treated as derived rather than canonical.
7. WHEN a paid provider call fails THEN the system SHALL NOT retry indefinitely;
   reruns SHALL be explicit and auditable.

### Requirement 18 — Result Caching and Idempotency

**User Story:** As an engineer, I want valid agent outputs reused when nothing
relevant changed, so that cost is controlled and reproducibility is preserved.

#### Acceptance Criteria

1. WHEN all relevant identity inputs are unchanged THEN a valid agent output MAY
   be reused, keyed by at least: case ID, evidence snapshot version, agent ID,
   `agent_definition_hash` (and/or `agent_registry_version`), prompt hash,
   response-schema hash, model ID/config, router version and routed packet hash.
2. WHEN any of evidence snapshot, prompt, model, router version, response schema,
   relevant input packet, or **agent definition semantics** (owned parameters,
   evidence selectors, dependencies, task type or output schema) changes THEN
   cached reuse SHALL be invalidated (Requirement 28).
3. WHEN `force_regenerate` is requested THEN the system SHALL bypass cache reuse.
4. WHEN a result is reused from cache THEN the reuse SHALL be recorded on the
   `AgentRun` (e.g. `reused_from_cache`).
5. WHEN a cached result is reused THEN it SHALL have previously passed validation.

### Requirement 19 — Provenance and Auditability

**User Story:** As an auditor, I want every conclusion traceable back to source
evidence and every run reconstructable, so that the memo is fully defensible.

#### Acceptance Criteria

1. WHEN a memo claim exists THEN it SHALL be traceable:
   memo sentence → `ConclusionClaim.claim_id` → `ParameterResult` ID(s) →
   deterministic formula and/or `AgentRun` → canonical evidence IDs → original
   source document/page/cell. Every object in this chain SHALL also carry the
   owning `analysis_run_id`.
2. WHEN an agent executes THEN the system SHALL persist an `AgentRun` with enough
   metadata to reconstruct it: run ID, `analysis_run_id`, agent ID, topic, case
   ID, snapshot version, prompt ID/version/hash, `agent_definition_hash`, model
   ID, model config, input hash, input evidence IDs, raw response, parsed
   response, validation status, parent/dependency run IDs, execution wave,
   latency, token usage when available, error state, rerun reason and whether
   reused from cache.
3. WHEN an agent task is defined THEN the system SHALL represent it as an
   `AgentTask` carrying at least: agent ID, topic, task type, dependencies, routed
   input packet, prompt, schema, model tier, case ID, snapshot version and
   retry/rerun metadata.
4. WHEN any significant agentic action occurs THEN it SHALL emit an append-only
   audit event consistent with baseline Req 18 (e.g. agent run, challenge
   created, parameter scored, score computed, rerun triggered).
5. WHEN a `FinalCaseSnapshot` is produced THEN it SHALL identify exactly one
   accepted `analysis_run_id`, freeze the exact accepted artifact IDs/hashes
   (parameters, scores, conclusions, candidate selection, challenge outcomes),
   and record exact versions/hashes for: evidence snapshot, deterministic
   formulas/parameter definitions, scoring config, policy/rules, router version,
   prompts, model identities, agent registry, topic conclusions and challenge
   outcomes.
6. WHEN prompts or configuration change after finalization THEN finalized
   historical cases SHALL remain reproducible.

### Requirement 20 — Human Review and Immutable Finalization Preserved

**User Story:** As a reviewer, I want the mandatory human sign-off and immutable
final snapshot preserved, so that the new architecture never finalizes without a
human.

#### Acceptance Criteria

1. WHEN agentic analysis completes THEN it SHALL produce a draft that still
   requires explicit human sign-off before finalization (baseline Req 15/16).
2. WHEN unresolved mandatory escalations exist THEN finalization SHALL remain
   blocked.
3. WHEN a `FinalCaseSnapshot` is finalized THEN it SHALL be immutable and
   reproducible; a later change SHALL create a new superseding version.
4. WHEN the agentic draft is produced THEN it SHALL carry topic conclusions,
   scores, challenge outcomes and unresolved exceptions for human review.
5. WHEN finalization is attempted THEN deterministic consistency checks SHALL
   block it if the accepted run contains any of: stale descendants; unresolved
   material challenges; unresolved mandatory escalations; a score reference from
   a different `analysis_run_id`; a conclusion referencing a superseded
   `ParameterResult`; a candidate selection pointing to an infeasible candidate;
   a `FacilityRiskScore` present when required structuring inputs are unavailable
   unless the score status explicitly permits `unavailable`/`provisional`; or
   mixed router/prompt/scoring/registry versions not explicitly recorded
   (Requirement 37).

### Requirement 21 — UI / Workbench Extensions (Read-Only)

**User Story:** As a reviewer, I want to inspect the agentic analysis, so that I
can audit parameters, scores, runs, challenges and structures without being able
to mutate finalized data.

#### Acceptance Criteria

1. WHEN the workbench is extended THEN it SHALL add read-only views for:
   parameters, parameter provenance, deterministic formulas, topic scores,
   `ObligorRiskScore`, `FacilityRiskScore`, evidence quality, agent runs, routed
   evidence packets, prompt/model identity, token usage, challenges, targeted
   reruns, candidate structures, policy failures, base/downside structure
   results, topic conclusions, cross-topic conclusion, unresolved exceptions and
   human approvals.
2. WHEN read-only inspection is used THEN it SHALL remain genuinely read-only and
   SHALL NOT mutate any record.
3. WHEN agentic views are added THEN they SHALL extend the existing inspection/
   workbench rather than replacing it.

### Requirement 22 — Evaluation and Testing

**User Story:** As an engineer, I want the fake deterministic backend extended to
the multi-agent graph with comprehensive offline tests, so that correctness is
provable without any network calls.

#### Acceptance Criteria

1. WHEN automated tests run THEN there SHALL be no live/network provider calls;
   the deterministic fake backend SHALL drive the full multi-agent graph.
2. WHEN the test suite runs THEN it SHALL cover at least: agent-registry
   validation; DAG dependency ordering; concurrent-ready-task scheduling;
   max-concurrency semaphore behavior; no shared `Session` mutation across
   concurrent calls; routed-evidence isolation; packet hashing; irrelevant-
   evidence exclusion; cache reuse; cache invalidation; prompt-version
   invalidation; model-version invalidation; schema-invalid output; unsupported
   evidence IDs; invalid PDF/page references; hallucinated numeric output; wrong-
   entity evidence; wrong-period evidence; contradictory outputs; deterministic
   formula replay; parameter scoring; weighted scoring; scoring floors/overlays;
   LLM inability to modify official score; evidence-quality separation; Business
   and Financial orchestration; topic challenge; targeted-rerun-only; one-round
   automatic rerun bound; unresolved challenge → escalation; early Structuring
   extraction concurrency; Structuring dependency on accepted Business/Financial
   outputs; candidate feasibility; candidate rejection on policy violation;
   downside structuring stress; Structuring Challenge; Cross-Topic Challenge;
   partial DAG failure; provider timeout; provider refusal/error; resume after
   partial failure; historical reproducibility; and `FinalCaseSnapshot`
   immutability.
3. WHEN Vertex integration is tested THEN there SHALL be mocked tests for Vertex
   request construction and structured-response parsing, and live Vertex
   execution SHALL NOT be part of automated tests.

### Requirement 23 — Delta End-to-End Acceptance

**User Story:** As a stakeholder, I want the Delta fixture to run through the full
agentic path, so that the architecture is proven on real documents.

#### Acceptance Criteria

1. WHEN the Delta fixture runs THEN the agentic path SHALL execute end-to-end:
   existing parsing/review → canonical evidence snapshot → deterministic
   parameters → Business agents → Financial agents → Business/Financial scores →
   topic orchestrators → topic challenge → `ObligorRiskScore` → Structuring path
   where facility inputs exist → final cross-topic synthesis → human review →
   final snapshot.
2. WHEN Delta lacks required structuring inputs THEN the output SHALL be an
   explicit missing-data / Not Available state, never invented terms.
3. WHEN the Delta acceptance test runs THEN it SHALL prove agents consume the
   saved reviewed evidence and do NOT reparse original source documents.

### Requirement 24 — Backward-Compatible Migration

**User Story:** As an engineer, I want the legacy path preserved behind a flag
during development, so that migration is incremental and non-destructive.

#### Acceptance Criteria

1. WHEN analysis is invoked THEN the system SHALL support an `analysis_mode`
   selector (`legacy` | `agentic`) or equivalent, defaulting to preserve existing
   behavior until the agentic path passes regression.
2. WHEN the agentic mode is selected THEN it SHALL begin after the same
   `CanonicalEvidenceSnapshot` boundary, leaving parsing/reconciliation behavior
   unchanged.
3. WHEN either mode runs THEN existing baseline tests SHALL continue to pass, and
   the agentic mode SHALL have its own regression tests.
4. WHEN this feature is delivered THEN it SHALL NOT remove or destroy the legacy
   analysis path; deprecation is a separate later change.
5. WHEN new persistence is added THEN schema changes SHALL be additive and SHALL
   not rewrite existing evidence, snapshot, audit or review payloads.

### Requirement 25 — Cost / Token Observability and Failure Recovery

**User Story:** As an operator, I want token usage, cost visibility and robust
failure handling, so that runs are affordable, observable and recoverable.

#### Acceptance Criteria

1. WHEN provider calls occur THEN token usage (input/output/total, latency, model
   ID) SHALL be recorded per `AgentRun` where available and aggregated at the
   `AgenticAnalysisRun` level (Requirement 27), so that the run summary can report
   number of model calls, cache hits, reruns, models used, total tokens, total
   latency and (optionally) estimated cost.
2. WHEN estimated cost is reported THEN it SHALL be derived from a versioned rate
   card, token usage SHALL remain the canonical record, and cost estimation SHALL
   NOT be part of scoring or any business logic.
3. WHEN a DAG run partially fails THEN completed valid results SHALL be preserved,
   and the run SHALL be resumable without redoing valid, cached work.
4. WHEN a provider refuses, errors or times out THEN the failure SHALL be
   explicit and auditable, and SHALL NOT be silently retried indefinitely.
5. WHEN a run is resumed THEN idempotent reuse (Requirement 18) SHALL avoid
   repeating unchanged valid work.

### Requirement 26 — Prompt-Injection and Evidence Boundary

**User Story:** As a security-conscious engineer, I want documents treated as
evidence and never as instructions, so that malicious or crafted document content
cannot steer the agents.

#### Acceptance Criteria

1. WHEN evidence is routed to any agent THEN it SHALL be presented as data, and
   SHALL NOT be able to change prompts, control flow, model selection or tool use.
2. WHEN agent output references evidence THEN only evidence IDs present in the
   routed packet and admitted in the canonical snapshot SHALL be accepted
   (validation per Requirement 8).
3. WHEN public/synthetic data policy applies THEN the baseline security posture
   (secrets via env, never logged; no unauthorized external data egress) SHALL be
   preserved.

### Requirement 27 — Agentic Analysis Run Identity

**User Story:** As an engineer, I want every complete agentic execution to have a
first-class identity, so that repeated analyses of the same evidence snapshot
never produce ambiguous, co-mingled artifacts.

#### Acceptance Criteria

1. WHEN the agentic downstream pipeline is invoked THEN the system SHALL create
   one `AgenticAnalysisRun` with a unique `analysis_run_id`, bound to a `case_id`
   and an `evidence_snapshot_version`.
2. WHEN an `AgenticAnalysisRun` is created THEN it SHALL carry at least:
   `analysis_run_id`, `case_id`, `evidence_snapshot_version`, `status`
   (`created` | `running` | `partially_failed` | `completed` | `blocked` |
   `superseded`), `analysis_mode` (`agentic`), `started_at`, `completed_at`,
   `parent_analysis_run_id`, `supersedes_analysis_run_id`, `router_version`,
   `router_hash`, `agent_registry_version`, `agent_registry_hash`,
   `scoring_config_version`, `scoring_config_hash`, `model_configuration`,
   `config_versions`, token totals (`total_input_tokens`, `total_output_tokens`,
   `total_tokens`) and `failure_state`.
3. WHEN any analytical artifact is persisted (evidence packet, agent run,
   parameter result, score, topic conclusion, challenge finding, candidate
   structure, final cross-topic conclusion, rerun lineage) THEN it SHALL carry its
   owning `analysis_run_id`.
4. WHEN two executions occur over the same `CanonicalEvidenceSnapshot` (e.g.
   `force_regenerate`, model/prompt A/B, config experiment, resumed partial
   failure, historical evaluation, manual fresh analysis) THEN they SHALL remain
   distinct `AgenticAnalysisRun`s and their artifacts SHALL NOT leak between runs.
5. WHEN "current results" are requested THEN the system SHALL NOT infer the run
   from the snapshot version alone; a specific `analysis_run_id` SHALL be
   explicitly selected.
6. WHEN a `FinalCaseSnapshot` is accepted THEN it SHALL identify exactly one
   accepted `analysis_run_id`.

### Requirement 28 — Agent-Definition-Aware Cache Invalidation

**User Story:** As an engineer, I want cached agent output invalidated when the
agent's semantics change, so that a stale cache can never serve results for a
different agent definition.

#### Acceptance Criteria

1. WHEN the cache identity is computed THEN it SHALL include
   `agent_definition_hash` (and/or `agent_registry_version`) and the
   response-schema hash in addition to case, snapshot version, agent ID, prompt
   hash, model ID/config, router version and packet hash.
2. WHEN an agent's owned parameters, evidence selectors, dependencies, task type
   or output schema change THEN the `agent_definition_hash` SHALL change and
   cached output SHALL be invalidated even if the prompt text is unchanged.

### Requirement 29 — Dependency-Aware Descendant Invalidation

**User Story:** As an engineer, I want a changed node to invalidate exactly its
dependent descendants, so that no downstream conclusion is ever combined with
superseded upstream inputs.

#### Acceptance Criteria

1. WHEN a node's output changes (via rerun or correction) THEN the system SHALL
   deterministically compute the set of DAG descendants whose declared inputs
   depend on the changed output, and SHALL invalidate and recompute only that
   set.
2. WHEN descendants are recomputed THEN deterministic descendants SHALL be
   recomputed by the engines and dependent orchestrators/challengers/mitigant
   agents SHALL rerun as required, producing an internally consistent accepted
   state.
3. WHEN invalidation runs THEN unaffected ancestors, unrelated sibling branches
   and unrelated narrow agents SHALL NOT be rerun.
4. WHEN a changed Business agent output invalidates descendants THEN the affected
   `BusinessRiskScore`, Business conclusion/challenge, `ObligorRiskScore`,
   dependent mitigant agents, candidate structures, `StructuringConclusion`,
   `StructureProtectionScore`, `FacilityRiskScore`, Credit Orchestrator and
   Cross-Topic Challenge SHALL be recomputed as applicable; unrelated Financial
   narrow agents SHALL NOT rerun.
5. WHEN descendants have been invalidated THEN no orchestrator or finalization
   step SHALL consume a stale descendant.

### Requirement 30 — Async Provider Execution Boundary and Sibling Isolation

**User Story:** As an engineer, I want provider I/O to never block the event loop
and one failure to never discard sibling results, so that concurrent execution is
correct and robust.

#### Acceptance Criteria

1. WHEN a provider call is awaited THEN it SHALL use a native async method
   (`backend.generate_async(...)`) where supported, otherwise be offloaded via
   `asyncio.to_thread(backend.generate, ...)`; synchronous provider I/O SHALL
   NEVER run directly on the event loop.
2. WHEN an agent task encounters a provider refusal, error or timeout THEN the
   child task SHALL catch it internally and return a typed execution result
   (e.g. `AgentExecutionResult(status="error", error_type=...)`); provider
   exceptions SHALL NOT escape the child task to cancel sibling tasks.
3. WHEN the executor gathers results THEN it SHALL persist valid sibling results,
   persist failed `AgentRun` records, and skip or escalate only dependent
   descendants.

### Requirement 31 — `unavailable` vs `not_applicable` Semantics

**User Story:** As a reviewer, I want a strict distinction between "not relevant"
and "relevant but missing", so that missing evidence is never disguised as
inapplicability.

#### Acceptance Criteria

1. WHEN a parameter genuinely does not apply to the case (determined by
   deterministic/config-driven applicability logic) THEN its status SHALL be
   `not_applicable` (e.g. inventory turnover where there is no inventory concept
   and policy marks it non-applicable; collateral LTV for an explicitly unsecured
   facility with no collateral in the transaction).
2. WHEN a parameter would be relevant but required evidence is absent,
   insufficient, conflicting, inaccessible or not disclosed THEN its status SHALL
   be `unavailable` (e.g. undisclosed customer concentration; undeterminable
   revolver availability; facility terms not provided).
3. WHEN evidence is simply missing THEN the default status SHALL be `unavailable`,
   NOT `not_applicable`.
4. WHEN applicability is decided THEN it SHALL be deterministic/config-driven
   where possible.

### Requirement 32 — Append-Only Rerun Outputs and Lineage

**User Story:** As an auditor, I want challenge corrections to add new versioned
artifacts rather than mutate old ones, so that the full analytical history is
preserved.

#### Acceptance Criteria

1. WHEN a targeted rerun produces corrected analytical outputs THEN it SHALL
   create new `AgentRun`, `ParameterResult`, `RiskScore`, `TopicConclusion`,
   `ChallengeFinding` and candidate-feasibility versions rather than updating
   prior rows in place.
2. WHEN a new version is created THEN it SHALL carry lineage fields as applicable:
   `supersedes_id`, `parent_id`, `rerun_of`, and `analysis_run_id`.
3. WHEN a prior version exists THEN it SHALL be preserved as historical and SHALL
   remain queryable.

### Requirement 33 — Accepted / Current Artifact Resolution

**User Story:** As an engineer, I want deterministic rules for which versioned
artifact is accepted/current within a run, so that no consumer relies on
insertion order or timestamps.

#### Acceptance Criteria

1. WHEN multiple versions of an artifact exist within an `analysis_run_id` THEN
   the accepted/current artifact SHALL be resolved by explicit lineage/status/
   acceptance fields (or a deterministic resolution service), NOT by latest
   timestamp, maximum primary key or insertion order.
2. WHEN an orchestrator consumes an artifact THEN it SHALL consume only the
   accepted/current version; superseded versions SHALL remain historical and
   SHALL NOT be consumed.
3. WHEN a `FinalCaseSnapshot` is produced THEN it SHALL freeze the exact accepted
   artifact IDs.

### Requirement 34 — Orchestrator Number Handling (Quote, Not Calculate)

**User Story:** As an auditor, I want orchestrators to be able to reference exact
validated numbers but never to create numerical analysis, so that narrative is
useful without becoming a source of truth.

#### Acceptance Criteria

1. WHEN an orchestrator quotes a number THEN that number SHALL already exist in a
   validated `ParameterResult`, the claim SHALL explicitly reference that
   `parameter_result_id`, the value SHALL be copied exactly, and no LLM
   arithmetic/transformation SHALL have been performed.
2. WHEN validation runs on an orchestrator output THEN any quoted number SHALL be
   compared against its referenced `ParameterResult`, and a mismatch or an
   unreferenced invented number SHALL be rejected.
3. WHEN an orchestrator states a forward/forecast number THEN it SHALL be allowed
   only if that number already exists as a deterministic forecast
   `ParameterResult`; otherwise it SHALL be rejected.

### Requirement 35 — Claim-Level Conclusion Provenance (`ConclusionClaim`)

**User Story:** As an auditor, I want each material conclusion statement to be an
individually identified claim with its own evidence, so that the memo→evidence
chain is exact and challenges can target a specific claim.

#### Acceptance Criteria

1. WHEN a `TopicConclusion` is produced THEN its `overall_assessment` and every
   material strength, weakness, key driver and material risk SHALL be a typed
   `ConclusionClaim` carrying at least: `claim_id`, `category`
   (`strength` | `weakness` | `driver` | `risk` | `assessment` | `mitigant` |
   `limitation`), `text`, `parameter_result_ids`, `evidence_ids`, `materiality`
   and `uncertainty`. (This reuses/extends the baseline `AnalyticalClaim` design.)
2. WHEN a material conclusion claim is produced THEN it SHALL carry specific
   `parameter_result_ids` and specific `evidence_ids`, not only an aggregate list.
3. WHEN a `ChallengeFinding` targets a conclusion THEN its `target` SHALL point to
   an exact `claim_id` wherever possible.
4. WHEN provenance is traced THEN the chain memo sentence → `claim_id` →
   `ParameterResult` ID(s) → formula and/or `AgentRun` → evidence IDs → source
   document/page/cell SHALL be reconstructable end-to-end.

### Requirement 36 — Immutable Candidate Structures and Selection

**User Story:** As an auditor, I want candidate structures and their selection to
be append-only, so that structuring decisions are fully auditable.

#### Acceptance Criteria

1. WHEN a `CandidateStructure` is persisted THEN it SHALL be immutable and SHALL
   NOT carry a mutable `selected` flag.
2. WHEN a candidate is selected THEN the selection SHALL be recorded as
   `StructuringConclusion.selected_candidate_id` or an immutable decision/
   selection record.
3. WHEN candidate feasibility is evaluated THEN feasibility results SHALL be
   versioned/append-only.
4. WHEN a different candidate is later selected THEN a new `StructuringConclusion`/
   decision version SHALL be created and the previous selection SHALL be
   preserved.

### Requirement 37 — Finalization Consistency Checks

**User Story:** As a reviewer, I want finalization to deterministically reject an
inconsistent accepted run, so that a frozen memo is always internally coherent.

#### Acceptance Criteria

1. WHEN finalization is attempted THEN deterministic consistency checks SHALL run
   before freezing, and SHALL block finalization if the accepted run contains any
   of: stale descendants; unresolved material challenges; unresolved mandatory
   escalations; a score reference from another `analysis_run_id`; a conclusion
   referencing a superseded `ParameterResult`; a candidate selection pointing to
   an infeasible candidate; a `FacilityRiskScore` present when required
   structuring inputs are unavailable unless the score status explicitly permits
   `unavailable`/`provisional`; or mixed router/prompt/scoring/registry versions
   not explicitly recorded.
2. WHEN finalization succeeds THEN it SHALL freeze the exact accepted artifact
   IDs/hashes and the accepted `analysis_run_id`.
