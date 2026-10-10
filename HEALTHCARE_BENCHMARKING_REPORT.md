# Medical-Device Industry Benchmarking — Implementation Report

Branch `spec/agentic-analysis`. The work is in the **working tree on top of
`18db2b9b1bffa2ea17c27c1c752ff55314a8177d`; nothing has been committed or
pushed**, so there is no new commit SHA yet.

Specification: [`.kiro/specs/credit-memo-healthcare-benchmarking/`](.kiro/specs/credit-memo-healthcare-benchmarking/).
Example and run instructions: [`examples/healthcare/README.md`](examples/healthcare/README.md).

## 1. Status in one page

| Definition-of-done item | Status |
|---|---|
| 1. Reproducible Stryker case computing supported metrics | **Done on a clearly labelled synthetic fixture.** Not run on real filings. |
| 2. Healthcare Products selected | Done (classified, confidence `medium`, identity not SEC-verified). |
| 3. Workbook validated with documented errors, limitations, assumptions | Done. |
| 4. Validated aggregates compared with borrower metrics | Done (13 of 23 configured comparisons are permitted). |
| 5. Incompatible metrics blocked or qualified | Done (4 blocked, 10 qualified, 6 unavailable, each with reasons). |
| 6. No aggregate shown as a median or percentile | Done; result and table have no such fields. |
| 7. No fabricated DSCR | Done; DSCR is `missing_input` and the workbook proxy is withheld. |
| 8. Scoring engine remains authoritative | Done; scores proven identical with and without the benchmark. |
| 9. Agents interpret without scoring or arithmetic | Done with fake backends. **No live model has been run.** |
| 10. Memo shows contextual comparisons with provenance and limitations | Done. |
| 11. Delta and other regressions pass | Done; one baseline failure is also fixed. |
| 12. No historical artifact or finalized snapshot mutated | Done; superseded rows keep their payload. |
| 13. Offline tests pass | 881 passed, 1 skipped (PDF backend not installed). |
| 14. Complete / partial / unverified identified | Sections 9 and 10. |

**Benchmark findings affect narrative only.** They enter the memo table, the
Financial Orchestrator's context and the financial conclusion. They do not enter
scoring, policy rules or escalation thresholds.

## 2. What the baseline actually did

Recorded in full in the design document, §1. The points that shaped this work:

- The agentic runner received `metrics`, `trends` and `benchmarks` and used none
  of them; no deterministic parameter was stored in a run.
- `ParameterEngine`, `EvidenceRouter` and the orchestrator input builders were
  exercised only by tests, not by the pipeline.
- Scores are computed only from LLM-promoted parameters, so with the offline
  backend every score is `unavailable`. That is unchanged.
- `tests/end_to_end/test_delta_agentic.py::test_delta_agentic_end_to_end` failed
  at baseline on any machine with OCR (CI skips it). It now passes.

## 3. Architectural flow

Original:

```
evidence snapshot → metrics → trends → empirical peers → _ai → draft → memo
                                                          └ agentic: narrow agents → scores → fixed-sentence conclusions
```

Revised (the added path runs only when a package declares `sector_benchmark`):

```
evidence snapshot → metrics → trends → empirical peers
        └→ sector stage: workbook import/validation → reference dataset
                         classification (or stop for review)
                         borrower metrics + own history
                         comparability → industry-aggregate comparisons
        → _ai
            └ agentic: metrics → accepted parameter results (all cases)
                       benchmark numbers → accepted parameter results
                       narrow agents ← sector risk dimensions
                       Financial Orchestrator ← bounded benchmark_context
                       deterministic gate → conclusion, or deterministic fallback
        → comparisons persisted → draft → memo → human sign-off → finalize
                                                 (blocked if the benchmark is stale)
```

## 4. File-by-file changes

New modules

| File | Purpose |
|---|---|
| `app/services/benchmarking/formula.py` | Safe formula evaluator for independent recalculation. |
| `app/services/benchmarking/workbook.py` | Workbook importer, validator, dataset, report. |
| `app/services/benchmarking/sector_config.py` | Sector configuration loading, validation, registration. |
| `app/services/benchmarking/classification.py` | Sector classifier, human override, persistence. |
| `app/services/benchmarking/aggregates.py` | Comparability, Method A, comparison store, stale guard. |
| `app/services/benchmarking/peer_cohort.py` | Method B cohort contract. |
| `app/services/parameters/sector.py` | Nine guarded ratio parameter definitions. |
| `app/services/pipeline/inputs.py` | Fiscal input pools, moved out of `_metrics` unchanged. |
| `app/services/pipeline/sector.py` | The sector pipeline stage. |
| `app/services/pipeline/sector_demo.py` | Scripted offline orchestrator double for tests. |
| `app/services/orchestration/benchmark_context.py` | Parameter results, bounded context, narrative guards. |
| `app/services/orchestration/sector_observations.py` | Sector dimensions and observation validation. |
| `config/healthcare/medical_devices.json` | Versioned sector configuration. |
| `examples/healthcare/**` | Workbook, generated dataset and report, synthetic fixture, README. |

