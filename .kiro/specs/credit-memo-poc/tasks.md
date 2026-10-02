# Implementation Plan — AI-Assisted Credit Memo Generator (PoC)

Tasks follow the handoff's 9-milestone build order. Each task is incremental and references the requirements it fulfills. Build deterministic/versioned foundations first; add AI only after Milestone 3.

## Milestone 0 — Project Scaffold

- [ ] 0.1 Initialize the repository structure and tooling
  - Create `pyproject.toml` (Python 3.11+), `.env.example`, `README.md`.
  - Create `app/{api,core,models,schemas,services,prompts}` and the `services` subpackages (`ingestion`, `extraction`, `reconciliation`, `metrics`, `benchmarking`, `analysis`, `escalation`, `reporting`, `audit`).
  - Create `data/{raw,parsed,normalized,derived,snapshots}`, `tests/{unit,integration,golden,ablation,prompts,end_to_end}`, `evals/{cases,annotations,results,reports}`, `output/{json,pdf}`.
  - Configure pytest and the FastAPI app entrypoint.
  - _Requirements: 18.2_

## Milestone 1 — Skeleton (no AI)

- [ ] 1.1 Define core persistence and config
  - Implement SQLAlchemy models for `cases`, `documents`, `audit_events` with SQLite backend.
  - Implement `app/core` config loader reading secrets from env vars (never hardcoded/logged).
  - _Requirements: 1.1, 1.2, 18.2_

- [ ] 1.2 Implement the append-only audit log
  - Implement `audit` service emitting events with `event_id, case_id, event_type, timestamp, actor_type, actor_id, before, after, reason, linked_objects`.
  - Enforce immutability (no update/delete paths); support all required event types.
  - _Requirements: 14.1, 14.2, 14.3, 14.4_

- [ ] 1.3 Implement case creation and ingestion with versioning
  - Add API + service to create a case (unique `case_id`, required `as_of_date`, required `evidence_cutoff_timestamp`).
  - Store original files unchanged; compute SHA-256; build `DocumentRecord` with all required metadata; accept PDF/XLSX/CSV/JSON/HTML-XBRL.
  - Never overwrite objects; on corrected upload create a new record and set `supersedes`.
  - Enforce cutoff rejection for documents newer than `evidence_cutoff_timestamp`; sanitize filenames; restrict file types; prevent path traversal.
  - Emit `case_created` / `document_ingested` / `document_superseded` audit events.
  - _Requirements: 1.1–1.10, 18.1, 18.3_

- [ ] 1.4 Define the basic canonical JSON schema
  - Implement Pydantic + JSON Schema (`schema_version`) for the top-level case record and `DocumentRecord`.
  - Validate on write; track schema version alongside snapshots.
  - _Requirements: 4.8_

- [ ] 1.5 Write unit tests for the skeleton
  - Test identical bytes → identical hash, no source mutation, audit event per file, cutoff rejection, and reconstructable source package.
  - _Requirements: 1.4, 1.7, 1.9, 1.10, 16.1_

## Milestone 2 — Deterministic Financial Pipeline

- [ ] 2.1 Implement the structured data parser (priority order)
  - Parse XBRL/structured filings, then XLSX/CSV, then PDF tables, then free-form PDF text; OCR only if required.
  - Target revenue, EBITDA inputs, EBIT, cash, total debt, interest expense, CFO, capex, debt maturities, liquidity, operating metrics.
  - _Requirements: 2.1, 2.2_

- [ ] 2.2 Build the normalized financial schema and unit/date normalization
  - Preserve raw value separately from normalized value; normalize units and dates; preserve reporting period; include data freshness.
  - Keep `missing`/`unknown`/`zero` distinct (`missing` never numerical zero).
  - _Requirements: 4.1, 4.2, 4.3, 4.5, 4.6, 4.7_

- [ ] 2.3 Implement the deterministic metric engine (5–8+ metrics)
  - Implement revenue growth, operating margin, net debt, net debt/EBITDA, interest coverage, CFO/EBITDA, free cash flow, liquidity, capex/revenue, load factor, and one operating cost indicator.
  - Store formula ID, inputs, input fact IDs, result, period, units, engine version.
  - Division safeguards: zero/missing/conflicting/misleading denominator → `not_meaningful | missing_input | requires_review`.
  - _Requirements: 5.1, 5.2, 5.3, 5.6_

