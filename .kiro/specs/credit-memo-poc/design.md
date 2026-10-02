# Design — AI-Assisted Credit Memo Generator (PoC)

## Overview

The system is a staged, evidence-processing pipeline that turns a mixed source package into a **`FinalCaseSnapshot`** JSON and a PDF credit memo rendered from that snapshot. The architecture enforces a strict separation of responsibilities: **deterministic components anchor numerical reliability**, **AI accelerates extraction and interpretation under tight constraints**, **rule-based engines make escalation decisions explainable**, and **humans resolve material judgment calls** — all over an **append-only audit substrate** with **versioned prompts, models, and configuration**.

Design priorities, in order: traceability, immutability, graceful degradation under missing evidence, reproducibility, and only then convenience.

### Requirements vs Design vs Configuration

This document records **current implementation choices**; `requirements.md` records behavior-level guarantees; configuration holds replaceable values. The split (restated from requirements) is:

- **Design choices (here):** Python 3.11+, FastAPI, SQLite→PostgreSQL, SQLAlchemy, pandas/Polars, Jinja2, local file store, the airline PoC sector, Delta as borrower, and United/American/Southwest as the initial comparison set.
- **Configuration (versioned & hashed, Req 19.6):** reconciliation tolerances & near-zero floor, illustrative policy thresholds, peer cohort membership, metric definitions (components in/out), adverse-direction definitions, escalation proximity thresholds, parser precedence, and source-criticality profiles.

These may change without altering the normative requirements.

## Architecture

### Pipeline (data flow)

```text
SOURCE DOCUMENTS
      │
      ▼
INGESTION + VERSIONING + ENTITY RESOLUTION + CUTOFF ──┐
      │                                               │
      ▼                                               │
DETERMINISTIC PARSERS (XBRL→XLSX/CSV→PDF tbl→text→OCR)│  cross-cutting:
AI QUALITATIVE EXTRACTION                              │  - append-only audit log
      │                                               │  - data provenance
      ▼                                               │  - prompt/model registry
RECONCILIATION (zero-safe) + DEFINITION COMPAT ────────┤  - configuration registry
      ├─ conflict / mismatch → escalation             │    (tolerances, policy,
      └─ verified                                      │     peers, metric defs,
      ▼                                               │     rules) — versioned+hashed
CANONICAL EVIDENCE SNAPSHOT (JSON, versioned) ─────────┤  - schema versions
      │                                               │  - entity/scope registry
      ├────────────┬───────────┐                      │
      ▼            ▼           ▼                      │
  METRICS     BENCHMARKS    GENAI                      │
  (det.,      (small-       ANALYSIS                   │
  versioned   cohort-                                  │
  defs)       aware)                                   │
      └────────────┴───────────┘                      │
                   │                                   │
                   ▼                                   │
       CHALLENGE + CLAIM GROUNDING (cite vs entail)    │
                   ▼                                   │
            ESCALATION ENGINE ─────────────────────────┤
                   │ (human if needed)                 │
                   ▼                                   │
  HUMAN REVIEW → FINAL CASE SNAPSHOT (immutable) ──────┘
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
        PDF      API/UI    EVALS (GroundTruthManifest)
```

### Layering

- **API layer** (`app/api`): FastAPI routes for cases, documents, entities, extraction, metrics, analysis, escalations, reviews, outputs.
- **Core** (`app/core`): config loading & versioning, hashing, time/cutoff enforcement, IDs, logging, security helpers.
- **Models** (`app/models`): SQLAlchemy ORM entities + persistence.
- **Schemas** (`app/schemas`): Pydantic models + JSON Schema for `CanonicalEvidenceSnapshot`, `FinalCaseSnapshot`, and all object schemas.
- **Services** (`app/services`): one subpackage per stage — `ingestion`, `entity`, `extraction`, `reconciliation`, `metrics`, `benchmarking`, `analysis`, `escalation`, `reporting`, `audit`, `evaluation`.
- **Prompts** (`app/prompts`): versioned prompt templates + registry.
- **Config** (`app/config` + `config/`): versioned/hashed tolerance, policy, peer, metric-definition, trend, and escalation config.

### Repository structure (target)

```text
credit-memo-poc/
├── README.md
├── pyproject.toml
├── .env.example
├── app/
│   ├── api/   core/   models/   schemas/   prompts/   config/
│   ├── services/
│   │   ├── ingestion/   entity/        extraction/
│   │   ├── reconciliation/  metrics/   benchmarking/
│   │   ├── analysis/   escalation/     reporting/
│   │   ├── audit/      evaluation/
├── config/    (tolerances/ policy/ peers/ metric_defs/ rules/ source_profiles/ — versioned)
├── data/      (raw/ parsed/ normalized/ derived/ snapshots/)
├── tests/     (unit/ integration/ golden/ ablation/ prompts/ end_to_end/ leakage/)
├── evals/     (cases/ manifests/ annotations/ results/ reports/)
└── output/    (json/ pdf/)
```

