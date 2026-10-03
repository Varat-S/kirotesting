# Requirements — AI-Assisted Credit Memo Generator (PoC)

## Introduction

This proof-of-concept converts heterogeneous borrower evidence (PDFs, XBRL, Excel, CSV, HTML, news) into an **auditable, source-grounded draft credit memo** and a **machine-readable JSON case record**. The system deliberately refuses to treat generative AI as the sole source of truth. It combines deterministic parsing and calculation, source provenance, rule-based checks, peer/historical benchmarking, generative analysis, escalation rules, human review, immutable version history, and reproducible testing.

The guiding objective is **defensibility and traceability**, not maximum model complexity.

The system answers five core questions: (1) Who is the borrower? (2) Can they repay? (3) How is the bank protected? (4) Can every important conclusion be traced to evidence? (5) Does the system know when evidence is insufficient or conflicting?

### How to read this document (Requirements vs Design vs Configuration)

These requirements state **behavioral guarantees** that must hold independent of implementation. Specific technology choices (Python, FastAPI, SQLite, SQLAlchemy, Jinja2, local file store) and PoC scoping choices (the airline sector, Delta as borrower, the United/American/Southwest comparison set) are **design/configuration decisions** recorded in `design.md`, not normative requirements. Replaceable settings (tolerances, illustrative policy thresholds, peer cohort membership, metric-definition components, adverse-direction definitions, escalation proximity thresholds, source-criticality profiles) are **configuration** and are versioned and hashed per Requirement 21; requirements below reference such values only as *illustrative examples*, never as fixed law.

The PoC's current scope is **one industry at a time**; the first configured industry is U.S. scheduled passenger airlines. Every case carries an explicit `as_of_date` and `evidence_cutoff_timestamp`; no evidence available after the cutoff may be used in a historical run (Requirement 20).

### Non-Goals

The PoC does not build a production credit-approval system, an autonomous lending decision-maker, an ML model that discovers binding credit policy, a real-time dashboard that silently rewrites history, a general multi-industry system, or a large swarm of overlapping agents. No learned credit-policy thresholds are included in the MVP unless explicitly justified, versioned, and validated against a simpler baseline. The LLM must never overwrite raw sources, invent missing values, perform authoritative calculations that deterministic code can do, change policy thresholds, approve/decline credit, or resolve material conflicts without human review.

---

## Requirements

### Requirement 1 — Source Ingestion & Versioning

**User Story:** As a credit analyst, I want to create a case and ingest raw source files immutably, so that every memo can be traced back to the exact evidence package that produced it.

#### Acceptance Criteria

1. WHEN a user creates a case THEN the system SHALL assign a unique `case_id` and require an `as_of_date`.
2. WHEN a case is created THEN the system SHALL require an `evidence_cutoff_timestamp`.
3. WHEN a file is uploaded THEN the system SHALL accept common evidence formats (at minimum PDF, XLSX, CSV, JSON, and HTML/XBRL) where practical.
4. WHEN a file is ingested THEN the system SHALL save the original file unchanged and SHALL NEVER mutate or overwrite an existing object.
5. WHEN a file is ingested THEN the system SHALL compute and store its SHA-256 hash.
6. WHEN a file is ingested THEN the system SHALL record original filename, file type, ingest timestamp, source, document date, publication/availability date (if known), entity, fiscal period, and hash as a `DocumentRecord`.
7. WHEN identical bytes are re-uploaded THEN the system SHALL produce the same hash.
8. WHEN a corrected document is uploaded THEN the system SHALL create a new `DocumentRecord` and set its `supersedes`/`version` links rather than overwriting the prior record.
9. IF a historical run is executed THEN the system SHALL reject any document whose availability-by-cutoff status fails the temporal-eligibility check of Requirement 20.
10. WHEN any ingestion occurs THEN the system SHALL emit a corresponding append-only audit event.

### Requirement 2 — Entity Resolution & Consolidation Scope

**User Story:** As a credit analyst, I want every financial fact bound to an explicit legal entity and consolidation scope, so that numerically correct values are never silently attributed to the wrong borrower, subsidiary, parent, guarantor, or consolidated group.

#### Acceptance Criteria

