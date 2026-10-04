# Implementation Plan — AI-Assisted Credit Memo Generator (PoC)

Tasks follow the revised milestone order: **core evidence/provenance model is built before any parser**, parsing is split into independently testable units, and tasks/tests exist for every new requirement. Build deterministic/versioned foundations first; add AI only from Milestone 6. Requirement references point to `requirements.md` (renumbered in the revision).

## Milestone 0 — Project Scaffold

- [x] 0.1 Initialize the repository structure and tooling
  - Create `pyproject.toml` (Python 3.11+), `.env.example`, `README.md`.
  - Create `app/{api,core,models,schemas,prompts,config}` and `app/services/{ingestion,entity,extraction,reconciliation,metrics,benchmarking,analysis,escalation,reporting,audit,evaluation}`.
  - Create `config/{tolerances,policy,peers,metric_defs,rules,source_profiles}`, `data/{raw,parsed,normalized,derived,snapshots}`, `tests/{unit,integration,golden,ablation,prompts,end_to_end,leakage}`, `evals/{cases,manifests,annotations,results,reports}`, `output/{json,pdf}`.
  - Configure pytest and the FastAPI app entrypoint.
  - _Requirements: 25.2_

## Milestone 1 — Core Evidence Model (data model before parsers)

- [x] 1.1 Define core persistence and the configuration registry
  - SQLAlchemy models for `cases`, `entities`, `documents`, `facts`, `fact_source_refs`, `snapshots`, `audit_events`, `config_versions` on SQLite.
  - `app/core` config loader reading secrets from env vars (never hardcoded/logged); configuration registry that versions + hashes tolerances, policy, peers, metric defs, trend rules, escalation rules, parser precedence, source profiles.
  - _Requirements: 19.6, 21.1, 21.2, 25.2_

- [x] 1.2 Implement the append-only audit log
  - Emit events with `event_id, case_id, event_type, timestamp, actor_type, actor_id, before, after, reason, linked_objects`; enforce immutability (no update/delete).
  - Support all required event types including `entity_registered`, `entity_mismatch_detected`, `metric_definition_changed`, `config_version_changed`, `snapshot_finalized`.
  - _Requirements: 18.1, 18.2, 18.3, 18.4_

- [x] 1.3 Define the EntityRecord and entity registry
  - `EntityRecord` with `entity_id, legal_name, aliases, tickers, entity_type, parent_entity_id, borrower_flag, guarantor_flag, jurisdiction?, source_refs`; preserve legal borrower distinct from consolidated parent.
  - _Requirements: 2.1, 2.5_

- [ ] 1.4 Define SourceRef, ExtractedFact / CanonicalFact, and fact-status enum
  - `SourceRef` (document/page/table/taxonomy_concept/row_label/cell).
  - Enriched fact schema: raw vs normalized value/unit, `currency, scale, period_start, period_end, period_type, fiscal_year, accounting_basis, consolidation_scope, entity_id, reporting_entity_name?, restated, taxonomy_concept?, source_label?, data_freshness, normalization_method, definition_version?, status, extraction_method, confidence, source_refs[], created_by`.
  - Status enum `verified|unverified|conflicting|missing|not_applicable|not_disclosed|stale`; `missing` never zero; qualitative facts need not populate every field.
  - Every factual field carries ≥1 source reference.
  - _Requirements: 2.2, 3.5, 4.1, 4.2, 4.4, 5.2, 5.4_

- [ ] 1.5 Define schema versions and the two snapshot schemas (skeleton)
  - Pydantic + JSON Schema skeletons for `CanonicalEvidenceSnapshot` and `FinalCaseSnapshot` with `schema_version` and `config_versions`; validate on write.
  - _Requirements: 5.1, 5.5, 16.1_

- [ ] 1.6 Unit tests for the core model
  - Fact-status distinctness (missing ≠ zero), audit immutability, config versioning/hashing, entity borrower-vs-parent separation.
  - _Requirements: 2.5, 4.4, 5.4, 18.4, 19.6, 23.1_