## Technology Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Strong typing + ecosystem (design choice) |
| API | FastAPI | Async, Pydantic-native, OpenAPI (design choice) |
| Validation | Pydantic + JSON Schema | Runtime validation + schema versioning |
| Data | pandas or Polars | Tabular parsing & metric computation (design choice) |
| DB | SQLite (PoC) → PostgreSQL (multi-user) | Simple first; migratable (design choice) |
| ORM | SQLAlchemy | Portable across SQLite/PostgreSQL |
| Templating | Jinja2 + HTML→PDF | Deterministic memo rendering from snapshot |
| Testing | pytest | unit/integration/golden/ablation/leakage/e2e |
| LLM | provider-abstracted `LLMClient` | No provider lock-in; centralized logging |

## Components and Interfaces

### Two distinct canonical objects (renamed)

To remove the earlier ambiguity between the mid-pipeline evidence object and the final case object, the design uses two named snapshots:

**A. `CanonicalEvidenceSnapshot`** (mid-pipeline, versioned) — documents, entities, facts, normalized financials, provenance, data-quality states.

```json
{
  "schema_version": "1.0",
  "snapshot_type": "canonical_evidence",
  "snapshot_version": 1,
  "case_id": "DAL_2024",
  "as_of_date": "2024-12-31",
  "evidence_cutoff_timestamp": "2025-02-15T23:59:59Z",
  "config_versions": {},
  "documents": [], "entities": [], "facts": [],
  "financials": {}, "provenance": {}, "data_quality": {}
}
```

**B. `FinalCaseSnapshot`** (post-analysis/escalation/human-review, immutable once finalized) — references a `CanonicalEvidenceSnapshot` version plus derived content. **The PDF renders from this object.**

```json
{
  "schema_version": "1.0",
  "snapshot_type": "final_case",
  "snapshot_version": 1,
  "supersedes_snapshot": null,
  "evidence_snapshot_ref": {"case_id": "DAL_2024", "snapshot_version": 1},
  "config_versions": {}, "metric_definition_versions": {},
  "rule_versions": {}, "prompt_model_versions": {},
  "metrics": {}, "benchmarks": {},
  "business_analysis": {}, "financial_analysis": {},
  "risks": [], "mitigants": [], "exceptions": [],
  "escalations": [], "human_reviews": [],
  "recommendation": {"status": "draft"},
  "audit_metadata": {}, "finalized": false
}
```

### Core data objects

**EntityRecord** (new): `entity_id, legal_name, aliases[], tickers[], entity_type (parent|operating_subsidiary|borrower|guarantor|...), parent_entity_id, borrower_flag, guarantor_flag, jurisdiction?, source_refs[]`.

**DocumentRecord**: `document_id, case_id, filename, document_type, issuer, period, observed_date?, published_at/available_at, retrieved_at, ingested_at, sha256, storage_path, source_authority, version, supersedes`.

**SourceRef**: `document_id, page?, table?, taxonomy_concept?, row_label?, cell?`.

**ExtractedFact / CanonicalFact** (enriched): `fact_id, name, raw_value, raw_unit, normalized_value, normalized_unit, currency, scale, period_start, period_end, period_type, fiscal_year, accounting_basis, consolidation_scope, entity_id, reporting_entity_name?, restated, taxonomy_concept?, source_label?, data_freshness, normalization_method, definition_version?, status, extraction_method, confidence, source_refs[], created_by`. Allowed `status`: `verified | unverified | conflicting | missing | not_applicable | not_disclosed | stale`. **`missing` is never numerical zero.** Qualitative facts need not populate every financial field, but the schema supports them.

**Metric result** (versioned definitions): `metric_definition_id, metric_definition_version, formula_id, inputs, input_fact_ids[], result | explicit_state, period, units, engine_version`. Explicit states: `not_meaningful | missing_input | requires_review`. The definition states which components are included/excluded.

**Reconciliation result** (zero-safe): `field, values[], source_refs[], absolute_delta, relative_delta?, near_zero_floor, comparison_method (relative|absolute|exact|definition_mismatch|not_comparable), tolerance_version, resolved_state`.

**Benchmark result** (small-cohort-aware): `metric, cohort_definition, cohort_version, benchmark_date, sample_size, benchmark_method, raw_peer_values[], rank, median, min, max, p25?, p75?, p90?, p95?, percentiles_reliable (bool), synthetic (bool)`.

