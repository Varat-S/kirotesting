# Requirements — AI-Assisted Credit Memo Generator (PoC)

## Introduction

This proof-of-concept converts heterogeneous borrower evidence (PDFs, XBRL, Excel, CSV, HTML, news) into an **auditable, source-grounded draft credit memo** and a **machine-readable JSON case record**. The system deliberately refuses to treat generative AI as the sole source of truth. It combines deterministic parsing and calculation, source provenance, rule-based checks, peer/historical benchmarking, generative analysis, escalation rules, human review, immutable version history, and reproducible testing.

The guiding objective is **defensibility and traceability**, not maximum model complexity. The PoC targets U.S. scheduled passenger airlines, with Delta Air Lines as the primary borrower and United, American, and Southwest as peers. Every case carries an explicit `as_of_date` and `evidence_cutoff_timestamp`; no document published after the cutoff may be used in a historical run.

The system answers five core questions: (1) Who is the borrower? (2) Can they repay? (3) How is the bank protected? (4) Can every important conclusion be traced to evidence? (5) Does the system know when evidence is insufficient or conflicting?

### Non-Goals

The PoC does not build a production credit-approval system, an autonomous lending decision-maker, an ML model that discovers binding credit policy, a real-time dashboard that silently rewrites history, a general multi-industry system, or a large swarm of overlapping agents. The LLM must never overwrite raw sources, invent missing values, perform authoritative calculations that deterministic code can do, change policy thresholds, approve/decline credit, or resolve material conflicts without human review.

---

## Requirements

### Requirement 1 — Source Ingestion & Versioning

**User Story:** As a credit analyst, I want to create a case and ingest raw source files immutably, so that every memo can be traced back to the exact evidence package that produced it.

#### Acceptance Criteria

1. WHEN a user creates a case THEN the system SHALL assign a unique `case_id` and require an `as_of_date`.
2. WHEN a case is created THEN the system SHALL require an `evidence_cutoff_timestamp`.
3. WHEN a file is uploaded THEN the system SHALL accept PDF, XLSX, CSV, JSON, and HTML/XBRL formats where practical.
4. WHEN a file is ingested THEN the system SHALL save the original file unchanged and SHALL NEVER mutate or overwrite an existing object.
5. WHEN a file is ingested THEN the system SHALL compute and store its SHA-256 hash.
6. WHEN a file is ingested THEN the system SHALL record original filename, file type, ingest timestamp, source, document date, publication date (if known), entity, fiscal period, and hash as a `DocumentRecord`.
7. WHEN identical bytes are re-uploaded THEN the system SHALL produce the same hash.
8. WHEN a corrected document is uploaded THEN the system SHALL create a new `DocumentRecord` and set its `supersedes` field rather than overwriting the prior record.
9. IF a historical run is executed THEN the system SHALL reject any document with a publication date newer than the case `evidence_cutoff_timestamp`.
10. WHEN any ingestion occurs THEN the system SHALL emit a corresponding audit event.

### Requirement 2 — Parsing & Extraction

**User Story:** As a credit analyst, I want heterogeneous evidence transformed into structured facts with deterministic parsing preferred over AI, so that numerical facts are reliable and every fact is traceable.

#### Acceptance Criteria

1. WHEN a source contains structured data THEN the system SHALL use deterministic parsing in priority order: XBRL/structured filings, XLSX/CSV, PDF tables, free-form PDF text, OCR only if required.
2. WHEN the system extracts financial inputs THEN it SHALL target revenue, EBITDA inputs, EBIT, cash, total debt, interest expense, CFO, capex, debt maturities, liquidity, and operating metrics where available.
3. WHEN qualitative content is needed THEN the system SHALL use the LLM for management descriptions, ownership, corporate structure, competitive position, industry risks, major capex/M&A, qualitative debt terms, narrative risk factors, and facts in poorly structured text.
4. WHEN the LLM extracts a fact THEN it SHALL return fact name, value/statement, period, source reference, confidence/evidence status, and SHALL NOT include unsupported inference.
5. WHEN any factual field is extracted THEN it SHALL carry at least one source reference.
6. WHEN the LLM extracts a fact THEN that fact SHALL NOT automatically enter `verified` state.
7. WHEN a numerical fact exists from both structured and free-form sources THEN the structured source SHALL take precedence.
8. WHEN exact duplicate facts are produced THEN the system SHALL deduplicate them.
9. WHEN facts conflict THEN the system SHALL keep both visible and SHALL NOT silently merge them.

### Requirement 3 — Reconciliation & Data Quality