1. WHEN entities are registered THEN the system SHALL maintain an `EntityRecord` with at least `entity_id`, `legal_name`, aliases/tickers, `entity_type` (e.g., parent, operating subsidiary, borrower, guarantor), `parent_entity_id`, `borrower_flag`, `guarantor_flag`, jurisdiction (if available), and `source_refs`.
2. WHEN a financial fact is extracted THEN it SHALL carry `entity_id`, `consolidation_scope`, and the `reporting_entity_name` as shown in source where useful.
3. WHEN facts originate from different entities or consolidation scopes THEN the system SHALL NOT silently combine them.
4. WHEN borrower, parent, or subsidiary identity is ambiguous THEN the system SHALL escalate rather than guess.
5. WHEN a legal borrower and its ultimate/consolidated parent both appear THEN the system SHALL preserve them as distinct entities.
6. WHEN an entity mismatch is detected between a fact and its expected scope THEN the system SHALL raise a conflict/escalation and SHALL NOT merge the values.

### Requirement 3 — Parsing & Extraction

**User Story:** As a credit analyst, I want heterogeneous evidence transformed into structured facts with deterministic parsing preferred where appropriate, so that numerical facts are reliable and every fact is traceable.

#### Acceptance Criteria

1. WHEN a source contains structured data THEN the system SHALL prefer deterministic parsing, applying parser types in a configurable precedence (illustratively: XBRL/structured filings, then XLSX/CSV, then PDF tables, then free-form PDF text, with OCR only as a last resort when required by the evidence).
2. WHEN the system extracts financial inputs THEN it SHALL target the configured financial-fact set (illustratively revenue, EBITDA inputs, EBIT, cash, total debt, interest expense, CFO, capex, debt maturities, liquidity, and operating metrics) where available.
3. WHEN qualitative content is needed THEN the system SHALL use the LLM for management descriptions, ownership, corporate structure, competitive position, industry risks, major capex/M&A, qualitative debt terms, narrative risk factors, and facts in poorly structured text.
4. WHEN the LLM extracts a fact THEN it SHALL return fact name, value/statement, period, source reference, confidence/evidence status, and SHALL NOT include unsupported inference.
5. WHEN any factual field is extracted THEN it SHALL carry at least one source reference and (for financial facts) the metadata of Requirement 4.
6. WHEN the LLM extracts a fact THEN that fact SHALL NOT automatically enter `verified` state.
7. WHEN a numerical fact exists from multiple routes THEN precedence SHALL be governed by the compatibility checks of Requirement 6 — the system SHALL NOT apply a single global source ranking that erases disagreement.
8. WHEN exact duplicate facts are produced THEN the system SHALL deduplicate them.
9. WHEN facts conflict THEN the system SHALL keep both visible and SHALL NOT silently merge them.

### Requirement 4 — Financial Fact Schema (Entity/Period/Unit/Definition Metadata)

**User Story:** As a credit analyst, I want financial facts to carry enough metadata to judge comparability, so that two facts are never treated as equivalent until entity, period, unit/scale, currency, and accounting definition are known to match.

#### Acceptance Criteria

1. WHEN a financial fact is stored THEN the schema SHALL support `currency`, `scale` (units/thousands/millions/billions), `period_start`, `period_end`, `period_type` (instant, quarter, FY, TTM where used), `fiscal_year`, `accounting_basis` (e.g., GAAP / non-GAAP / management-adjusted), `consolidation_scope`, `entity_id`, `restated` flag, source taxonomy/concept name (for XBRL), source label/row name, data freshness, `normalization_method`, and definition/version metadata where adjusted.
2. WHEN a fact is qualitative THEN the system SHALL NOT require every financial field, but the schema SHALL still support them.
3. WHEN two financial facts are candidates for comparison or reconciliation THEN the system SHALL NOT treat them as equivalent until entity, period, unit/scale, currency, and accounting-definition compatibility are checked.
4. WHEN a value is normalized THEN the system SHALL preserve the raw extracted value and raw unit separately from the normalized value and unit.
5. WHEN unit/scale and date/period conversions occur THEN they SHALL be deterministic and covered by tests.

### Requirement 5 — Canonical Evidence Snapshot

**User Story:** As a downstream consumer, I want a single normalized evidence interface where each field carries value, state, and provenance, so that no derived logic depends on untraceable or ambiguous values.

#### Acceptance Criteria

1. WHEN the mid-pipeline evidence object is produced THEN it SHALL be named `CanonicalEvidenceSnapshot` and SHALL contain documents, entities, facts, normalized financials, provenance, and data-quality states.
2. WHEN a field is stored in the snapshot THEN it SHALL include value, state, and provenance, and SHALL include data freshness.
3. WHEN metrics are derived THEN the system SHALL store formula inputs independently from derived metrics.
4. WHEN a value is missing, unknown, not-disclosed, not-applicable, stale, conflicting, or zero THEN each state SHALL remain distinct, and `missing` SHALL NEVER be represented as numerical zero.
5. WHEN the `CanonicalEvidenceSnapshot` is produced THEN it SHALL validate against a versioned JSON Schema.
6. WHEN a `CanonicalEvidenceSnapshot` version exists THEN downstream objects SHALL reference it by version.

