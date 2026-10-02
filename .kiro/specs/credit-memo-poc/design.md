# Design — AI-Assisted Credit Memo Generator (PoC)

## Overview

The system is a staged, evidence-processing pipeline that turns a mixed source package into a canonical JSON case record and a PDF credit memo rendered from that JSON. The architecture enforces a strict separation of responsibilities: **deterministic components anchor numerical reliability**, **AI accelerates extraction and interpretation under tight constraints**, **rule-based engines make escalation decisions explainable**, and **humans resolve material judgment calls** — all over an **append-only audit substrate**.

Design priorities, in order: traceability, immutability, graceful degradation under missing evidence, reproducibility, and only then convenience. The PoC targets U.S. airlines (Delta borrower; United/American/Southwest peers) and runs locally with SQLite + a structured file store.

## Architecture

### Pipeline (data flow)

```text
SOURCE DOCUMENTS
      │
      ▼
INGESTION + VERSIONING ───────────────┐
      │                               │
      ▼                               │
EXTRACTION                            │
  ├─ deterministic parsers            │
  └─ AI extraction                    │   cross-cutting:
      │                               │   - audit log (append-only)
      ▼                               │   - data provenance
RECONCILIATION + DATA QUALITY         │   - model/prompt registry
      ├─ conflict → escalation        │   - version history
      └─ verified                     │   - schema versions
      ▼                               │
CANONICAL CREDIT DATASET (JSON) ──────┤
      │                               │
      ├────────────┬───────────┐      │
      ▼            ▼           ▼      │
  METRICS     BENCHMARKS    GENAI     │
  (det.)      + RULES       ANALYSIS  │
      └────────────┴───────────┘      │
                   │                  │
                   ▼                  │
            CHALLENGE LAYER           │
                   ▼                  │
            ESCALATION ENGINE ────────┤
                   │ (human if needed)│
                   ▼                  │
           CANONICAL CASE JSON ───────┘
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
        PDF      API/UI    EVALS
```

### Layering

- **API layer** (`app/api`): FastAPI routes for cases, documents, extraction, metrics, analysis, escalations, reviews, outputs.
- **Core** (`app/core`): config loading, hashing, time/cutoff enforcement, IDs, logging, security helpers.
- **Models** (`app/models`): SQLAlchemy ORM entities + persistence.
- **Schemas** (`app/schemas`): Pydantic models + JSON Schema for the canonical case record and all object schemas.
- **Services** (`app/services`): one subpackage per stage — `ingestion`, `extraction`, `reconciliation`, `metrics`, `benchmarking`, `analysis`, `escalation`, `reporting`, `audit`.
- **Prompts** (`app/prompts`): versioned prompt templates + registry.

### Repository structure (target)

```text
credit-memo-poc/
├── README.md
├── pyproject.toml
├── .env.example
├── app/
│   ├── api/
│   ├── core/
│   ├── models/
│   ├── schemas/
│   ├── services/
│   │   ├── ingestion/   extraction/   reconciliation/
│   │   ├── metrics/     benchmarking/ analysis/
│   │   ├── escalation/  reporting/    audit/
│   └── prompts/
├── data/      (raw/ parsed/ normalized/ derived/ snapshots/)
├── tests/     (unit/ integration/ golden/ ablation/ prompts/ end_to_end/)
├── evals/     (cases/ annotations/ results/ reports/)
└── output/    (json/ pdf/)
```

## Technology Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Specified; strong typing + ecosystem |
| API | FastAPI | Async, Pydantic-native, OpenAPI |
| Validation | Pydantic + JSON Schema | Runtime validation + canonical schema versioning |
| Data | pandas or Polars | Tabular parsing & metric computation |
| DB | SQLite (PoC) → PostgreSQL (multi-user) | Simple first; migratable |
| ORM | SQLAlchemy | Portable across SQLite/PostgreSQL |
| Templating | Jinja2 + HTML→PDF | Deterministic memo rendering from JSON |
| Testing | pytest | Unit/integration/golden/ablation/e2e |
| LLM | provider-abstracted `LLMClient` | No provider lock-in; centralized logging |

File store for the PoC is a structured local directory under `data/`. The relational DB holds cases, source metadata, facts, metrics, provenance, flags, escalations, human reviews, prompt versions, model runs, and audit events.

## Components and Interfaces

### Core data objects (canonical schema)

**Top-level case record** (canonical system output; PDF renders from this):

```json
{
  "schema_version": "1.0",
  "case_id": "DAL_2024",
  "borrower": {}, "as_of_date": "2024-12-31",
  "evidence_cutoff_timestamp": "2025-02-15T23:59:59Z",
  "documents": [], "facts": [], "financials": {}, "metrics": {},
  "benchmarks": {}, "business_analysis": {}, "financial_analysis": {},
  "risks": [], "mitigants": [], "exceptions": [], "escalations": [],
  "human_reviews": [], "recommendation_draft": {}, "audit_metadata": {}
}
```