**ClaimGrounding** (new): `claim_id, citation_present (bool), entailment_state (supported|partially_supported|unsupported|contradictory|not_verifiable), evidence_refs[], judge (deterministic|llm|human), adjudicated (bool)`.

**Escalation**: `escalation_id, case_id, severity, rule_id, reason, triggered_at, evidence_refs[], status, resolution`.

**Audit event** (append-only): `event_id, case_id, event_type, timestamp, actor_type, actor_id, before, after, reason, linked_objects[]`.

**GroundTruthManifest** (new, evaluation): `case_id, as_of_date, evidence_cutoff_timestamp, manifest_version, approved_document_ids[], verified_facts[], expected_normalized_values{}, expected_metric_outputs{}, known_missing_items[], known_contradictions[], expected_rule_triggers[], expected_escalations[], adjudicated_material_risks[]?, contemporaneous_ground_truth{}, future_outcome{}?, annotator_metadata{}`. `contemporaneous_ground_truth` and `future_outcome` are stored in separate sub-objects and never co-mingled in scoring.

### LLMClient abstraction

```python
class LLMClient:
    def extract(self, request): ...
    def analyze(self, request): ...
    def challenge(self, request): ...
```

All provider-specific calls live behind this interface. Every call requests structured JSON against a fixed schema, uses temperature 0 where supported, and logs model ID, prompt ID/version/hash, input evidence IDs, raw + parsed response, and validation outcome to the model-run registry. Temperature 0 improves consistency but is **not** treated as sufficient for reproducibility — the raw run is always stored. The system never claims byte-exact regeneration.

### Entity resolution (new)

The `entity` service registers `EntityRecord`s, maps aliases/tickers/reporting names to `entity_id`, and tags every financial fact with `entity_id` + `consolidation_scope`. It detects mismatches (fact scope ≠ expected scope, borrower vs ultimate parent, consolidated vs standalone) and raises conflict/escalation instead of merging. Legal borrower and consolidated parent are preserved as distinct entities.

### Reconciliation (zero-safe) (revised)

```python
absolute_delta = abs(a - b)
if abs(denominator) > near_zero_floor:        # near_zero_floor is versioned config
    relative_delta = absolute_delta / abs(denominator)
    comparison_method = "relative"
else:
    relative_delta = None
    comparison_method = "absolute"            # or "exact" / "definition_mismatch" / "not_comparable"
```

Before any numeric comparison, the service runs **compatibility checks** (entity, period, unit/scale, currency, accounting definition, restatement, source authority). If any check fails, the structured value does **not** automatically win; both observations are preserved and a conflict/`definition_mismatch` is recorded for reconciliation. Handles 0-vs-0, 0-vs-small, near-zero denominator, negatives, and scale mismatch explicitly.

### Service responsibilities

| Service | Responsibility | Key guarantees |
|---|---|---|
| `ingestion` | Create cases; store originals; hash; version; enforce cutoff across all data types | Immutable originals; SHA-256; `supersedes`; availability-by-cutoff |
| `entity` | Register entities; tag facts with scope; detect mismatches | Distinct borrower/parent; escalate ambiguity; no silent cross-scope merge |
| `extraction` | Deterministic parsers (split) + AI qualitative extraction | Compatibility-gated precedence; every fact has source ref; AI never auto-`verified` |
| `reconciliation` | Zero-safe comparison; definition compat; set fact state | Conflicts preserved; `comparison_method` recorded; human resolution additive |
| `metrics` | Deterministic metric library + definition registry | Inputs + formula + definition version + fact IDs; explicit bad-denominator states |
| `benchmarking` | Small-cohort-aware peer stats | `benchmark_method`+`sample_size`; unstable percentiles suppressed/labelled; anomaly signal only |
| (policy rules) | Versioned, config-driven rule evaluation | Rule IDs; three concepts separated; thresholds in versioned config |
| `analysis` | GenAI interpretation of canonical evidence | Schema-validated; claims carry evidence IDs + fact/interpretation + uncertainty |
| (challenge + grounding) | Second-pass critic + citation/entailment split | Non-destructive; `supported..not_verifiable` states; high severity → escalation |
| `escalation` | Event-based routing to humans | Every escalation has reason + rule; cannot vanish unresolved |
| (human review) | Non-destructive review workflow | Before/after preserved; sign-off gates finalization |
| `reporting` | Final snapshot JSON first, then PDF | PDF renders from `FinalCaseSnapshot`; JSON↔PDF parity; as-of/cutoff/exceptions shown |
| `audit` | Append-only event log | Events never modified |
| `evaluation` | GroundTruthManifest-based scoring harness | No scored metric without declared truth; contemporaneous vs future kept separate |