## Milestone 2 — Ingestion + Entity/Period Controls

- [ ] 2.1 Implement case creation and immutable ingestion
  - Create case (unique `case_id`, required `as_of_date`, required `evidence_cutoff_timestamp`); accept PDF/XLSX/CSV/JSON/HTML-XBRL; store originals unchanged; SHA-256; `DocumentRecord` with all metadata incl. `observed_date?, published_at/available_at, retrieved_at`; never overwrite; corrected upload creates new record + `supersedes`.
  - Sanitize filenames; restrict file types; prevent path traversal; emit `case_created`/`document_ingested`/`document_superseded`.
  - _Requirements: 1.1–1.8, 1.10, 25.1, 25.3_

- [ ] 2.2 Implement temporal-leakage controls across all data types
  - Availability-by-cutoff eligibility (based on `available_at`, not `retrieved_at`) for filings, news, peer filings, market/macro/fuel data, rating actions, external benchmarks, web content; reject future-dated items in historical runs.
  - _Requirements: 1.9, 20.1, 20.2, 20.3, 20.4_

- [ ] 2.3 Implement entity resolution and mismatch detection
  - Map aliases/tickers/reporting names to `entity_id`; tag facts with `entity_id` + `consolidation_scope`; detect scope mismatch; escalate ambiguous borrower/parent/subsidiary identity; never silently combine cross-scope facts; emit `entity_registered`/`entity_mismatch_detected`.
  - _Requirements: 2.2, 2.3, 2.4, 2.6_

- [ ] 2.4 Implement source-authority metadata and required-source profile
  - Store source authority per document; load case/industry source-criticality profile (`critical`/`important`/`optional`); missing critical → mandatory escalation or explicit inability-to-conclude; missing optional → reduced coverage; determination deterministic (not LLM confidence).
  - _Requirements: 6.3, 22.1, 22.2, 22.3, 22.4_

- [ ] 2.5 Tests: ingestion, leakage, entity, completeness
  - Identical bytes → identical hash; no source mutation; audit per file; leakage rejection (future filing/news/peer/market); seeded wrong-entity extraction triggers conflict/escalation; missing-critical-source escalation.
  - _Requirements: 1.7, 2.6, 20.4, 22.2, 23.1_

## Milestone 3 — Deterministic Parsing (split, each independently testable)

> **Definition of Done for every parser (3.1–3.5):** writes `ExtractedFact`/`CanonicalFact`-compatible objects; preserves entity/period/unit/scale/currency metadata; produces source references; handles missing fields without inventing values; ships golden fixtures; passes expected-value tests; logs parser name/version metadata.

- [ ] 3.1 XBRL / structured-filing parser
  - Map taxonomy concepts to facts with `taxonomy_concept`, entity, period, scale; golden fixtures + expected outputs; error handling for malformed/absent concepts.
  - _Requirements: 3.1, 3.2, 4.1, 4.5, 23.2_

- [ ] 3.2 XLSX / CSV parser
  - Parse financial tables; capture `source_label`/row name, scale, period; golden fixtures; error handling for missing sheets/columns.
  - _Requirements: 3.1, 3.2, 4.1, 4.5, 23.2_

- [ ] 3.3 PDF table parser
  - Extract tabular facts with page/table source refs; golden fixtures; error handling for unparseable tables (no invented values).
  - _Requirements: 3.1, 3.2, 4.5, 23.2_

- [ ] 3.4 Free-form PDF text extraction
  - Extract fielded facts from narrative with page refs; golden fixtures; never invent missing values.
  - _Requirements: 3.1, 3.2, 23.2_

- [ ] 3.5 OCR fallback (optional / last resort)
  - Only when required by selected PoC evidence; same DoD; clearly flagged `extraction_method=ocr`.
  - _Requirements: 3.1_

- [ ] 3.6 Unit/scale and date/period normalization
  - Deterministic, tested conversions; preserve raw vs normalized; preserve reporting period and freshness.
  - _Requirements: 4.4, 4.5, 5.2, 23.1_