### Requirement 6 — Structured-Source Precedence via Compatibility Checks

**User Story:** As a credit analyst, I want structured data preferred only when it is genuinely comparable, so that an XBRL/XLSX value never silently overrides a contradicting source of a different definition.

#### Acceptance Criteria

1. WHEN choosing between a structured and a free-form numerical fact THEN the system SHALL prefer the structured value ONLY when entity matches, period matches, units/scale match, accounting definition matches, restatement status is compatible, and source authority is appropriate.
2. IF any compatibility check fails THEN the system SHALL NOT automatically prefer the structured source, SHALL preserve both observations, SHALL mark a conflict or definition mismatch, and SHALL route for reconciliation.
3. WHEN sources are compared THEN the system SHALL record source-authority metadata but SHALL NOT use a single global ranking to erase disagreements.

### Requirement 7 — Reconciliation & Data Quality (Zero-Safe)

**User Story:** As a credit analyst, I want independent extraction routes compared with explicit, zero-safe tolerances, so that facts are promoted only when genuinely comparable and conflicts are preserved for review.

#### Acceptance Criteria

1. WHEN a numerical field has multiple comparable sources THEN the system SHALL always compute an absolute delta.
2. WHEN the denominator exceeds a configurable near-zero floor THEN the system SHALL also compute a relative delta; OTHERWISE it SHALL use an absolute tolerance or field-specific comparison instead of dividing.
3. WHEN a comparison is performed THEN the system SHALL record the `comparison_method` used, from at least: `relative`, `absolute`, `exact`, `definition_mismatch`, `not_comparable`.
4. WHEN comparing fields THEN the system SHALL apply versioned, field-specific tolerances from configuration (illustratively revenue 0.5%, debt 0.5%, ratio 0.01x) labelled illustrative, not bank policy.
5. IF values are within tolerance THEN the fact state SHALL become `verified`.
6. IF values are outside tolerance THEN the fact state SHALL become `conflicting`.
7. IF only one reliable source exists THEN the fact state SHALL become `unverified`.
8. IF no source exists THEN the fact state SHALL become `missing`.
9. WHEN non-numeric disagreement occurs THEN the system SHALL create a contradiction record rather than force a merge.
10. WHEN a conflict is recorded THEN it SHALL preserve both values and both source references.
11. WHEN a human resolves a conflict THEN the system SHALL create a new event and SHALL NOT delete the conflict.
12. WHEN requested THEN the system SHALL be able to show before-resolution and after-resolution states.

### Requirement 8 — Deterministic Financial Metric Engine (Versioned Definitions)

**User Story:** As a credit analyst, I want financial metrics computed in code with explicit inputs, formulas, and definition versions, so that no LLM arithmetic can override deterministic results and every metric is reproducible and traceable to a stated definition.

#### Acceptance Criteria

1. WHEN metrics are computed THEN the system SHALL calculate the configured metric set (illustratively revenue growth, operating margin, net debt, net debt/EBITDA, interest coverage, CFO/EBITDA cash conversion, free cash flow, liquidity, capex/revenue, load factor, and at least one operating cost indicator) where data allow.
2. WHEN a metric is computed THEN the system SHALL store `metric_definition_id`, `metric_definition_version`, `formula_id`, inputs, input fact IDs, result, period, units, and `engine_version`.
3. WHEN a metric definition is recorded THEN it SHALL state which components are included/excluded (e.g., what "EBITDA", "net debt", "FCF", "liquidity" comprise).
4. IF a denominator is zero, near-zero, missing, conflicting, or economically misleading THEN the system SHALL return an explicit state (`not_meaningful`, `missing_input`, or `requires_review`) instead of a normal ratio.
5. WHEN golden examples are evaluated THEN deterministic metrics SHALL achieve a 100% pass rate.
6. WHEN the same case is re-run under the same definition/engine versions THEN metric results SHALL be identical.
7. WHEN an LLM produces an arithmetic result THEN it SHALL NEVER override a deterministic metric.
8. WHEN a metric definition changes THEN it SHALL create an audit event, SHALL NOT rewrite historical outputs, and SHALL trigger deterministic regression tests; historical runs SHALL remain linked to the definition version used.

### Requirement 9 — Historical Trend Layer