- [ ] 2.4 Implement the historical trend layer
  - Per metric: current, previous period, 2Y/3Y change, slope/direction, volatility where relevant; metric-specific adverse direction.
  - Do not imply continuity across missing years; surface structural breaks.
  - _Requirements: 6.1–6.5_

- [ ] 2.5 Write deterministic unit tests + golden metric examples
  - 100% pass on manually calculated golden examples; identical results on re-run; cover unit conversion, date normalization, trend rules.
  - _Requirements: 5.4, 5.5, 16.1, 16.2_

## Milestone 3 — Provenance + Reconciliation

- [ ] 3.1 Implement the ExtractedFact model with source references
  - Fields: `fact_id, name, value, unit, period, status, extraction_method, confidence, source_refs[], created_by`; allowed states enumerated.
  - Every factual field carries ≥1 source reference.
  - _Requirements: 2.5, 4.1, 4.4_

- [ ] 3.2 Implement reconciliation and conflict detection
  - Compute `absolute_delta` / `relative_delta`; apply field-specific tolerances from config (illustrative labels).
  - Resolve to `verified | conflicting | unverified | missing`; structured sources precede AI for numerics; deduplicate exact duplicates; keep conflicts visible (no silent merge); contradiction records for non-numeric disagreement preserving both values + refs.
  - _Requirements: 2.7, 2.8, 2.9, 3.1–3.8_

- [ ] 3.3 Implement the human correction workflow (non-destructive)
  - Resolution creates a new event (does not delete conflict); show before/after states; emit `fact_human_corrected` audit events.
  - _Requirements: 3.9, 3.10, 12.2, 12.3_

- [ ] 3.4 Tests: reconciliation + missing-data states
  - Verify no large delta passes silently; conflicts preserve both values/refs; before/after recoverable; missing ≠ zero.
  - _Requirements: 3.4, 3.8, 4.6, 16.6_

## Milestone 4 — LLM Extraction

- [ ] 4.1 Implement the LLMClient abstraction + model-run registry
  - Single interface `extract/analyze/challenge`; request structured JSON on fixed schema; temperature 0 where supported; validate before use.
  - Log prompt ID/version/hash, model ID/config, case version, evidence IDs, raw + parsed response, validation outcome; emit `llm_run` events.
  - _Requirements: 15.2, 15.3, 15.4_

- [ ] 4.2 Build the prompt registry
  - Versioned prompt artifacts (e.g., `business_analysis_v1.0`); prompt updates trigger regression tests.
  - _Requirements: 15.1, 15.5_

- [ ] 4.3 Implement one qualitative extraction prompt + response schema
  - Extract management/ownership/structure/competitive position/industry risks/qualitative debt terms; return fact, value/statement, period, source ref, confidence/evidence status, no unsupported inference.
  - AI facts never auto-`verified`; validate against schema; attach source IDs.
  - _Requirements: 2.3, 2.4, 2.6_

- [ ] 4.4 Tests: AI vs deterministic delta
  - Seed disagreements → detected, escalated, never merged.
  - _Requirements: 16.6_

## Milestone 5 — Analysis + Challenge

- [ ] 5.1 Implement GenAI credit analysis (schema-validated)
  - Inputs limited to canonical facts, metrics, trends, benchmarks, needed snippets, data limitations, open conflicts, approved prompt.
  - Output JSON: `business_overview, repayment_analysis, key_risks, mitigants, data_limitations, questions_for_human`; each claim carries evidence IDs, fact/interpretation flag, materiality, uncertainty.
  - Prompt forbids inventing/inferring; requires evidence citations, contradiction surfacing, and explicit "cannot conclude" statements; validate before use.
  - _Requirements: 9.1–9.7_

- [ ] 5.2 Implement the challenge layer (second-pass critic)
  - Test support, metric contradictions, omitted risks, mitigant relevance, missing evidence, alternative explanations, overstated certainty.
  - Output challenges with `claim_id, issue_type, severity, reason, evidence_refs`; non-destructive; log accept/reject; high severity → escalation queue.
  - _Requirements: 10.1–10.5_

- [ ] 5.3 Implement the unsupported-claim detector
  - Verify each factual statement against ≥1 supporting evidence reference; target zero unsupported hard facts in final memo.
  - _Requirements: 9.6, 16.5_

## Milestone 6 — Benchmarking + Escalation

- [ ] 6.1 Implement the peer benchmark layer
  - Compute peer median, P25/P75/P90/P95, borrower rank/percentile; store cohort definition + benchmark date/period; prevent borrower double-count; label synthetic benchmarks; reproducible from source; treat as anomaly signal only.
  - _Requirements: 7.1–7.6_