**DocumentRecord**: `document_id, case_id, filename, document_type, issuer, period, published_at, ingested_at, sha256, storage_path, source_authority, version, supersedes`.

**ExtractedFact**: `fact_id, name, value, unit, period, status, extraction_method, confidence, source_refs[], created_by`. Allowed `status`: `verified | unverified | conflicting | missing | not_applicable | not_disclosed | stale`. **`missing` is never numerical zero.**

**Canonical field** (value + state + provenance): preserves `raw_value`/`raw_unit` separately from `normalized_value`/`normalized_unit`, plus `period`, `status`, `source_refs[]`.

**Metric result**: `formula_id, inputs, input_fact_ids[], result | explicit_state, period, units, engine_version`. Explicit states: `not_meaningful | missing_input | requires_review`.

**Escalation**: `escalation_id, case_id, severity, rule_id, reason, triggered_at, evidence_refs[], status, resolution`.

**Audit event** (append-only): `event_id, case_id, event_type, timestamp, actor_type, actor_id, before, after, reason, linked_objects[]`.

### LLMClient abstraction

```python
class LLMClient:
    def extract(self, request): ...
    def analyze(self, request): ...
    def challenge(self, request): ...
```

All provider-specific calls live behind this interface. Every call: requests structured JSON against a fixed schema, uses temperature 0 where supported, and logs model ID, prompt ID/version/hash, input evidence IDs, raw + parsed response, and validation outcome to the model-run registry. Temperature 0 improves consistency but is **not** treated as sufficient for reproducibility — the raw run is always stored.

### Service responsibilities

| Service | Responsibility | Key guarantees |
|---|---|---|
| `ingestion` | Create cases; store originals; hash; version; enforce cutoff | Immutable originals; SHA-256; `supersedes`; cutoff rejection |
| `extraction` | Deterministic parse (priority order) + AI qualitative extraction | Structured > AI for numerics; every fact has source ref; AI never auto-`verified` |
| `reconciliation` | Compare routes; apply tolerances; set fact state | Conflicts preserved with both values/refs; human resolution is additive |
| `metrics` | Deterministic metric library | Inputs + formula + fact IDs stored; explicit states for bad denominators; reproducible |
| `benchmarking` | Peer cohort stats + rank/percentile | Cohort/date stored; borrower not double-counted; anomaly signal only |
| (policy rules) | Versioned, config-driven rule evaluation | Rule IDs; three concepts separated; thresholds in config |
| `analysis` | GenAI interpretation of canonical evidence | Schema-validated; claims carry evidence IDs + fact/interpretation + uncertainty |
| (challenge) | Second-pass critic | Non-destructive; high severity → escalation |
| `escalation` | Event-based routing to humans | Every escalation has reason + rule; cannot vanish unresolved |
| (human review) | Non-destructive review workflow | Before/after preserved; sign-off gates finalization |
| `reporting` | Canonical JSON first, then PDF | JSON↔PDF value parity; as-of/cutoff/exceptions shown |
| `audit` | Append-only event log | Events never modified |

### Policy configuration (illustrative)

```yaml
# ILLUSTRATIVE — NOT BANK POLICY
net_debt_to_ebitda:
  policy_limit: 3.5
  peer_review_percentile: 90
  historical_change_review: 0.50
```

Three concepts kept distinct: policy threshold, peer benchmark, historical deterioration. Changes are versioned and audited; historical runs stay linked to the rule version used.

## Data Models

- **Relational schema (SQLAlchemy):** `cases`, `documents`, `facts`, `fact_source_refs`, `metrics`, `benchmarks`, `rules`/`rule_versions`, `escalations`, `human_reviews`, `prompt_versions`, `model_runs`, `audit_events`.
- **Canonical JSON Schema:** versioned (`schema_version`), validated on output; schema versions tracked alongside case snapshots.
- **File store layout:** `data/raw` (originals, never mutated), `data/parsed`, `data/normalized`, `data/derived`, `data/snapshots` (frozen case versions). Outputs in `output/json` and `output/pdf`.

### Metric engine (examples)

```python
net_debt = total_debt - unrestricted_cash
net_debt_to_ebitda = net_debt / ebitda  # guarded: zero/missing/negative → explicit state
```

Metric-to-escalation example: ND/EBITDA trending `1.73x → 2.17x → 3.00x` against illustrative policy 3.50 and peer P90 3.10 → no policy breach, elevated peer anomaly, >50% 2Y deterioration → `R-TREND-LEV-01` fires analyst review. The narrative may explain it, but the trigger is deterministic and auditable.