**User Story:** As a credit analyst, I want deterministic trend analysis per metric, so that deterioration or improvement is visible without implying continuity across missing years.

#### Acceptance Criteria

1. WHEN trends are computed THEN the system SHALL store current value, previous period, 2-year and 3-year change, slope/direction, and volatility where relevant for each metric.
2. WHEN direction is assessed THEN the system SHALL use configured, metric-specific definitions of adverse direction.
3. WHEN trend logic runs THEN it SHALL be deterministic.
4. IF intermediate years are missing THEN the system SHALL NOT silently imply a continuous trend.
5. WHEN a structural break occurs THEN it SHALL be visible.

### Requirement 10 — Peer Benchmark Layer (Small-Cohort-Aware)

**User Story:** As a credit analyst, I want the borrower compared to a defined peer cohort as an anomaly signal, with honest treatment of small samples, so that benchmarking informs review without manufacturing misleading percentiles or acting as an approval threshold.

#### Acceptance Criteria

1. WHEN the cohort is small THEN the system SHALL report raw peer values, rank, median, min/max, and a simple distribution, and SHALL suppress or clearly label unstable percentiles.
2. WHEN sample size/configuration permits THEN the system MAY calculate P90/P95; OTHERWISE it SHALL NOT present them as reliable.
3. WHEN benchmarks are produced THEN the system SHALL record `benchmark_method` and `sample_size`, along with the cohort definition and benchmark date/period.
4. WHEN a larger company-year panel is configured THEN the system MAY use it, labelling any synthetic/illustrative peer values explicitly.
5. WHEN benchmarks are computed THEN the borrower SHALL NOT be accidentally included twice in its own peer set.
6. WHEN peer data are presented THEN they SHALL be reproducible from source data.
7. WHEN benchmarking results are used THEN they SHALL be treated as an anomaly signal and NEVER as a credit approval threshold.

### Requirement 11 — Policy / Rule Layer

**User Story:** As a credit analyst, I want transparent deterministic rules with versioned, configurable thresholds, so that every escalation references an explainable rule and historical runs stay linked to the rule version used.

#### Acceptance Criteria

1. WHEN rules are defined THEN the system SHALL keep policy threshold, peer benchmark, and historical deterioration as three separate concepts.
2. WHEN rules evaluate THEN the system SHALL support mandatory escalation for hard policy breach, critical missing evidence, and material AI/deterministic conflict; and analyst review for peer anomaly, adverse historical deterioration beyond threshold, and proximity to a policy boundary.
3. WHEN thresholds are configured THEN they SHALL be stored in versioned configuration (not buried in code) and labelled `ILLUSTRATIVE — NOT BANK POLICY`.
4. WHEN an escalation triggers THEN it SHALL identify the exact rule ID.
5. WHEN rules change THEN they SHALL be versioned and the change SHALL create an audit event.
6. WHEN a historical run completes THEN it SHALL remain linked to the rule version in effect at run time.

### Requirement 12 — Generative Credit Analysis

**User Story:** As a credit analyst, I want the LLM to interpret verified facts (not recreate them) and return schema-validated structured output, so that no unsupported factual claim enters the memo and uncertainty is surfaced.

#### Acceptance Criteria

1. WHEN the analysis LLM is invoked THEN it SHALL receive only canonical facts, deterministic metrics, trends, benchmarks, needed source snippets, data limitations, open conflicts, and the approved prompt template.
2. WHEN analysis is produced THEN it SHALL be structured JSON with `business_overview`, `repayment_analysis`, `key_risks`, `mitigants`, `data_limitations`, and `questions_for_human`.
3. WHEN an analytical claim is made THEN it SHALL include claim text, evidence IDs, fact-vs-interpretation designation, materiality, and uncertainty/caveat.
4. WHEN the prompt is applied THEN it SHALL require the model to not invent information, not infer missing values, distinguish fact from interpretation, use only supplied evidence, cite evidence IDs, surface contradictions, and state when a conclusion cannot be reached.
5. WHEN output is received THEN it SHALL validate against schema before use.
6. WHEN a hard numerical fact appears THEN it SHALL carry evidence.
7. IF critical evidence is missing THEN the system SHALL produce a caveat/escalation rather than confident prose.

### Requirement 13 — Challenge Layer & Claim Grounding

**User Story:** As a credit analyst, I want a second-pass critic plus a two-part grounding check, so that a claim is never treated as grounded merely because a citation ID is attached.

#### Acceptance Criteria