**User Story:** As a credit analyst, I want independent extraction routes compared with explicit tolerances, so that facts are promoted to canonical state only when they agree, and conflicts are preserved for review.

#### Acceptance Criteria

1. WHEN a numerical field has multiple sources THEN the system SHALL compute `absolute_delta = |AI - deterministic|` and `relative_delta = absolute_delta / |deterministic|`.
2. WHEN comparing fields THEN the system SHALL apply field-specific tolerances (prototype: revenue 0.5%, debt 0.5%, ratio 0.01x) stored as configuration labelled illustrative, not bank policy.
3. IF values are within tolerance THEN the fact state SHALL become `verified`.
4. IF values are outside tolerance THEN the fact state SHALL become `conflicting`.
5. IF only one reliable source exists THEN the fact state SHALL become `unverified`.
6. IF no source exists THEN the fact state SHALL become `missing`.
7. WHEN non-numeric disagreement occurs THEN the system SHALL create a contradiction record rather than force a merge.
8. WHEN a conflict is recorded THEN it SHALL preserve both values and both source references.
9. WHEN a human resolves a conflict THEN the system SHALL create a new event and SHALL NOT delete the conflict.
10. WHEN requested THEN the system SHALL be able to show before-resolution and after-resolution states.

### Requirement 4 — Canonical Credit Dataset

**User Story:** As a downstream consumer, I want a single normalized JSON interface where each field carries value, state, and provenance, so that no derived logic depends on untraceable or ambiguous values.

#### Acceptance Criteria

1. WHEN a field is stored in the canonical dataset THEN it SHALL include value, state, and provenance.
2. WHEN a value is normalized THEN the system SHALL preserve the raw extracted value separately from the normalized value.
3. WHEN values are stored THEN the system SHALL normalize units and dates and preserve the reporting period.
4. WHEN metrics are derived THEN the system SHALL store formula inputs independently from derived metrics.
5. WHEN a field is stored THEN it SHALL include data freshness.
6. WHEN a value is missing, unknown, or zero THEN the three states SHALL remain distinct and `missing` SHALL NEVER be represented as numerical zero.
7. WHEN unit conversions occur THEN they SHALL be deterministic and covered by tests.
8. WHEN the case JSON is produced THEN it SHALL validate against a versioned JSON Schema.

### Requirement 5 — Deterministic Financial Metric Engine

**User Story:** As a credit analyst, I want financial metrics computed in code with explicit inputs and formulas, so that no LLM arithmetic can override deterministic results and every metric is reproducible.

#### Acceptance Criteria

1. WHEN metrics are computed THEN the system SHALL calculate (at minimum) revenue growth, operating margin, net debt, net debt/EBITDA, interest coverage, CFO/EBITDA cash conversion, free cash flow, liquidity, capex/revenue, load factor, and at least one operating cost indicator where data allow.
2. WHEN a metric is computed THEN the system SHALL store formula ID, inputs, input fact IDs, result, period, units, and engine version.
3. IF a denominator is zero, missing, conflicting, or economically misleading THEN the system SHALL return an explicit state (`not_meaningful`, `missing_input`, or `requires_review`) instead of a normal ratio.
4. WHEN golden examples are evaluated THEN deterministic metrics SHALL achieve a 100% pass rate.
5. WHEN the same case is re-run THEN metric results SHALL be identical.
6. WHEN an LLM produces an arithmetic result THEN it SHALL NEVER override a deterministic metric.

### Requirement 6 — Historical Trend Layer

**User Story:** As a credit analyst, I want deterministic trend analysis per metric, so that deterioration or improvement is visible without implying continuity across missing years.

#### Acceptance Criteria

1. WHEN trends are computed THEN the system SHALL store current value, previous period, 2-year and 3-year change, slope/direction, and volatility where relevant for each metric.
2. WHEN direction is assessed THEN the system SHALL use metric-specific definitions of adverse direction.
3. WHEN trend logic runs THEN it SHALL be deterministic.
4. IF intermediate years are missing THEN the system SHALL NOT silently imply a continuous trend.
5. WHEN a structural break occurs THEN it SHALL be visible.

### Requirement 7 — Peer Benchmark Layer

**User Story:** As a credit analyst, I want the borrower compared to a defined peer cohort as an anomaly signal, so that benchmarking informs review without being treated as an approval threshold.

#### Acceptance Criteria

