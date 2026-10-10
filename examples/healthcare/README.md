# Healthcare / medical-device benchmarking example

## What is here

> **Not in the repository.** The workbook and the two files generated from it
> (`reference/`) are kept local and are git-ignored, so the 114 tests that need
> them skip on a fresh clone and in CI. Place the workbook at
> `examples/healthcare/reference/Healthcare_Financial_Overview_Benchmarks.xlsx`
> and run `benchmark-import` (below) to enable them.

| Path | What it is |
|---|---|
| `reference/Healthcare_Financial_Overview_Benchmarks.xlsx` | The benchmark workbook, byte-for-byte as supplied (SHA-256 `25ae2845…baa2`). Benchmark **reference data**, never borrower evidence. |
| `reference/normalized_dataset.json` | The normalized reference dataset produced by the importer. |
| `reference/validation_report.json` | The machine-readable validation report. |
| `stryker_fixture_package.json` | Source package for the offline pilot case. |
| `stryker-fixture/financials.csv` | **Synthetic** financial statements for the pilot. |

## The fixture is synthetic

`stryker-fixture/financials.csv` holds invented, round illustrative numbers in
USD millions for FY2021–FY2024. They are **not** Stryker Corporation's reported
financials and say nothing about Stryker's credit quality. The entity is named
`Stryker Corporation (SYNTHETIC FIXTURE - NOT REPORTED FINANCIALS)` so the label
travels into every output.

Only the identity used for classification relates to the real company: ticker
`SYK`, SIC `3841` and industry group `Healthcare Products` are read from the
workbook's company sheet (row 686). The CIK `310764` is declared in the package
and was **not** verified against SEC EDGAR, so the classification is recorded as
`declared_unverified` with confidence capped at `medium`.

## Run it offline

```powershell
$env:DEBUG = 'false'
# Deterministic comparisons only
python -m app.cli --database data/syk_fixture.db --output output/syk_fixture run-case SYK_FIXTURE --package examples/healthcare/stryker_fixture_package.json
# With the agentic layer (offline fake backend; no network, no credentials)
python -m app.cli --database data/syk_fixture_agentic.db --output output/syk_fixture_agentic --analysis-mode agentic run-case SYK_FIXTURE --package examples/healthcare/stryker_fixture_package.json
```

The result is an unapproved draft. Approval and finalization are the same
explicit human steps as for any other case (`approve`, then `finalize`).

The default offline backend returns safe-empty agent answers, so the draft shows
the deterministic comparison table without an interpretation. The test suite
uses `app.services.pipeline.sector_demo.ScriptedBenchmarkBackend`, a scripted
test double (not a model), to exercise the interpretation path.

## Regenerate the reference artifacts

```powershell
python -m app.cli benchmark-import examples/healthcare/reference/Healthcare_Financial_Overview_Benchmarks.xlsx --out examples/healthcare/reference
```

A test fails if the checked-in JSON differs from a fresh import.

## Run on real Stryker filings (not done here)

Live acquisition needs your own contact details in the SEC user agent, which
were not available when this was built:

```powershell
$env:SEC_USER_AGENT = 'Your Name you@example.com'
python -m app.cli --database data/syk.db --output output/syk --analysis-mode agentic run-sec-case SYK_FY2024 --ticker SYK --fy 2024 --cutoff 2025-03-01T00:00:00Z --sector medical_devices --reference-workbook examples/healthcare/reference/Healthcare_Financial_Overview_Benchmarks.xlsx
```

On that path the CIK and SIC are read from SEC EDGAR (`sec_verified`) and the
10-K narrative is used to corroborate the classification. It has not been
executed, so treat the following as unverified:

- whether the `--fy` selection picks the intended 10-K;
- field coverage: the base mapping has no us-gaap rule for total debt, EBITDA or
  unrestricted cash, so leverage and coverage ratios are likely to come back
  `missing_input` until input derivations for a real filer are added and
  reviewed;
- multi-year history, which needs more than one annual filing's facts.