1. WHEN the challenge pass runs THEN it SHALL test whether each important claim is supported, whether any narrative contradicts deterministic metrics, whether a material risk is omitted, whether claimed mitigants are relevant, whether critical evidence is missing, whether alternative explanations are plausible, and whether certainty is overstated.
2. WHEN challenges are produced THEN each SHALL include `claim_id`, `issue_type`, `severity`, `reason`, and `evidence_refs`.
3. WHEN the challenge completes THEN its results SHALL NOT overwrite the original analysis.
4. WHEN a challenge is accepted or rejected THEN the decision SHALL be logged.
5. IF a high-severity challenge is raised THEN it SHALL enter the escalation queue.
6. WHEN a claim is evaluated THEN the system SHALL perform TWO separate checks: (A) citation presence — does the claim reference evidence; and (B) evidence entailment/support — does the cited evidence actually support the claim.
7. WHEN a claim is scored THEN it SHALL receive one of the states: `supported`, `partially_supported`, `unsupported`, `contradictory`, `not_verifiable`.
8. WHEN grounding is assessed THEN a claim SHALL NOT be marked grounded solely because a citation ID is attached; entailment SHALL use deterministic checks where possible and MAY use a second structured LLM judge, with manual adjudication available for high-severity factual claims.

### Requirement 14 — Escalation Engine

**User Story:** As a credit analyst, I want only material exceptions routed to human review via explicit event-based rules, so that escalations are explainable and cannot disappear without resolution.

#### Acceptance Criteria

1. WHEN escalations are evaluated THEN the system SHALL use event-based rules and SHALL avoid a single opaque risk score in the MVP.
2. WHEN exceptions arise THEN the system SHALL categorize them as data integrity, financial rules, AI/deterministic conflict, or evidence categories.
3. WHEN an escalation is created THEN it SHALL include `escalation_id`, `case_id`, `severity`, `rule_id`, `reason`, `triggered_at`, `evidence_refs`, `status`, and `resolution`.
4. WHEN an escalation exists THEN it SHALL have a reason and a rule/source/trigger.
5. WHEN an escalation is open THEN it SHALL NOT disappear without resolution.
6. WHEN a human resolves an escalation THEN the system SHALL create a new event.
7. WHEN mandatory escalations are unresolved THEN the case status SHALL reflect them.

### Requirement 15 — Human-in-the-Loop Review

**User Story:** As a reviewer, I want to resolve ambiguity and material exceptions with a non-destructive, fully logged workflow, so that before/after values stay visible and final recommendations require explicit sign-off.

#### Acceptance Criteria

1. WHEN a reviewer acts THEN they MAY verify a source, resolve a conflicting figure, approve an accounting adjustment, approve assumptions, confirm the peer set, resolve contradictory evidence, accept/reject an AI interpretation, modify risk materiality, or sign off the final recommendation.
2. WHEN any human action occurs THEN the system SHALL record reviewer, timestamp, prior value, new value (if changed), reason, linked evidence, and optional comment.
3. WHEN a human edit is made THEN it SHALL be non-destructive and before/after values SHALL remain visible.
4. WHEN human approval is required THEN it SHALL apply to final recommendation, material accounting adjustments, resolved conflicts, facility structure recommendations, policy exceptions, and judgment-heavy risk rankings.
5. WHEN the final output is produced THEN it SHALL identify unresolved exceptions.
6. WHEN a recommendation is marked final THEN it SHALL require explicit sign-off.

### Requirement 16 — Final Case Snapshot & Immutability

**User Story:** As an auditor, I want the final case object to be a distinct, immutable snapshot, so that outputs are reproducible and later changes never rewrite history.

#### Acceptance Criteria

1. WHEN the final case object is produced THEN it SHALL be named `FinalCaseSnapshot` and SHALL contain a `CanonicalEvidenceSnapshot` reference/version plus metrics, benchmarks, analysis, risks/mitigants, escalations, human reviews, recommendation draft/final status, and audit metadata.
2. WHEN a `FinalCaseSnapshot` is finalized THEN it SHALL be immutable.
3. WHEN a change occurs after finalization THEN it SHALL create a new snapshot/version that references its predecessor, leaving the original reproducible.
4. WHEN JSON/PDF outputs are generated THEN they SHALL be linked to the exact `FinalCaseSnapshot` they were rendered from.

### Requirement 17 — Output Generation (JSON + PDF)

**User Story:** As a credit analyst, I want the canonical JSON generated first and the PDF rendered deterministically from the `FinalCaseSnapshot`, so that JSON and PDF always agree and every memo claim maps back to evidence.

#### Acceptance Criteria