1. WHEN benchmarks are computed THEN the system SHALL calculate peer median, P25, P75, and P90/P95 (where sample size is meaningful), plus borrower rank/percentile for each comparable metric.
2. WHEN the cohort is small (e.g., four peers) THEN the system SHALL show rank and simple distribution and MAY use a larger historical panel, labelling any synthetic benchmarks explicitly.
3. WHEN a benchmark is produced THEN the system SHALL store the cohort definition and benchmark date/period.
4. WHEN benchmarks are computed THEN the borrower SHALL NOT be accidentally included twice in its own peer set.
5. WHEN peer data are presented THEN they SHALL be reproducible from source data.
6. WHEN benchmarking results are used THEN they SHALL be treated as an anomaly signal and NEVER as a credit approval threshold.

### Requirement 8 — Policy / Rule Layer

**User Story:** As a credit analyst, I want transparent deterministic rules with versioned, configurable thresholds, so that every escalation references an explainable rule and historical runs stay linked to the rule version used.

#### Acceptance Criteria

1. WHEN rules are defined THEN the system SHALL keep policy threshold, peer benchmark, and historical deterioration as three separate concepts.
2. WHEN rules evaluate THEN the system SHALL support mandatory escalation for hard policy breach, critical missing evidence, and material AI/deterministic conflict; and analyst review for peer anomaly, adverse historical deterioration beyond threshold, and proximity to a policy boundary.
3. WHEN thresholds are configured THEN they SHALL be stored in configuration (not buried in code) and labelled `ILLUSTRATIVE — NOT BANK POLICY`.
4. WHEN an escalation triggers THEN it SHALL identify the exact rule ID.
5. WHEN rules change THEN they SHALL be versioned and the change SHALL create an audit event.
6. WHEN a historical run completes THEN it SHALL remain linked to the rule version in effect at run time.

### Requirement 9 — Generative Credit Analysis

**User Story:** As a credit analyst, I want the LLM to interpret verified facts (not recreate them) and return schema-validated structured output, so that no unsupported factual claim enters the memo and uncertainty is surfaced.

#### Acceptance Criteria

1. WHEN the analysis LLM is invoked THEN it SHALL receive only canonical facts, deterministic metrics, trends, benchmarks, needed source snippets, data limitations, open conflicts, and the approved prompt template.
2. WHEN analysis is produced THEN it SHALL be structured JSON with `business_overview`, `repayment_analysis`, `key_risks`, `mitigants`, `data_limitations`, and `questions_for_human`.
3. WHEN an analytical claim is made THEN it SHALL include claim text, evidence IDs, fact-vs-interpretation designation, materiality, and uncertainty/caveat.
4. WHEN the prompt is applied THEN it SHALL require the model to not invent information, not infer missing values, distinguish fact from interpretation, use only supplied evidence, cite evidence IDs, surface contradictions, and state when a conclusion cannot be reached.
5. WHEN output is received THEN it SHALL validate against schema before use.
6. WHEN a hard numerical fact appears THEN it SHALL carry evidence.
7. IF critical evidence is missing THEN the system SHALL produce a caveat/escalation rather than confident prose.

### Requirement 10 — Challenge Layer

**User Story:** As a credit analyst, I want a second-pass critic that tests the analysis, so that unsupported claims, contradictions, and omissions are flagged without overwriting the original analysis.

#### Acceptance Criteria

1. WHEN the challenge pass runs THEN it SHALL test whether each important claim is supported, whether any narrative contradicts deterministic metrics, whether a material risk is omitted, whether claimed mitigants are relevant, whether critical evidence is missing, whether alternative explanations are plausible, and whether certainty is overstated.
2. WHEN challenges are produced THEN each SHALL include `claim_id`, `issue_type`, `severity`, `reason`, and `evidence_refs`.
3. WHEN the challenge completes THEN its results SHALL NOT overwrite the original analysis.
4. WHEN a challenge is accepted or rejected THEN the decision SHALL be logged.
5. IF a high-severity challenge is raised THEN it SHALL enter the escalation queue.

### Requirement 11 — Escalation Engine

**User Story:** As a credit analyst, I want only material exceptions routed to human review via explicit event-based rules, so that escalations are explainable and cannot disappear without resolution.

#### Acceptance Criteria

1. WHEN escalations are evaluated THEN the system SHALL use event-based rules and SHALL avoid a single opaque risk score in the MVP.
2. WHEN exceptions arise THEN the system SHALL categorize them as data integrity, financial rules, AI/deterministic conflict, or evidence categories.
3. WHEN an escalation is created THEN it SHALL include `escalation_id`, `case_id`, `severity`, `rule_id`, `reason`, `triggered_at`, `evidence_refs`, `status`, and `resolution`.
4. WHEN an escalation exists THEN it SHALL have a reason and a rule/source/trigger.
5. WHEN an escalation is open THEN it SHALL NOT disappear without resolution.
6. WHEN a human resolves an escalation THEN the system SHALL create a new event.
7. WHEN mandatory escalations are unresolved THEN the case status SHALL reflect them.