## Milestone 4 — Reconciliation + CanonicalEvidenceSnapshot

- [x] 4.1 Implement definition-compatibility checks
  - Before comparison, check entity/period/unit-scale/currency/accounting-definition/restatement/source-authority; structured value wins ONLY if all pass; otherwise preserve both and mark `definition_mismatch`; no global ranking erases disagreement.
  - _Requirements: 3.7, 6.1, 6.2, 6.3_

- [x] 4.2 Implement zero-safe reconciliation + conflict detection
  - Always compute absolute delta; relative delta only above versioned near-zero floor; record `comparison_method` (`relative|absolute|exact|definition_mismatch|not_comparable`); apply versioned field tolerances; resolve to `verified|conflicting|unverified|missing`; dedupe exact duplicates; keep conflicts visible with both values/refs; contradiction records for non-numeric disagreement.
  - _Requirements: 3.8, 3.9, 7.1–7.10_

- [x] 4.3 Implement human correction workflow (non-destructive)
  - Resolution creates a new event (does not delete conflict); before/after states visible; emit `fact_human_corrected`.
  - _Requirements: 7.11, 7.12, 15.2, 15.3_

- [x] 4.4 Assemble and version the CanonicalEvidenceSnapshot
  - Documents, entities, facts, normalized financials, provenance, data-quality states; record `config_versions`; validate against JSON Schema; downstream references by version.
  - _Requirements: 5.1, 5.3, 5.5, 5.6_

- [ ] 4.5 Tests: reconciliation matrix + snapshot
  - matching, conflicting, 0-vs-0, 0-vs-small, near-zero denominator, negatives, scale mismatch, adjusted-vs-GAAP, restated-vs-original, wrong-entity, wrong-period; missing ≠ zero; snapshot validates.
  - _Requirements: 7.2, 7.3, 7.6, 23.3_

## Milestone 5 — Deterministic Metrics / Trends / Benchmarks / Rules

- [x] 5.1 Implement the metric-definition registry
  - `metric_definition_id` + version stating components included/excluded; definition change → audit event + regression trigger, no rewrite of historical outputs; historical runs linked to definition version.
  - _Requirements: 8.2, 8.3, 8.8_

- [x] 5.2 Implement the deterministic metric engine
  - Configured metric set; store definition/formula/engine versions, inputs, input fact IDs, result, period, units; zero/near-zero/missing/misleading denominator → `not_meaningful|missing_input|requires_review`; LLM never overrides.
  - _Requirements: 8.1, 8.2, 8.4, 8.7_

- [x] 5.3 Implement the historical trend layer
  - current/previous/2Y/3Y change, slope/direction, volatility; configured metric-specific adverse direction; no implied continuity across missing years; surface structural breaks; deterministic.
  - _Requirements: 9.1–9.5_

- [x] 5.4 Implement the small-cohort-aware peer benchmark layer
  - Report raw peer values, rank, median, min/max, simple distribution; suppress/label unstable percentiles; P90/P95 only when sample size permits; store `benchmark_method`, `sample_size`, cohort definition/version, benchmark date; prevent borrower double-count; label synthetic values; reproducible; anomaly signal only.
  - _Requirements: 10.1–10.7_

- [x] 5.5 Implement the versioned policy/rule layer
  - Keep policy threshold / peer benchmark / historical deterioration separate; thresholds in versioned config labelled `ILLUSTRATIVE — NOT BANK POLICY`; rules versioned; change → audit; historical runs linked to rule version.
  - _Requirements: 11.1, 11.2, 11.3, 11.5, 11.6_

- [ ] 5.6 Implement the escalation engine (event-based)
  - Event-based (no opaque score); categories data integrity / financial rules / AI-deterministic conflict / evidence; escalation record with all fields + exact `rule_id`; cannot vanish unresolved; human resolution new event; case status reflects unresolved mandatory escalations; emit `rule_triggered`/`escalation_created`/`escalation_resolved`.
  - _Requirements: 11.4, 14.1–14.7_