## Error Handling

- **Missing inputs:** never coerced to zero; propagate `missing`/`missing_input`; downstream caveat or escalation.
- **Bad denominators:** metric engine returns `not_meaningful | missing_input | requires_review`, never a misleading ratio.
- **Conflicts:** reconciliation stores both values + both refs as a conflict record; no silent merge; human resolution is a new event.
- **Schema violations:** LLM output failing schema validation is rejected before use and logged in the model-run registry.
- **Cutoff violations:** documents newer than `evidence_cutoff_timestamp` are rejected in historical runs.
- **Uncertainty propagation:** fact `status`/`confidence` flow into metrics, analysis caveats, and escalation decisions so the system "knows what it does not know."
- **Fail-safe principle:** incomplete information lowers completeness/confidence and triggers caveats/escalation — it never produces invented values.

## Multi-Agent Boundaries

Only three distinct, independently testable roles (plus optional news): **Extraction** (qualitative facts from unstructured sources), **Analysis** (credit interpretation from canonical evidence), **Challenge** (unsupported claims / omissions / contradictions / missing info). No swarm of agents independently writing memos and voting.

## Security & Data Hygiene

Public/synthetic data only (unless authorized); secrets via env vars, never hardcoded/logged; sanitized filenames; restricted file types; JSON validation; path-traversal prevention; recorded provider calls; no external-model data egress unless permitted.

## Testing Strategy

Component-level and system-level, mapped to `tests/` subdirs:

- **unit** — formulas (exact ± rounding), unit conversion, date normalization, tolerance logic, policy/trend/escalation rules, versioning, hashing.
- **golden** — manually verified extraction dataset; measure field/numeric/citation accuracy and required-field recall, reported by field type.
- **end_to_end** — reproducibility: same snapshot/rules/model/prompt/config reproduces deterministic metrics, source set, escalation rules, and canonical JSON structure (store raw LLM run; don't assume exact regeneration).
- **ablation** — degraded variants (FULL, NO_XLSX, NO_XBRL, PDF_ONLY, NO_INDUSTRY_DATA, NO_FUEL_DATA, NO_DEBT_MATURITY_TABLE, NO_INTERIM_STATEMENTS); measure `performance_drop` and verify graceful degradation (caveat/escalation, never invented values).
- **prompts** — prompt/model A/B with blinded reviewer; groundedness, material-risk precision/recall, omissions; not "which sounds better."
- **integration** — AI-vs-deterministic delta: seeded disagreements detected, escalated, never merged.
- **Temporal backtesting** — expanding vs rolling windows; never mix future/past; don't assume one window superior.
- **Statistical** (if ML used) — precision/recall/F1/AUROC/calibration; McNemar for paired binary; paired bootstrap CIs; report effect size, CI, sample size.

### Evaluation metrics (evals/)

Extraction (field recall, numeric accuracy, citation accuracy, unit/period error); deterministic (formula accuracy, rule-trigger accuracy, benchmark reproducibility); generative (groundedness, unsupported-claim count, material-risk precision/recall, omission/contradiction counts); system (escalation precision/recall on seeded exceptions, human review rate, unresolved exceptions, reproducibility, processing time); missing-data robustness (performance drop by removed source, hallucination/unsupported-fill rate, correct escalation rate under missing critical evidence).

## Build Order (milestones → maps to tasks)

1. **Skeleton** — case creation, ingestion, hashing, DB, audit events, basic JSON schema (no AI yet).
2. **Deterministic financial pipeline** — structured parser, normalized schema, 5–8 metrics, unit tests, trends.
3. **Provenance + reconciliation** — fact objects, source refs, conflict detection, missing-data states, human correction workflow.
4. **LLM extraction** — one qualitative prompt, structured schema, source IDs, validation, prompt registry.
5. **Analysis + challenge** — GenAI analysis, challenge pass, unsupported-claim detector, schema-validated output.
6. **Benchmarking + escalation** — peer comparison, illustrative policy config, rule IDs, escalation queue.
7. **Human review** — review actions, before/after history, sign-off states.
8. **Outputs** — canonical case JSON, PDF rendered from JSON, evidence appendix.
9. **Evaluation harness** — golden dataset, temporal runs, missing-data ablations, A/B tests, summary metrics.

**MVP cut if time is tight:** keep versioned evidence store, provenance, canonical JSON, deterministic metrics, one peer benchmark layer, one generative analysis step, one challenge step, escalation rules, human review log, PDF rendering, missing-data tests. Defer learned thresholds, elaborate orchestration, real-time monitoring, news streaming, retraining, PD models, CDS ingestion, large-scale cloud deploy. The differentiator is **control architecture**, not model count.