### Requirement 12 — Human-in-the-Loop Review

**User Story:** As a reviewer, I want to resolve ambiguity and material exceptions with a non-destructive, fully logged workflow, so that before/after values stay visible and final recommendations require explicit sign-off.

#### Acceptance Criteria

1. WHEN a reviewer acts THEN they MAY verify a source, resolve a conflicting figure, approve an accounting adjustment, approve assumptions, confirm the peer set, resolve contradictory evidence, accept/reject an AI interpretation, modify risk materiality, or sign off the final recommendation.
2. WHEN any human action occurs THEN the system SHALL record reviewer, timestamp, prior value, new value (if changed), reason, linked evidence, and optional comment.
3. WHEN a human edit is made THEN it SHALL be non-destructive and before/after values SHALL remain visible.
4. WHEN human approval is required THEN it SHALL apply to final recommendation, material accounting adjustments, resolved conflicts, facility structure recommendations, policy exceptions, and judgment-heavy risk rankings.
5. WHEN the final output is produced THEN it SHALL identify unresolved exceptions.
6. WHEN a recommendation is marked final THEN it SHALL require explicit sign-off.

### Requirement 13 — Output Generation (JSON + PDF)

**User Story:** As a credit analyst, I want the canonical JSON generated first and the PDF rendered deterministically from it, so that JSON and PDF always agree and every memo claim maps back to evidence.

#### Acceptance Criteria

1. WHEN outputs are generated THEN the canonical case JSON SHALL be produced first and SHALL contain source references, facts, metrics, analysis, escalations, human reviews, final status, and version metadata.
2. WHEN the PDF is produced THEN it SHALL be rendered from the JSON using deterministic templates and SHALL NOT independently ask an LLM to generate narrative from scratch.
3. WHEN the PDF is rendered THEN it SHALL include the suggested sections (case summary, business overview, financial overview, metrics/trends, risk register, mitigants, exceptions/missing evidence, draft recommendation, sources appendix, review/approval history).
4. WHEN the PDF is produced THEN every numerical value SHALL match the canonical JSON.
5. WHEN the PDF is produced THEN every major claim SHALL map back to an evidence/analysis object and the PDF SHALL NOT silently include content absent from JSON.
6. WHEN the PDF is produced THEN it SHALL show the as-of date, evidence cutoff, and any unresolved exceptions.
7. WHEN the same case version is re-rendered THEN it SHALL produce equivalent content.

### Requirement 14 — Audit Trail

**User Story:** As an auditor, I want an append-only event log, so that the full history of a case is reconstructable and no past event is ever modified.

#### Acceptance Criteria

1. WHEN any significant action occurs THEN the system SHALL emit an append-only audit event.
2. WHEN event types are recorded THEN they SHALL include at least: `case_created`, `document_ingested`, `document_superseded`, `fact_extracted`, `fact_verified`, `fact_conflict_detected`, `fact_human_corrected`, `metric_calculated`, `benchmark_generated`, `rule_triggered`, `llm_run`, `challenge_created`, `escalation_created`, `escalation_resolved`, `human_review`, `memo_generated`, `case_finalized`.
3. WHEN an event is recorded THEN it SHALL include `event_id`, `case_id`, `event_type`, `timestamp`, `actor_type` (human/system/model), `actor_id`, `before`, `after`, `reason`, and `linked_objects`.
4. WHEN an event exists THEN it SHALL NEVER be modified after creation.

### Requirement 15 — Prompt & Model Registry

**User Story:** As an engineer, I want prompts treated as versioned artifacts with full run logging, so that LLM behavior is reproducible and prompt changes trigger regression tests.

#### Acceptance Criteria

1. WHEN a prompt is used THEN it SHALL be identified by name and version (e.g., `business_analysis_v1.0`).
2. WHEN any LLM run occurs THEN the system SHALL record prompt ID, prompt version, prompt hash, model ID, model configuration, case version, evidence IDs, raw response, parsed response, and validation outcome.
3. WHEN provider calls are made THEN they SHALL go through a single `LLMClient` abstraction (`extract`, `analyze`, `challenge`) and SHALL NOT spread provider-specific calls throughout the codebase.
4. WHEN generative calls are made THEN the system SHALL request structured JSON output against a fixed schema, use temperature 0 where supported, and validate before use.
5. WHEN a prompt is updated THEN it SHALL trigger regression tests.