- [ ] 5.7 Tests: metrics/trends/benchmarks/rules
  - 100% pass on golden metric examples; identical re-run under same versions; definition-version linkage; benchmark reproducibility + percentile suppression on small cohort; rule-trigger accuracy; escalation precision/recall on seeded exceptions; metric-to-escalation flow (`R-TREND-LEV-01`).
  - _Requirements: 8.5, 8.6, 23.1_

## Milestone 6 — LLM Extraction / Analysis / Challenge

- [ ] 6.1 Implement LLMClient + model-run registry
  - Single `extract/analyze/challenge` interface; structured JSON on fixed schema; temperature 0 where supported; validate before use; log prompt ID/version/hash, model ID/config, case version, evidence IDs, raw + parsed response, validation outcome; emit `llm_run`; no byte-exact claim.
  - _Requirements: 19.2, 19.3, 19.4_

- [ ] 6.2 Build the prompt registry
  - Versioned prompt artifacts (e.g., `business_analysis_v1.0`); prompt updates trigger regression tests.
  - _Requirements: 19.1, 19.5_

- [ ] 6.3 Implement qualitative extraction prompt + response schema
  - Extract management/ownership/structure/competitive position/industry risks/qualitative debt terms; return fact, value/statement, period, source ref, confidence/status, no unsupported inference; AI facts never auto-`verified`; validate against schema; attach source IDs.
  - _Requirements: 3.3, 3.4, 3.6_

- [ ] 6.4 Implement GenAI credit analysis (schema-validated)
  - Inputs limited to canonical facts/metrics/trends/benchmarks/needed snippets/data limitations/open conflicts/approved prompt; output JSON `business_overview, repayment_analysis, key_risks, mitigants, data_limitations, questions_for_human`; each claim carries evidence IDs, fact/interpretation flag, materiality, uncertainty; prompt forbids inventing/inferring; validate before use; missing critical evidence → caveat/escalation.
  - _Requirements: 12.1–12.7_

- [ ] 6.5 Implement challenge layer + claim-grounding split
  - Challenge tests support/metric-contradiction/omitted-risk/mitigant-relevance/missing-evidence/alternatives/overstated-certainty; output `claim_id,issue_type,severity,reason,evidence_refs`; non-destructive; log accept/reject; high severity → escalation.
  - Separate checks: (A) citation presence, (B) entailment; states `supported|partially_supported|unsupported|contradictory|not_verifiable`; citation ID alone never = grounded; deterministic where possible, optional LLM judge, manual adjudication for high-severity facts.
  - _Requirements: 13.1–13.8_

- [ ] 6.6 Tests: seeded conflicts + grounding
  - AI-vs-deterministic numerical mismatch, qualitative contradiction, wrong entity, wrong period, stale evidence, missing critical evidence → detected/escalated/never-merged; citation presence vs entailment vs unsupported vs contradiction measured separately.
  - _Requirements: 23.9, 23.10_

## Milestone 7 — Human Review + FinalCaseSnapshot

- [x] 7.1 Implement the human-in-the-loop review workflow
  - All reviewer actions; record reviewer/timestamp/prior/new/reason/linked evidence/comment; non-destructive, before/after visible; explicit sign-off for final recommendation + gated items; final output identifies unresolved exceptions; emit `human_review`.
  - _Requirements: 15.1–15.6_

- [x] 7.2 Implement FinalCaseSnapshot finalization + immutability
  - Assemble `FinalCaseSnapshot` referencing a `CanonicalEvidenceSnapshot` version + metrics/benchmarks/analysis/risks/mitigants/escalations/human reviews/recommendation/audit metadata + config/metric-def/rule/prompt-model versions; once finalized immutable; later change creates new version referencing predecessor; outputs linked to exact snapshot; emit `snapshot_finalized`.
  - _Requirements: 16.1, 16.2, 16.3, 16.4_