- [ ] 6.2 Implement the versioned policy/rule layer
  - Keep policy threshold / peer benchmark / historical deterioration separate; thresholds in config labelled `ILLUSTRATIVE — NOT BANK POLICY`; rules versioned; threshold changes audited; historical runs linked to rule version.
  - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6_

- [ ] 6.3 Implement the escalation engine (event-based)
  - Event-based rules (no single opaque score); categories: data integrity, financial rules, AI/deterministic conflict, evidence.
  - Escalation record with all required fields + exact `rule_id`; cannot vanish unresolved; human resolution creates new event; case status reflects unresolved mandatory escalations; emit `rule_triggered`/`escalation_created`/`escalation_resolved`.
  - _Requirements: 8.4, 11.1–11.7_

- [ ] 6.4 Tests: policy/escalation + benchmark reproducibility
  - Rule-trigger accuracy; escalation precision/recall on seeded exceptions; benchmark reproducibility; metric-to-escalation flow (e.g., R-TREND-LEV-01).
  - _Requirements: 8.4, 16.1_

## Milestone 7 — Human Review

- [ ] 7.1 Implement the human-in-the-loop review workflow
  - Support all reviewer actions; record reviewer/timestamp/prior value/new value/reason/linked evidence/comment; non-destructive with before/after visible.
  - Require explicit sign-off for final recommendation + other gated items; final output identifies unresolved exceptions; emit `human_review` events.
  - _Requirements: 12.1–12.6_

## Milestone 8 — Outputs

- [ ] 8.1 Generate the canonical case JSON (first)
  - Include source refs, facts, metrics, analysis, escalations, human reviews, final status, version metadata; validate against JSON Schema; emit `memo_generated`/`case_finalized`.
  - _Requirements: 13.1_

- [ ] 8.2 Render the PDF memo from JSON (deterministic templates)
  - Jinja2 templates; sections per handoff; every number matches JSON; every major claim maps to evidence/analysis; no content absent from JSON; show as-of date, evidence cutoff, unresolved exceptions; stable re-render.
  - _Requirements: 13.2–13.7_

- [ ] 8.3 Tests: JSON↔PDF parity
  - Verify numerical parity and claim-to-evidence mapping; re-render equivalence.
  - _Requirements: 13.4, 13.5, 13.7_

## Milestone 9 — Evaluation Harness

- [ ] 9.1 Build the golden extraction dataset + scorer
  - Per field: correct value, period, units, source location, allowed tolerance; measure field/numeric/citation accuracy and required-field recall by field type.
  - _Requirements: 16.2_

- [ ] 9.2 Implement temporal backtesting (expanding + rolling)
  - Support both window designs; never mix future/past; compare designs without assuming one superior.
  - _Requirements: 16.3, 16.9_

- [ ] 9.3 Implement missing-data ablation harness
  - Variants FULL/NO_XLSX/NO_XBRL/PDF_ONLY/NO_INDUSTRY_DATA/NO_FUEL_DATA/NO_DEBT_MATURITY_TABLE/NO_INTERIM_STATEMENTS; compute `performance_drop`; verify graceful degradation (caveat/escalation, never invented values); run ≥4 cases.
  - _Requirements: 16.4, 16.9_

- [ ] 9.4 Implement prompt/model A/B testing (blinded)
  - Identical cases, reviewer blinded; compare material risks, unsupported claims, omissions, corrections, review time, groundedness; not "which sounds better"; run ≥1 comparison.
  - _Requirements: 16.7, 16.9_

- [ ] 9.5 Implement the end-to-end reproducibility test
  - Same snapshot/rules/model/prompt/config reproduces deterministic metrics, source set, escalation rules, canonical JSON structure; store original LLM run.
  - _Requirements: 16.8_

- [ ] 9.6 Produce the evaluation summary report
  - Export extraction/deterministic/generative/system/missing-data robustness metrics into `evals/reports`.
  - _Requirements: 16.9_

## Milestone 10 — Case Workbench UI (optional per MVP cut)

- [ ] 10.1 Build the frozen case workbench
  - Show as-of date, evidence cutoff, navigable sections, and counts (open exceptions, critical missing sources, AI/deterministic conflicts, awaiting-human decisions).
  - Keep historical versions frozen; a later review creates a new snapshot.
  - _Requirements: 17.1–17.4_