### Configuration & versioning (new, cross-cutting)

A **configuration registry** versions and hashes: reconciliation tolerances & near-zero floor, policy thresholds, peer definitions, metric definitions, adverse-trend rules, escalation rules, parser precedence, and source-criticality profiles. Each snapshot records the exact `config_versions` used. Historical re-runs support two visibly distinguished modes: **(A) reproduce historical run** (original config versions) and **(B) re-evaluate historical evidence** (latest config versions). Example illustrative policy config:

```yaml
# ILLUSTRATIVE — NOT BANK POLICY   (config version is recorded per snapshot)
net_debt_to_ebitda:
  policy_limit: 3.5
  peer_review_percentile: 90
  historical_change_review: 0.50
```

### No silent defaults (new)

If a field, threshold, peer set, metric definition, or assumption is absent, the system does not substitute a value unless that default is explicitly versioned/configured; when a configured default is applied, its source and version are surfaced, and material finance assumptions can require human approval.

## Data Models

- **Relational schema (SQLAlchemy):** `cases`, `entities`, `documents`, `facts`, `fact_source_refs`, `reconciliations`, `metrics`, `metric_definitions`, `benchmarks`, `rules`/`rule_versions`, `config_versions`, `escalations`, `human_reviews`, `claim_groundings`, `prompt_versions`, `model_runs`, `snapshots` (evidence + final, with `supersedes`), `audit_events`, `ground_truth_manifests`.
- **Canonical JSON Schemas:** versioned `CanonicalEvidenceSnapshot` and `FinalCaseSnapshot`, validated on output; schema versions tracked alongside snapshots.
- **File store layout:** `data/raw` (originals, never mutated), `data/parsed`, `data/normalized`, `data/derived`, `data/snapshots` (frozen case versions). Outputs in `output/json` and `output/pdf`, each linked to a `FinalCaseSnapshot` version.

### Metric engine (examples)

```python
net_debt = total_debt - unrestricted_cash          # components fixed by metric_definition_version
net_debt_to_ebitda = net_debt / ebitda             # guarded: zero/near-zero/missing/negative → explicit state
```

Metric-to-escalation example: ND/EBITDA trending `1.73x → 2.17x → 3.00x` against illustrative policy 3.50 and peer P90 3.10 → no policy breach, elevated peer anomaly, >50% 2Y deterioration → `R-TREND-LEV-01` fires analyst review. The trigger is deterministic and auditable; the narrative only explains it.

## Error Handling

- **Missing inputs:** never coerced to zero; propagate `missing`/`missing_input`; downstream caveat or escalation.
- **Zero/near-zero denominators:** reconciliation switches to absolute/exact comparison; metrics return `not_meaningful | missing_input | requires_review`.
- **Entity/scope mismatch:** facts from different scopes are never merged; mismatch raises conflict/escalation.
- **Definition mismatch:** structured value does not auto-win; both preserved; `definition_mismatch` recorded.
- **Conflicts:** both values + both refs stored; no silent merge; human resolution is a new event.
- **Schema violations:** LLM output failing validation is rejected before use and logged.
- **Temporal leakage:** items failing availability-by-cutoff are rejected across all data types.
- **No silent defaults:** absent settings are surfaced, not quietly filled.
- **Fail-safe principle:** incomplete information lowers completeness/confidence and triggers caveats/escalation — never invented values.

## Multi-Agent Boundaries

Only three distinct, independently testable roles (plus optional news): **Extraction** (qualitative facts from unstructured sources), **Analysis** (credit interpretation from canonical evidence), **Challenge/Grounding** (unsupported claims / omissions / contradictions / missing info / entailment). No swarm of agents independently writing memos and voting. Exact agent count is a design/config choice, not a requirement.

## Security & Data Hygiene

Public/synthetic data only (unless authorized); secrets via env vars, never hardcoded/logged; sanitized filenames; restricted file types; JSON validation; path-traversal prevention; recorded provider calls; no external-model data egress unless permitted.

## Evaluation Design (GroundTruthManifest-based)