1. WHEN outputs are generated THEN the `FinalCaseSnapshot` JSON SHALL be produced first and SHALL contain source references, facts, metrics, analysis, escalations, human reviews, final status, and version metadata.
2. WHEN the PDF is produced THEN it SHALL be rendered from the `FinalCaseSnapshot` JSON using deterministic templates and SHALL NOT independently ask an LLM to generate narrative from scratch.
3. WHEN the PDF is rendered THEN it SHALL include the configured memo sections (illustratively case summary, business overview, financial overview, metrics/trends, risk register, mitigants, exceptions/missing evidence, draft recommendation, sources appendix, review/approval history).
4. WHEN the PDF is produced THEN every numerical value SHALL match the canonical JSON.
5. WHEN the PDF is produced THEN every major claim SHALL map back to an evidence/analysis object and the PDF SHALL NOT silently include content absent from JSON.
6. WHEN the PDF is produced THEN it SHALL show the as-of date, evidence cutoff, and any unresolved exceptions.
7. WHEN the same `FinalCaseSnapshot` is re-rendered THEN it SHALL produce equivalent content.

### Requirement 18 — Audit Trail

**User Story:** As an auditor, I want an append-only event log, so that the full history of a case is reconstructable and no past event is ever modified.

#### Acceptance Criteria

1. WHEN any significant action occurs THEN the system SHALL emit an append-only audit event.
2. WHEN event types are recorded THEN they SHALL include at least: `case_created`, `document_ingested`, `document_superseded`, `entity_registered`, `entity_mismatch_detected`, `fact_extracted`, `fact_verified`, `fact_conflict_detected`, `fact_human_corrected`, `metric_calculated`, `metric_definition_changed`, `benchmark_generated`, `rule_triggered`, `config_version_changed`, `llm_run`, `challenge_created`, `escalation_created`, `escalation_resolved`, `human_review`, `snapshot_finalized`, `memo_generated`, `case_finalized`.
3. WHEN an event is recorded THEN it SHALL include `event_id`, `case_id`, `event_type`, `timestamp`, `actor_type` (human/system/model), `actor_id`, `before`, `after`, `reason`, and `linked_objects`.
4. WHEN an event exists THEN it SHALL NEVER be modified after creation.

### Requirement 19 — Prompt, Model & Configuration Registries

**User Story:** As an engineer, I want prompts, models, and configuration treated as versioned artifacts with full run logging, so that behavior is reproducible and changes trigger regression tests.

#### Acceptance Criteria

1. WHEN a prompt is used THEN it SHALL be identified by name and version (e.g., `business_analysis_v1.0`).
2. WHEN any LLM run occurs THEN the system SHALL record prompt ID, prompt version, prompt hash, model ID, model configuration, case version, evidence IDs, raw response, parsed response, and validation outcome.
3. WHEN provider calls are made THEN they SHALL go through a single `LLMClient` abstraction (`extract`, `analyze`, `challenge`) and SHALL NOT spread provider-specific calls throughout the codebase.
4. WHEN generative calls are made THEN the system SHALL request structured JSON output against a fixed schema, use temperature 0 where supported, and validate before use, without claiming byte-exact reproducibility.
5. WHEN a prompt is updated THEN it SHALL trigger regression tests.
6. WHEN configuration is used THEN the system SHALL version and hash reconciliation tolerances, policy thresholds, peer definitions, metric definitions, adverse-trend rules, and escalation rules, and SHALL record which configuration versions each case snapshot used.
7. WHEN a historical run is re-executed THEN the system SHALL support two visibly distinguished modes: (A) reproduce historical run using the original configuration versions, and (B) re-evaluate historical evidence using the latest configuration versions.

### Requirement 20 — Temporal Leakage Controls (All Data Types)

**User Story:** As an evaluator, I want cutoff enforcement applied to every data type, so that historical runs reflect only what was knowable at the cutoff and never leak future information.

#### Acceptance Criteria

1. WHEN any external item is ingested THEN temporal controls SHALL apply to public filings, news, peer-company filings, market data, fuel/macro data, rating actions, external benchmark data, and any retrieved web content.
2. WHEN an external item is stored THEN the system SHALL record its observed/reported date (if relevant), `published_at`/`available_at`, `retrieved_at`, and source.
3. WHEN historical eligibility is determined THEN it SHALL be based on what was available by the `evidence_cutoff_timestamp`, NOT on when the pipeline downloaded the item.
4. WHEN a future-dated item is presented to a historical run THEN the system SHALL reject it, and leakage tests SHALL verify rejection for future filings, news, peer results, and market/macro values.

