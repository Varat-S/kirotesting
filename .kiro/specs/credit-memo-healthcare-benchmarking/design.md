# Design — Medical-Device Industry Benchmarking Extension

## 1. Findings from the baseline (`18db2b9`)

What is connected end to end versus isolated, as inspected before any change.

| Area | Finding |
|---|---|
| `PeerBenchmarker` | Wired into `CreditMemoPipeline._context` for every case. Peers come from `config/poc/peers.json` entity ids looked up in the case's own metrics, so a case with no peer entities gets an empty cohort. |
| `MetricEngine`, `TrendAnalyzer` | Wired. The metric set is the airline-oriented `config/poc/metric_defs.json`; `casm` and `load_factor` are missing for any non-airline borrower and raise a mandatory escalation. |
| `ParameterEngine` | Unit-tested only. The pipeline never calls it. |
| Agentic runner | `AgenticAnalysisOrchestrator.run` received `metrics`, `trends` and `benchmarks` and used none of them. No deterministic parameter was persisted in a run. |
| Scoring | Computed only from LLM-promoted parameters, so with the offline backend every score is `unavailable`. Scoring ids `interest_coverage` and `liquidity` share names with metrics that are defined differently (the metric `liquidity` is a currency amount; the scoring band is a ratio). |
| Orchestrators / challengers | Not executed. `build_financial_orchestrator_input` was called only from tests; conclusions were a fixed sentence. |
| `EvidenceRouter` | Not used by the runner; every narrow agent was handed all facts. |
| Final snapshot agentic fields | Not populated; provenance sits under `business_analysis.agentic`. |
| `tests/end_to_end/test_delta_agentic.py::test_delta_agentic_end_to_end` | Failing at baseline wherever OCR is available (it is skipped in CI): it expects a top-level `agentic` block in the memo JSON and at least one parameter in the run. |
| SEC client | Does not read the SIC code. |
| Field mapping | No canonical names for receivables, inventory, cost of sales, R&D, taxes paid, intangibles or acquisitions. |

### Conflicts with the handoff, and how they are resolved

1. **`#NAME?` cells.** The supplied workbook has no cached error values. The
   working-capital rows depend on an external workbook
   (`Healthcare_Industry_Benchmarks.xlsx`), which is what yields `#NAME?`/`#REF!`
   when that file is absent. The importer detects both conditions; the external
   references are re-derived locally and logged.
2. **Company sheet.** It holds identity and classification only (870 rows, no
   financials), so Method B cannot be populated from it. It is used only as a
   classification signal.
3. **Adding to core config.** Extending `ARTIFACT_KINDS`, `metric_defs.json` or
   `financial_mappings.json` would change the content hashes and escalations of
   every existing case. Sector artifacts therefore use optional registry kinds,
   and extra mapping rules and metrics apply only to opted-in cases.
4. **Evidence selectors.** The router is not in the execution path, so sector
   context is delivered as task-input data to the existing agents, using each
   agent's existing routing spec to select narrative passages.
5. **Live SEC acquisition** needs a contact email in the user agent; none is
   configured here. The pilot is an offline fixture; the live path is one command.

## 2. Flow

```
package.sector_benchmark ─┐
                          ▼
register sector config (ConfigRegistry, optional kind)      extend field mapper
                          │
ingest → reconcile → CanonicalEvidenceSnapshot → metrics → trends → peers   (unchanged)
                          │
                          ▼  sector stage (deterministic, app/services/pipeline/sector.py)
workbook bytes → import + validate → reference dataset (ConfigRegistry, optional kind)
identity + narrative → classification ── requires_review → stop, escalate
borrower metrics (MetricEngine results + ParameterEngine) + own history (TrendAnalyzer)
        × validated industry aggregates → comparability → comparisons
                          │
        legacy ───────────┤            agentic
                          │              ├ benchmark numbers → accepted ParameterResults
                          │              ├ narrow agents ← sector risk dimensions (data)
                          │              ├ Financial Orchestrator ← bounded benchmark_context
                          │              └ deterministic gate → conclusion or fallback
                          ▼
persist comparisons → draft (financial_analysis.industry_benchmarking) → memo
                          │
human sign-off → finalize (blocked if the benchmark is stale or superseded)
```

## 3. Components

| Module | Role |
|---|---|
| `app/services/benchmarking/formula.py` | Safe evaluator for the workbook's formula subset. |
| `app/services/benchmarking/workbook.py` | Importer, validator, dataset and report. |
| `app/services/benchmarking/sector_config.py` | Load, validate, register the sector configuration and dataset. |
| `app/services/benchmarking/classification.py` | Classifier, override, persistence. |
| `app/services/benchmarking/aggregates.py` | Comparability, Method A, comparison store, stale-benchmark guard. |
| `app/services/benchmarking/peer_cohort.py` | Method B cohort contract over `PeerBenchmarker`. |
| `app/services/parameters/sector.py` | Nine guarded ratio parameters. |
| `app/services/pipeline/inputs.py` | Fiscal input pools, extracted from `_metrics`. |
| `app/services/pipeline/sector.py` | The pipeline stage. |
| `app/services/orchestration/benchmark_context.py` | Parameter results, bounded context, narrative guards. |
| `app/services/orchestration/sector_observations.py` | Sector dimensions for narrow agents and observation validation. |
| `app/services/pipeline/sector_demo.py` | Scripted offline orchestrator double. |

## 4. Data contracts

**Reference dataset** (`industry-reference-dataset-1.0`): `source` (filename,
sha256, sheets, external links), `vintage`, `industries`, `assumptions`,
`documentation`, `metrics[]` (label, definition, note, per-industry value with
cell, formula, cached and re-derived value, status, reasons, dependencies,
reconciliation, eligibility), `sic_codes`, `companies`, `correction_log`,
`dataset_hash`.

**Comparison**: `comparison_id`, `benchmark_method`, `industry_label`,
`comparison_state`, `reasons`, `caveats`, `warnings`, `comparability_checks`,
`borrower` (value, state, units, period, fact ids, definition),
`industry_aggregate` (value or withheld, status, definition, sheet/cell/formula),
`difference`, `direction_vs_aggregate`, `position_vs_aggregate`, `own_history`,
`provenance`, `input_hash`. No peer-statistic fields.

**Benchmark context** (`benchmark-context-1.0.0`): sector, method, industry,
vintage, hashes, classification status, standing limitations, role constraints
and per comparison the state, reasons, caveats, parameter-result ids with values,
position and own-history direction. Size-bounded and key-whitelisted.

## 5. Storage

Two new append-only tables: `sector_classifications` and
`industry_benchmark_comparisons`. The latter has no peer-statistic columns.
`init_db` creates them; `app/migrate.py` reports them. Snapshots store the
payload under the existing free-form `financial_analysis`, so no snapshot schema
change is needed. Optional registry kinds: `sector_benchmark.<sector>` and
`industry_reference_dataset.<sector>`.

## 6. Scoring boundary

Benchmark parameter ids are namespaced `sector_benchmark.*`; none exists in
`scoring.json`. The runner scores from exactly the same list as before. The
sector configuration validator rejects scoring keys.

## 7. Decisions deliberately not taken

- Wiring deterministic metrics into official scoring (definitions differ; needs
  its own specification).
- New scoring rubrics for the medical-device observations.
- Populating the snapshot's top-level agentic fields.
- Sector benchmarking in the browser review / draft-regeneration path.
- A corrected copy of the workbook; the correction log lives in the dataset.
