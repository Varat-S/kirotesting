# HCA Healthcare FY2025 — Business Overview demonstration case

Primary demonstration borrower for the Business Overview (BO) module.

## Status

| Item | State |
|---|---|
| FY2025 Form 10-K fetched from SEC EDGAR | Done locally (accession `0001193125-26-044769`, filed 2026-02-10). **Not committed**: `filing/` is untracked (~10 MB). |
| Ingestion through the SEC path | Works: 16 files admitted, 1,547 facts, 180 narrative passages |
| Six BO specialists, live model | Run on `gemini-3.1-flash-lite`; all six validate. Latest: `ar_aaf78c0cf54943da` |
| Full BO path (orchestrator, challenger, memo) on HCA | **Not run** with a model |
| Review manifest | **Unverified template**: no reviewer has checked it against the filing |

Results and limitations: [`BO_QUALITY_REMEDIATION_REPORT.md`](../../../BO_QUALITY_REMEDIATION_REPORT.md).

## Files

| File | Purpose |
|---|---|
| `package.json` | Source package pointing at `filing/bundle.json`; as-of 2025-12-31, cut-off 2026-03-31 |
| `coverage_checks.json` | Case-specific coverage questions (currently: Galen). Questions and search terms only, no answers |
| `review_manifest.json` | Evaluation manifest template, `unverified_pending_filing`; concepts only, no figures |
| `filing/` | The SEC bundle (untracked; fetch it yourself) |

## Fetch the filing

SEC EDGAR needs a contact e-mail in the User-Agent. Set it in the terminal
(never committed):

```powershell
$env:SEC_USER_AGENT = "Your Name you@example.com"
python -m app.cli sec-fetch HCA --fy 2025 --destination examples/healthcare/hca-2025/filing
```

The destination folder must not exist. Do not type angle-bracket placeholders in
PowerShell; `<` is a redirection operator.

## Run

Six specialists and reports, live (provider cost; needs `.env`, see
`app/services/bo/README.md`):

```powershell
python -m app.cli --database data/bo_live.db --output output/bo_live `
    run-bo-specialists HCA_2025_LIVE --package examples/healthcare/hca-2025/package.json `
    --coverage examples/healthcare/hca-2025/coverage_checks.json --live
```

Add `--baseline <earlier run>/specialists_calls.json` (or `specialists_run.json`)
for an old-versus-new benchmark. Use `--replay <run>/specialists_run.json` in
place of `--live` to re-validate recorded responses with no model call; that
needs the same case id (`HCA_2025_LIVE`) and database as the recorded run.

Outputs land in `output/bo_live/bo/HCA_2025_LIVE/<analysis_run_id>/`:
`executive_summary.html`, `specialists_comparison.html` / `.pdf`,
`validation_audit.html`, `benchmark.html`, `specialists_run.json`.

Full BO path with no model (asserts nothing; checks ingestion and that the
manifest's anchor concepts are in the admitted text):

```powershell
python -m app.cli --database data/bo_demo.db --output output/bo_demo run-bo-case HCA_2025 `
    --package examples/healthcare/hca-2025/package.json `
    --manifest examples/healthcare/hca-2025/review_manifest.json
```

## Verify the manifest (open task)

For each `source_confirmed_facts` entry, replace `source_patterns` with a
`verified_quote` copied from the filing; fill `filing` (CIK 860730, accession,
filing date, primary-document hash from `filing/bundle.json`); add
`recomputed_values` from the deterministic indicators; then set
`verification_status` to `reviewer_verified` and name the reviewer. A human must
sign this off. The groupmate's reference document may suggest topics; it is not
a source of facts.

## What the filing contains that matters here

Found during the work, useful when judging outputs:

* The Texas/Florida revenue sentence has a page number inside it in the filing
  text, so a clean quote needs recovery to verify.
* "Galen" appears twice: Galen College of Nursing (workforce) and Galen Health
  Care, Inc. (a former employer in an executive biography, unrelated).
* Revenue by payer category, salaries and benefits, supplies expense and
  estimated implicit price concessions are tagged in XBRL and become
  deterministic indicators.
* Revenue per equivalent admission, admissions and state shares are text or
  table figures only: admissible as verbatim quotes, never calculated.
* Operating income is not tagged, so the operating-margin metric is unavailable.