- **Ground truth:** every scored case has a `GroundTruthManifest`; no accuracy/recall metric is reported without one.
- **Contemporaneous vs future:** `contemporaneous_ground_truth` (knowable at T) scores factual accuracy; `future_outcome` (default, downgrade, liquidity shock, covenant breach) may study predictive usefulness but never leaks into inputs at T and is never mixed into contemporaneous scoring.
- **Temporal leakage:** eligibility is by `available_at` ≤ cutoff for filings, news, peer filings, market/macro/fuel data, rating actions, external benchmarks, and web content. Leakage tests deliberately inject future items and assert rejection.
- **Windows:** expanding and (where a learned model exists) rolling windows; same historical evidence rerunnable under original or latest config.
- **ML (only if used):** simple-baseline comparison, precision/recall/F1, AUROC where meaningful, calibration, paired bootstrap CIs, McNemar where appropriate, effect size + sample size. The foundation LLM is not assumed retrained by this PoC.

### Evaluation metrics (evals/)

Extraction (field recall, numeric accuracy, citation accuracy, unit/scale/period error); deterministic (formula accuracy, rule-trigger accuracy, benchmark reproducibility); generative (groundedness, citation-presence vs entailment, unsupported-claim count, material-risk precision/recall, omission/contradiction counts); system (escalation precision/recall on seeded exceptions, human review rate, unresolved exceptions, reproducibility, processing time); missing-data robustness (performance drop by removed source, hallucination/unsupported-fill rate, correct escalation rate under missing critical evidence).

## Testing Strategy

Component- and system-level, mapped to `tests/` subdirs:

- **unit** — formulas (exact ± rounding), unit/scale conversion, date/period normalization, zero-safe reconciliation, entity/scope compatibility, tolerance logic, policy/trend/escalation rules, versioning, hashing, snapshot immutability.
- **golden** — manually verified extraction dataset with value/entity/period/currency/scale/source-location/tolerance/expected-status; measure field/numeric/citation accuracy and required-field recall by field type.
- **integration** — reconciliation matrix (matching, conflicting, zero, near-zero, adjusted-vs-GAAP, restated-vs-original, wrong-entity, wrong-period); AI-vs-deterministic seeded conflicts detected/escalated/never-merged.
- **leakage** — future filing / news / peer result / market-macro value all rejected.
- **ablation** — FULL + 7 degraded variants; measure completeness/accuracy, metric availability, risk recall, unsupported-claim rate, correct-escalation rate, human-review rate, `performance_drop`; graceful degradation only.
- **prompts** — prompt/model A/B with blinded reviewer; material-risk precision/recall, omissions, unsupported claims, groundedness, corrections, review time; not "which sounds better."
- **end_to_end** — reproducibility: same source snapshot + config/metric-def/rule/prompt/model versions reproduce source set, deterministic facts/metrics, rule triggers, schema structure, and `FinalCaseSnapshot` linkage; store raw LLM runs.
- **Statistical** (only if ML used) — baseline comparison, precision/recall/F1/AUROC/calibration, paired bootstrap CIs, McNemar, effect size + sample size.

## Build Order (milestones → maps to tasks)

0. **Scaffold.**
1. **Core evidence model** — case, DocumentRecord, EntityRecord, SourceRef, ExtractedFact, CanonicalField, append-only audit, hashing/versioning, config registry, schema versions. **(Data model before parsers.)**
2. **Ingestion + entity/period controls** — ingestion, cutoff across all data types, entity resolution, source authority, source-package completeness.
3. **Deterministic parsing** — XBRL, XLSX/CSV, PDF tables, optional text/OCR, normalization. **(Each parser independently testable.)**
4. **Reconciliation + CanonicalEvidenceSnapshot** — zero-safe delta/tolerance, definition compatibility, conflicts, missing states, human correction.
5. **Deterministic metrics / trends / benchmarks / rules** — metric-definition registry, formulas, trends, small-cohort peers, policy/config, escalation triggers.
6. **LLM extraction / analysis / challenge** — LLMClient, prompt registry, qualitative extraction, analysis, challenge, claim support/entailment.
7. **Human review + FinalCaseSnapshot** — resolution, sign-off, snapshot finalization, immutable case versions.
8. **Outputs** — JSON, PDF from `FinalCaseSnapshot`, parity/evidence tests.
9. **Evaluation harness** — GroundTruthManifest, golden facts, temporal + leakage tests, missing-data ablations, seeded conflicts, A/B, statistical tests, contemporaneous-vs-future.
10. **Optional workbench UI.**

**MVP cut if time is tight:** keep versioned evidence store, provenance, entity scope, zero-safe reconciliation, canonical evidence + final snapshots, deterministic metrics with versioned definitions, one small-cohort peer benchmark, one generative analysis step, one challenge+grounding step, escalation rules, human review log, PDF rendering, GroundTruthManifest + missing-data/leakage tests. Defer learned thresholds, elaborate orchestration, real-time monitoring, news streaming, retraining, PD models, CDS ingestion, large-scale cloud deploy. The differentiator is **control architecture**, not model count.