### Requirement 21 — No Silent Defaults

**User Story:** As a reviewer, I want absent settings surfaced rather than silently defaulted, so that no material assumption enters a case without a traceable, versioned source.

#### Acceptance Criteria

1. IF a field, threshold, peer set, metric definition, or assumption is absent THEN the system SHALL NOT silently substitute a default unless that default is explicitly versioned/configured.
2. WHEN a configured default is applied THEN the system SHALL surface the default's source and version.
3. WHEN a material finance assumption would be defaulted THEN the system SHALL require human approval if configured to do so.

### Requirement 22 — Source-Package Completeness / Required-Source Profile

**User Story:** As a credit analyst, I want a case/industry-specific expected source profile, so that missing-data behavior is deterministic rather than driven purely by LLM confidence.

#### Acceptance Criteria

1. WHEN a case is configured THEN the system SHALL load a case/industry-specific expected source profile that classifies each source type as `critical`, `important`, or `optional` (illustrative airline profile: annual filing, quarterly/interim filings, debt maturity disclosure, operating statistics, external industry dataset, optional macro/fuel dataset).
2. WHEN a critical source is missing THEN the system SHALL raise a mandatory escalation or explicitly record an inability to conclude.
3. WHEN an optional source is missing THEN the system SHALL record reduced coverage without necessarily escalating.
4. WHEN source-package completeness is assessed THEN the determination SHALL be deterministic and based on the configured profile, not on LLM confidence alone.

### Requirement 23 — Testing & Evaluation Harness

**User Story:** As an engineer, I want component- and system-level tests plus an evaluation harness grounded in declared truth, so that correctness, groundedness, graceful degradation, and reproducibility are demonstrable.

#### Acceptance Criteria

1. WHEN unit tests run THEN they SHALL cover formulas, unit/scale conversion, date/period normalization, zero-safe reconciliation, entity/scope compatibility, tolerance logic, policy rules, trend rules, escalation rules, versioning, hashing, and snapshot immutability, with formula accuracy exact except for explicit rounding tolerance.
2. WHEN a golden extraction dataset is defined THEN each field SHALL store correct value, entity, period, currency, scale, source location, allowed tolerance, and expected status, and the system SHALL measure field accuracy, numeric accuracy, citation accuracy, and required-field recall reported by field type.
3. WHEN reconciliation tests run THEN they SHALL cover matching values, conflicting values, zero values, near-zero values, adjusted-vs-GAAP mismatch, restated-vs-original, wrong-entity values, and wrong-period values.
4. WHEN a scored evaluation case is defined THEN a first-class `GroundTruthManifest` SHALL exist for it, containing at least `case_id`, `as_of_date`, `evidence_cutoff_timestamp`, approved source package/document IDs, verified facts, expected normalized values, expected deterministic metric outputs, known missing items, known contradictions, expected rule triggers, expected escalations, adjudicated material risks (if qualitative recall is evaluated), optional contemporaneous analyst conclusions, optional future-outcome data, annotator/reviewer metadata, and a manifest version.
5. WHEN any metric claims accuracy, risk recall, or escalation recall THEN it SHALL be computed against a declared `GroundTruthManifest`; no such metric SHALL be reported without declared ground truth.
6. WHEN historical ground truth is stored THEN `contemporaneous_ground_truth` (knowable/documented as of time T) SHALL be stored separately from `future_outcome` (what happened after T); future outcomes SHALL NEVER leak into model inputs at T, SHALL NEVER be treated as evidence the model was expected to know, and SHALL NEVER be mixed into contemporaneous factual-accuracy scoring.
7. WHEN temporal backtests run THEN they SHALL support expanding-window and (where a learned model is used) rolling-window designs, SHALL NEVER mix future and past observations, and SHALL allow the same historical evidence to be rerun under original configuration and optionally latest configuration.
8. WHEN missing-data ablations run THEN the system SHALL create degraded variants (at minimum FULL, NO_XLSX, NO_XBRL, PDF_ONLY, NO_INDUSTRY_DATA, NO_FUEL_DATA, NO_DEBT_MATURITY_TABLE, NO_INTERIM_STATEMENTS), measure field completeness/accuracy, metric availability, risk recall, unsupported-claim rate, correct escalation rate, human-review rate, and `performance_drop = score_full - score_degraded`, and demonstrate graceful degradation (caveat/escalation, never invented values).
9. WHEN seeded-conflict tests run THEN they SHALL cover AI-vs-deterministic numerical mismatch, qualitative contradiction, wrong entity, wrong period, stale evidence, and missing critical evidence, with each detected, escalated, and never silently merged.
10. WHEN claim-grounding tests run THEN they SHALL measure citation presence, evidence entailment, unsupported hard facts, and contradictions as separate quantities, targeting zero known unsupported hard factual claims in finalized golden-case outputs.
11. WHEN prompt/model A/B tests run THEN they SHALL use identical cases with the reviewer blinded, compare material-risk precision/recall, omissions, unsupported claims, groundedness, analyst corrections, and review time, and SHALL NOT use "which sounds better" as the primary metric.
12. IF a lower-level trainable ML component is used THEN its evaluation SHALL include a simple-baseline comparison, precision/recall/F1, AUROC only where meaningful, calibration where probabilities are produced, paired bootstrap confidence intervals, McNemar for paired classification where appropriate, and reported effect size and sample size; no ML component SHALL be required merely for appearance, and the foundation LLM SHALL NOT be assumed to be retrained by this PoC.
13. WHEN an end-to-end reproducibility test runs THEN the same source snapshot, configuration versions, metric-definition versions, rule versions, and prompt/model versions SHALL reproduce the source set, deterministic facts/metrics, rule triggers, canonical schema structure, and `FinalCaseSnapshot` linkage, storing original LLM outputs rather than assuming byte-identical regeneration.
14. WHEN acceptance is assessed THEN at least one temporal backtest, at least one rolling-vs-expanding comparison (if a learned model is used), at least four missing-data ablations, and at least one A/B comparison SHALL be demonstrated.