### Requirement 16 — Testing & Evaluation Harness

**User Story:** As an engineer, I want component- and system-level tests plus an evaluation harness, so that correctness, groundedness, graceful degradation, and reproducibility are demonstrable.

#### Acceptance Criteria

1. WHEN unit tests run THEN they SHALL cover formulas, unit conversion, date normalization, tolerance logic, policy rules, trend rules, escalation rules, versioning, and hashing, with formula accuracy exact except for explicit rounding tolerance.
2. WHEN a golden extraction dataset is defined THEN each field SHALL store correct value, period, units, source location, and allowed tolerance, and the system SHALL measure field accuracy, numeric accuracy, citation accuracy, and required-field recall reported by field type.
3. WHEN temporal backtests run THEN they SHALL support expanding-window and rolling-window designs and SHALL NEVER mix future and past observations.
4. WHEN missing-data ablations run THEN the system SHALL create degraded variants (FULL, NO_XLSX, NO_XBRL, PDF_ONLY, NO_INDUSTRY_DATA, NO_FUEL_DATA, NO_DEBT_MATURITY_TABLE, NO_INTERIM_STATEMENTS), measure `performance_drop = score_full - score_degraded`, and demonstrate graceful degradation (caveat/escalation, never invented values).
5. WHEN unsupported-claim tests run THEN every factual statement SHALL be verified against at least one supporting evidence reference, targeting zero unsupported hard factual claims in the final memo.
6. WHEN AI-vs-deterministic delta tests run THEN seeded disagreements SHALL be detected, escalated, and never silently merged.
7. WHEN prompt/model A/B tests run THEN they SHALL use identical cases with the reviewer blinded and SHALL NOT use "which sounds better" as the primary metric.
8. WHEN an end-to-end reproducibility test runs THEN the same snapshot/rules/model/prompt/config SHALL reproduce deterministic metrics, source set, escalation rules, and canonical JSON structure, storing the original LLM run rather than assuming exact regeneration.
9. WHEN at least one temporal backtest, one rolling-vs-expanding comparison (if an ML component is used), at least four missing-data ablations, and at least one A/B comparison are executed THEN the acceptance target SHALL be considered met.

### Requirement 17 — Case Workbench UI

**User Story:** As a credit analyst, I want a frozen case workbench rather than a mutating dashboard, so that historical case versions remain immutable and later reviews create new snapshots.

#### Acceptance Criteria

1. WHEN the workbench is presented THEN it SHALL show as-of date, evidence cutoff, and navigable sections (Sources, Canonical Data, Metrics, Business Analysis, Financial Analysis, Risks, Exceptions, Human Review, Memo).
2. WHEN a case is viewed THEN the workbench SHALL display counts of open exceptions, critical missing sources, AI/deterministic conflicts, and decisions awaiting humans.
3. WHEN a historical case version exists THEN it SHALL remain frozen.
4. WHEN a later review occurs THEN it SHALL create a new case version/snapshot rather than silently rewriting the old one.

### Requirement 18 — Security & Data Hygiene

**User Story:** As an engineer, I want baseline security controls, so that the PoC uses only safe data and avoids common handling risks.

#### Acceptance Criteria

1. WHEN data are used THEN the system SHALL use only public or synthetic data unless explicitly authorized otherwise.
2. WHEN secrets are needed THEN they SHALL be read from environment variables and SHALL NEVER be hardcoded or logged.
3. WHEN files are uploaded THEN the system SHALL sanitize filenames, restrict file types, validate JSON, and prevent arbitrary path traversal.
4. WHEN external model/provider calls are made THEN they SHALL be recorded, and data SHALL NOT be sent to external models unless permitted.

---

## Critical Design Rules (apply across all requirements)

1. AI is never the only source of truth.
2. Never overwrite evidence.
3. Every important fact must have provenance.
4. Every deterministic metric must expose its inputs and formula.
5. Peer statistics are not bank policy.
6. Missing is not zero.
7. Uncertainty must propagate downstream.
8. Conflicts remain visible until resolved.
9. Escalation rules must be explainable.
10. Human judgment must be logged.
11. PDF is a rendering of canonical JSON.
12. Historical backtests must prohibit future information.
13. Prompt changes are versioned and regression-tested.
14. Do not add ML unless it outperforms a simpler baseline and remains explainable.
15. A successful system should fail safely when information is incomplete.