Modified modules

| File | Change |
|---|---|
| `app/core/config_registry.py` | Optional per-sector kinds; core `ARTIFACT_KINDS` untouched. |
| `app/models/orm.py`, `app/migrate.py` | Tables `sector_classifications`, `industry_benchmark_comparisons`. |
| `app/services/audit/log.py` | Five audit event types. |
| `app/services/parameters/formulas.py`, `financial.py`, `registry.py` | `positive_denominator_ratio`; sector parameters composed into the financial set. |
| `app/services/mapping/mapper.py` | `extended()` for sector mapping rules without re-versioning the base mapping. |
| `app/services/pipeline/package.py`, `sec.py`, `result.py`, `inspection_views.py` | Optional `sector_benchmark` input and outputs. |
| `app/services/pipeline/runner.py` | Sector stage; inapplicable airline metrics and tags skipped for sector cases only. |
| `app/services/pipeline/agentic_runner.py` | Metrics as run parameters; benchmark context; orchestrator gate; observations. |
| `app/services/orchestration/financial.py` | `benchmark_context` on the orchestrator input, validated. |
| `app/prompts/agent_prompts.py`, `agent_schemas.py` | Benchmark rules for the Financial Orchestrator; `sector_observations`. |
| `app/services/reporting/memo.py`, `templates/memo.html.j2` | Top-level `agentic` block; benchmarking subsection. |
| `app/services/review/finalization.py` | Stale-benchmark check at finalization. |
| `app/services/workbench/agentic.py` | `industry_benchmarks` section. |
| `app/services/acquisition/sec_edgar.py` | `company_profile()` (CIK, SIC). |
| `app/cli.py` | `--analysis-mode`, `benchmark-import`, `classify-override`, sector flags on `run-sec-case`. |
| `tests/unit/test_audit_immutability.py` | Exact event-type set extended with the five new events. |

Changes that reach existing (non-sector) cases, all covered by the passing suite:

1. Agentic runs now persist the case's deterministic metrics as accepted
   parameter results and list their ids on the draft. They are not scored.
2. The memo JSON of an agentic case gains a top-level `agentic` key (a copy).
3. The narrow-agent and Financial Orchestrator prompt texts gained conditional
   rules, so those prompts register a new content hash.
4. Nine parameter ids were added to the deterministic set.

## 5. New deterministic parameters

All use `positive_denominator_ratio`: missing input → `missing_input`;
denominator near zero → `requires_review`; negative denominator →
`not_meaningful`.

| Parameter | Definition |
|---|---|
| `gross_debt_to_ebitda` | total debt / EBITDA |
| `ebit_interest_coverage` | operating income / interest expense |
| `ebitda_margin` | EBITDA / revenue |
| `gross_margin` | gross profit / revenue |
| `fcf_to_revenue` | (CFO − CapEx) / revenue |
| `rnd_to_revenue` | R&D expense / revenue |
| `cash_tax_burden` | income taxes paid / pre-tax income |
| `goodwill_intangibles_to_total_assets` | (goodwill + other intangibles) / total assets |
| `acquisitions_to_cfo` | acquisition spend / CFO |

Reused unchanged: `net_debt_to_ebitda`, `interest_coverage` (EBITDA-based),
`cash_conversion`, `operating_margin`, `revenue_growth`, `free_cash_flow`,
`liquidity`, `capex_to_revenue` from the `MetricEngine`; `dso`, `dio`, `dpo`,
`cash_conversion_cycle`, `revenue_cagr`, `dscr` from the `ParameterEngine`.

Not implemented because no structured input exists: organic versus acquired
growth, recurring revenue share, geographic and segment concentration,
litigation and recall provisions, quantified regulatory exposure. These are
covered only as qualitative risk dimensions.

## 6. Workbook validation report

Source `Healthcare_Financial_Overview_Benchmarks.xlsx`, SHA-256
`25ae2845901d77a9bb3e798a6da63b0b660088734c20c0ad0d4dc7c72241baa2`, data as of
2026-01, SIC list updated 2026-06-18. Dataset hash
`93400c4f91d3a4583f6ca61cd96b2a29d31478b4f9c01a4d2fa3115f49ff025e`. Full report:
`examples/healthcare/reference/validation_report.json`.