### Requirement 24 — Case Workbench UI (Optional)

**User Story:** As a credit analyst, I want a frozen case workbench rather than a mutating dashboard, so that historical case versions remain immutable and later reviews create new snapshots.

#### Acceptance Criteria

1. WHEN the workbench is presented THEN it SHALL show as-of date, evidence cutoff, and navigable sections (Sources, Canonical Data, Metrics, Business Analysis, Financial Analysis, Risks, Exceptions, Human Review, Memo).
2. WHEN a case is viewed THEN the workbench SHALL display counts of open exceptions, critical missing sources, AI/deterministic conflicts, and decisions awaiting humans.
3. WHEN a historical case version exists THEN it SHALL remain frozen.
4. WHEN a later review occurs THEN it SHALL create a new case version/snapshot (referencing its predecessor) rather than silently rewriting the old one.

### Requirement 25 — Security & Data Hygiene

**User Story:** As an engineer, I want baseline security controls, so that the PoC uses only safe data and avoids common handling risks.

#### Acceptance Criteria

1. WHEN data are used THEN the system SHALL use only public or synthetic data unless explicitly authorized otherwise.
2. WHEN secrets are needed THEN they SHALL be read from environment variables and SHALL NEVER be hardcoded or logged.
3. WHEN files are uploaded THEN the system SHALL sanitize filenames, restrict file types, validate JSON, and prevent arbitrary path traversal.
4. WHEN external model/provider calls are made THEN they SHALL be recorded, and data SHALL NOT be sent to external models unless permitted.

---

## Critical Design Rules (apply across all requirements)

Each rule has at least one acceptance test in `tasks.md` (see the traceability pass).

1. AI is never the only source of truth.
2. Never overwrite evidence.
3. Every important fact must have provenance.
4. Every financial fact carries explicit entity and consolidation scope.
5. Every deterministic metric must expose its inputs, formula, and definition version.
6. Peer statistics are not bank policy.
7. Missing is not zero; missing/unknown/not-disclosed/not-applicable/stale/conflicting/zero are distinct.
8. Structured data do not override contradictory sources without compatibility checks.
9. Reconciliation is zero-safe.
10. Uncertainty must propagate downstream.
11. Conflicts remain visible until resolved.
12. Escalation rules must be explainable and identify exact rule IDs.
13. Human judgment must be logged and non-destructive.
14. Citation presence is distinct from evidence entailment.
15. PDF is a rendering of the `FinalCaseSnapshot` JSON.
16. Finalized snapshots are immutable; changes create new versions.
17. Historical backtests must prohibit future information across all data types.
18. Prompt, model, and configuration changes are versioned and regression-tested.
19. No scored evaluation metric without a declared `GroundTruthManifest`.
20. Contemporaneous ground truth and future outcomes are stored separately.
21. No silent defaults; applied defaults are versioned and surfaced.
22. Do not add ML unless it outperforms a simpler baseline and remains explainable.
23. A successful system should fail safely when information is incomplete.