- [x] 7.3 Tests: review + snapshot immutability
  - Non-destructive edits with recoverable before/after; sign-off gates finalization; finalized snapshot immutable; new version references predecessor.
  - _Requirements: 16.2, 16.3, 23.1_

## Milestone 8 — Outputs

- [x] 8.1 Generate the FinalCaseSnapshot JSON (first)
  - Source refs, facts, metrics, analysis, escalations, human reviews, final status, version metadata; validate against JSON Schema; emit `memo_generated`/`case_finalized`.
  - _Requirements: 17.1_

- [x] 8.2 Render the PDF memo from FinalCaseSnapshot (deterministic)
  - Jinja2 templates; configured sections; every number matches JSON; every major claim maps to evidence/analysis; no content absent from JSON; show as-of/cutoff/unresolved exceptions; stable re-render; linked to snapshot version.
  - _Requirements: 17.2–17.7_

- [x] 8.3 Tests: JSON↔PDF parity
  - Numerical parity; claim-to-evidence mapping; re-render equivalence; output linked to exact snapshot.
  - _Requirements: 17.4, 17.5, 17.7_

## Milestone 9 — Evaluation Harness

- [ ] 9.1 Implement the GroundTruthManifest object + loader
  - Fields per Req 23.4; separate `contemporaneous_ground_truth` from `future_outcome`; no scored metric without a declared manifest.
  - _Requirements: 23.4, 23.5, 23.6_

- [ ] 9.2 Build the golden extraction dataset + scorer
  - Per field: value, entity, period, currency, scale, source location, allowed tolerance, expected status; measure field/numeric/citation accuracy + required-field recall by field type.
  - _Requirements: 23.2_

- [x] 9.3 Implement temporal backtesting + leakage tests
  - Expanding and (if learned model) rolling windows; never mix future/past; same evidence rerunnable under original vs latest config (two visibly distinguished modes); leakage tests reject future filing/news/peer/market-macro.
  - _Requirements: 19.7, 20.4, 23.7_

- [x] 9.4 Implement missing-data ablation harness
  - FULL + NO_XLSX/NO_XBRL/PDF_ONLY/NO_INDUSTRY_DATA/NO_FUEL_DATA/NO_DEBT_MATURITY_TABLE/NO_INTERIM_STATEMENTS; measure completeness/accuracy, metric availability, risk recall, unsupported-claim rate, correct-escalation rate, human-review rate, `performance_drop`; graceful degradation only; run ≥4.
  - _Requirements: 23.8, 23.14_

- [ ] 9.5 Implement prompt/model A/B testing (blinded)
  - Identical cases, blinded reviewer; compare material-risk precision/recall, omissions, unsupported claims, groundedness, corrections, review time; not "which sounds better"; run ≥1.
  - _Requirements: 23.11, 23.14_

- [ ] 9.6 Implement contemporaneous-vs-future evaluation separation
  - Score factual accuracy only against `contemporaneous_ground_truth`; study predictive usefulness from `future_outcome` without leaking into inputs at T or into contemporaneous scoring.
  - _Requirements: 23.6_

- [ ] 9.7 Implement lower-level ML statistical tests (only if ML used)
  - Simple-baseline comparison, precision/recall/F1, AUROC where meaningful, calibration, paired bootstrap CIs, McNemar where appropriate, effect size + sample size; no ML for appearance; LLM not retrained by PoC.
  - _Requirements: 23.12_

- [x] 9.8 Implement end-to-end reproducibility test
  - Same source snapshot + config/metric-def/rule/prompt/model versions reproduce source set, deterministic facts/metrics, rule triggers, schema structure, `FinalCaseSnapshot` linkage; store original LLM outputs.
  - _Requirements: 23.13_

- [ ] 9.9 Produce the evaluation summary report
  - Export extraction/deterministic/generative/system/missing-data robustness metrics into `evals/reports`; acceptance: ≥1 temporal backtest, ≥1 rolling-vs-expanding (if learned model), ≥4 ablations, ≥1 A/B.
  - _Requirements: 23.14_