| Status | Count | What |
|---|---:|---|
| observed | 911 | 41 SIC rows, 870 company identity rows |
| source_aggregate | 150 | 25 published rows × 6 industries |
| derived | 222 | formulas over published aggregates |
| assumed | 21 | 3 assumption inputs + 18 values depending on the 10% placeholder |
| unavailable | 12 | the two `n/a` rows |
| invalid | 0 | |

Issues: 0 errors, 68 warnings, 7 informational.

- **All 240 formula values re-derive exactly to their cached results.**
- **No `#NAME?` cell exists in the supplied file.** The handoff expected some. What
  is present is a link to an external workbook
  (`Healthcare_Industry_Benchmarks.xlsx`) used by 66 formula cells (DSO, DIO,
  DPO, interest expense and principal). Those are re-derived from the local
  Assumptions sheet, whose three values equal the link's cached values; the
  three rebindings are in the correction log. A separate test shows a cached
  `#NAME?` is detected and repaired the same way.
- DSCR, FCCR and "Principal due" are `assumed` in every industry and excluded.
- 78 values are eligible for comparison; 324 are excluded, each with a reason
  (mostly declared context-only or diagnostic-only, or unmapped rows).
- Data-quality notes: SIC 7370 and 7373 carry the same industry title; the
  company sheet lists fewer firms than each aggregate covers (157 versus 204 for
  Healthcare Products), so it is not the aggregate's sample; 81 company rows
  spell the IT industry "Heathcare".
- The aggregates are transcriptions. They were not checked against Damodaran
  Online.

## 7. Industry aggregate mapping (Healthcare Products, column E)

| Comparison | Borrower metric | Workbook row (cell) | Value status | Result on the fixture |
|---|---|---|---|---|
| net_debt_to_ebitda | MetricEngine | Net debt / EBITDA (E9) | derived | comparable with caveats |
| gross_debt_to_ebitda | new | Debt / EBITDA (gross) (E33) | source aggregate | comparable with caveats (lease treatment undocumented) |
| ebit_interest_coverage | new | ICR (EBIT) (E4) | derived | comparable with caveats |
| ebitda_interest_coverage | MetricEngine | ICR (EBITDA) (E5) | derived | comparable with caveats |
| cfo_to_ebitda | MetricEngine | CFO / EBITDA (E12) | derived | comparable with caveats (industry CFO is a proxy) |
| operating_margin | MetricEngine | EBIT / Sales (E29) | source aggregate | comparable |
| ebitda_margin | new | EBITDA / Sales (E28) | source aggregate | comparable |
| gross_margin | new | Gross margin (E26) | source aggregate | comparable |
| dso, dio, dpo, cash_conversion_cycle | ParameterEngine | rows 13–16 | derived, external link rebound | comparable with caveats |
| cash_tax_burden | new | Effective tax rate (E34) | source aggregate | comparable with caveats |
| fcf_to_revenue | new | UFCF / Sales (E7) | derived | **not comparable** (levered versus unlevered) |
| capex_to_revenue | MetricEngine | Net CapEx / Sales (E47) | source aggregate | **not comparable** (net of D&A, includes acquisitions and R&D) |
| rnd_to_revenue | new | Net R&D $mm (E46) | source aggregate | **not comparable** (a dollar amount net of amortization) |
| dscr | ParameterEngine | DSCR (E3) | assumed | **not comparable**; borrower value unavailable |
| revenue_growth, revenue_cagr, free_cash_flow, liquidity, goodwill and acquisition ratios | various | none | — | unavailable: no industry figure |

Worth a reviewer's eye: the published effective tax rate for Healthcare Products
is 4.85%, which is low for a tax rate. It is passed through as transcribed, with
a caveat, and the position is not assessed.

## 8. Sample output (synthetic fixture, FY2024)

Borrower values come from invented fixture numbers; only the industry column is
workbook data.

| Metric | Borrower | Industry aggregate | State | Versus aggregate | Own history |
|---|---:|---:|---|---|---|
| Net debt / EBITDA | 1.517 | 2.031 | with caveats | stronger | deteriorating |
| Gross debt / EBITDA | 1.858 | 2.740 | with caveats | stronger | deteriorating |
| EBIT interest coverage | 9.423 | 7.145 | with caveats | stronger | deteriorating |
| CFO / EBITDA | 0.743 | 0.637 | with caveats | stronger | deteriorating |
| Operating margin | 0.183 | 0.153 | comparable | stronger | deteriorating |
| Cash conversion cycle (days) | 210.0 | 143.2 | with caveats | weaker | deteriorating |
| R&D / revenue | 0.066 | not compared | not comparable | — | — |
| DSCR | missing_input | not compared | not comparable | — | — |
| Revenue growth | 0.107 | not available | unavailable | — | improving |

Interpretation produced by the scripted test double (templates, not a model),
accepted by the deterministic gate:

> The borrower's net debt/EBITDA of 1.52x is stronger than the Healthcare
> Products industry aggregate of 2.03x; over FY2021-FY2024 the borrower's own
> net debt/EBITDA trend is deteriorating. The comparison is contextual rather
> than evidence of default risk.

Recorded contradiction: "net debt/EBITDA: favourable versus the industry
aggregate but deteriorating versus the borrower's own history."

## 9. Tests

Environment: Windows 11, the repository's `.venv` (Python 3.13),
`python -m pytest -q` with `DEBUG=false`.

| | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Baseline `18db2b9` | 766 | 1 | 1 |
| After this work | 881 | 0 | 1 |

Those counts are with the reference workbook present locally. The workbook is
not published in the repository, so on a fresh clone and in CI the 114 new tests
skip (767 passed, 115 skipped). The other skip is the PDF backend. The baseline failure was
`test_delta_agentic_end_to_end`. CI's Python 3.11 and 3.12 jobs were not run
locally.

New tests (114):

| File | Tests | Covers |
|---|---:|---|
| `tests/unit/test_healthcare_workbook.py` | 30 | sheets, six industries, statuses, reconciliation, external links, cached errors, invalid values, dates, hash invalidation, no hard-coded averages |
| `tests/unit/test_industry_benchmarking.py` | 46 | classification, override, comparability dimensions, lease and period incompatibility, Method A without peer statistics, Method B cohort, small samples, guarded ratios, DSCR, scoring isolation |
| `tests/integration/test_sector_benchmark_pipeline.py` | 33 | full pipeline in both modes, missing data, bounded context, provenance of every number, rejection of six kinds of unsupported claim, observations, identical scores, reproducibility, supersession, stale-draft guard, approval and finalization, unchanged non-sector cases |
| `tests/integration/test_sector_benchmark_cli.py` | 5 | `benchmark-import`, `run-case` in both modes, SEC identity lookup with a fake transport |

The handoff's named regression suites (benchmarking, metric engine, parameter
engine, scoring engine, Delta default, Delta agentic, SEC pipeline, analysis
mode, snapshot immutability, leakage, reproducibility): 126 passed.

One existing test was edited: the exact audit event-type set gained the five new
events. No assertion was removed or loosened.

Not covered by tests: `run-sec-case --sector` end to end on a real bundle, and
any live provider.

## 10. Migration and configuration

- Existing SQLite databases: back up, then `python -m app.migrate --database <db>`.
  It creates the two new tables; no existing row is rewritten.
- No change to `config/poc/*`. The sector configuration is registered on first
  use under `sector_benchmark.medical_devices`; each distinct workbook becomes a
  version of `industry_reference_dataset.medical_devices`.
- A case opts in through `sector_benchmark` in its package (see the fixture) or
  `--sector` / `--reference-workbook` on `run-sec-case`.

## 11. Known limitations

1. **No real Stryker data.** The pilot's financials are synthetic. Live SEC
   acquisition needs a contact email in `SEC_USER_AGENT`, which was not set.
2. **The real-filing path is likely incomplete.** The base mapping has no us-gaap
   rule for total debt, EBITDA or unrestricted cash, so leverage and coverage
   would probably be `missing_input` on a real 10-K until derivations are added.
3. **No live model run.** Agent behaviour is shown only with fake backends.
4. **Official scores remain `unavailable` offline**, as at baseline, because
   scoring still consumes only LLM-promoted parameters.
5. **Method B has no data.** Only the contract and tests exist.
6. **Classification of the fixture is `medium` confidence**: identity is
   declared, and there is no narrative evidence to corroborate it.
7. **Evidence routing** for sector context is input data on existing agents; the
   router is still outside the main execution path.
8. **Browser review / draft regeneration** does not carry the benchmark section.
9. **The financial challenger does not run** over the benchmark interpretation.
10. **Legacy mode** shows the deterministic table but no interpretation.
11. Coverage flags for `industry`, `debt_maturity` and `interim` sources still
    fire for the fixture; `fuel`, `casm` and `load_factor` are declared
    inapplicable for the sector.
12. The memo prints numbers at full precision, consistent with the rest of the
    memo.
13. The workbook and its generated dataset are deliberately kept out of the
    repository (git-ignored under `examples/healthcare/reference/`).

## 12. Recommended next steps

1. Run the pilot on real Stryker filings and add reviewed input derivations for
   total debt, EBITDA and unrestricted cash for a real filer.
2. Verify the transcribed aggregates against Damodaran Online, in particular the
   effective tax rate.
3. Decide, as a separate specification, whether and how deterministic metrics
   should feed official scoring.
4. Acquire real peer financials and populate Method B.
5. Run the optional live-provider validation of the orchestrator and observation
   prompts, then add the financial challenger to this path.
6. Carry the benchmark section through the browser review path.