## Milestone 10 — Case Workbench UI (optional per MVP cut)

- [ ] 10.1 Build the frozen case workbench
  - Show as-of/cutoff, navigable sections, and counts (open exceptions, critical missing sources, AI/deterministic conflicts, awaiting-human decisions); historical versions frozen; later review creates a new snapshot referencing its predecessor.
  - _Requirements: 24.1–24.4_

---

## Traceability Pass

**Requirements → design → task coverage** (every requirement maps to ≥1 design element and ≥1 task):

| Req | Design element | Task(s) |
|---|---|---|
| 1 Ingestion/versioning | ingestion service, DocumentRecord | 2.1, 2.5 |
| 2 Entity resolution | entity service, EntityRecord | 1.3, 2.3, 2.5 |
| 3 Parsing/extraction | parsers, LLMClient | 3.1–3.6, 6.3 |
| 4 Financial fact schema | ExtractedFact/CanonicalFact | 1.4, 3.6 |
| 5 CanonicalEvidenceSnapshot | snapshot A schema | 1.5, 4.4 |
| 6 Structured precedence/compat | compatibility checks | 4.1 |
| 7 Zero-safe reconciliation | reconciliation (zero-safe) | 4.2, 4.3, 4.5 |
| 8 Metric engine + defs | metric-definition registry | 5.1, 5.2, 5.7 |
| 9 Trends | trend layer | 5.3 |
| 10 Peer benchmark | small-cohort benchmarking | 5.4 |
| 11 Policy/rules | policy/rule layer | 5.5 |
| 12 Generative analysis | analysis service | 6.4 |
| 13 Challenge + grounding | challenge + ClaimGrounding | 6.5, 6.6 |
| 14 Escalation | escalation engine | 5.6 |
| 15 Human review | human-review workflow | 4.3, 7.1 |
| 16 FinalCaseSnapshot immutability | snapshot B schema | 7.2, 7.3 |
| 17 Output JSON+PDF | reporting service | 8.1–8.3 |
| 18 Audit trail | append-only audit | 1.2 |
| 19 Prompt/model/config registries | registries | 1.1, 6.1, 6.2, 9.3 |
| 20 Temporal leakage all types | cutoff controls | 2.2, 9.3 |
| 21 No silent defaults | config/default surfacing | 1.1, 2.4 |
| 22 Source-package completeness | source profile | 2.4, 2.5 |
| 23 Testing/eval harness | evaluation + test strategy | 1.6, 2.5, 3.*, 4.5, 5.7, 6.6, 7.3, 8.3, 9.1–9.9 |
| 24 Workbench UI | workbench | 10.1 |
| 25 Security | security helpers | 0.1, 2.1 |

**Critical design rules → acceptance test:** rules 1–23 are each exercised by at least one test task — e.g. rule 4 (entity scope) → 2.5; rule 8 (compat precedence) → 4.5; rule 9 (zero-safe) → 4.5; rule 14 (citation vs entailment) → 6.6; rule 16 (snapshot immutability) → 7.3; rule 17 (leakage) → 2.5/9.3; rule 19 (ground truth) → 9.1; rule 20 (contemporaneous vs future) → 9.6; rule 21 (no silent defaults) → 1.6/2.4.

**Deferred / non-MVP:** OCR (3.5) optional unless required by evidence; lower-level ML statistical tests (9.7) only if a learned model is introduced; workbench UI (Milestone 10) optional per the MVP cut.

## Hardening acceptance evidence

The checked tasks above are substantiated by the passing deterministic suite and
`tests/integration/test_pipeline_acceptance.py`,
`tests/integration/test_hardening_boundaries.py`,
`tests/ablation/test_pipeline_ablation.py`, and
`tests/leakage/test_pipeline_replay.py`. Binary PDF production remains conditional
on native dependencies; HTML/JSON parity is exercised. Fake AI stages are wired;
real-provider integration and real-filing parser validation remain separate work.
See `HARDENING_REPORT.md` and `REAL_DATA_READINESS.md`.
