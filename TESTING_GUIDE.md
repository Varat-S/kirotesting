# Try the local browser preview

From the repository folder, start the preview:

```powershell
.\.venv\Scripts\python.exe -m app.preview --seed-demo
```

Open <http://127.0.0.1:8000/>. Stop a preview launched in your terminal with
Ctrl+C. If port 8000 is occupied, add `--port 8001` and open that port instead.
The server listens on this computer only. It uses `data/browser_demo.db` and
`output/browser_demo`, separate from the earlier hardening demo. Restarting
does not duplicate its seeded cases.

## What you can test in the browser

**The default test case is now the user's three Delta PDFs.** Open `/inspect`
and click **Open default Delta evidence preview**; the files and metadata are
already included. Use **Run the Delta files again as a new case** to repeat
processing. Start the server with `--seed-default` to prepare the default on
startup. This preset uses the PDF selection and excludes the `.xls` version.

The refreshed default is `/inspect/DELTA_2025_OCR_REVIEW/1`, as of December 31,
2025, with a March 1, 2026 cutoff. Earlier cases remain available. All 132 pages
are captured, including six OCR pages; 358 structured observations are mapped
and nine of eleven metrics can be calculated. Seven of those metrics still
require evidence/definition review. Interest coverage and liquidity remain
unavailable because gross interest and a structured revolver input are absent.
Net interest and cash interest are kept as distinct extracted fields.

**Saved outputs / JSON** includes recorded extraction and mapping outputs,
complete page captures with raw numeric candidates, reconciliation, canonical
evidence, metrics, context, exact LLM input and the full preview. SHA-256 payload
hashes and a hash chain are recorded during processing and checked on read.
The reference-check artifact contains 71 source-value/metric checks and an OCR
numeric-cell comparison. Historical exports without recorded hashes remain
labelled as export-time projections.

Use **Review** beside a fact to verify it, map an unresolved label, or select a
source value for a conflict. Enter your name, reason and explicit confirmation.
A decision creates a new evidence version and recalculates metrics; earlier
versions and original values remain available. Resolve exceptions separately
with a documented judgment. The release and audited 10-K have real balance-sheet
differences which require review.

Use **Generate draft** in the LLM section. Offline mode runs three empty scripted
responses to test the workflow. Live mode uses your configured provider and
sends the exact saved evidence; it does not reparse documents. Drafts expose
analysis, challenges, grounding, limitations, and separate approval/finalization
controls. A matching human approval and resolution of mandatory exceptions are
required for finalization. Superseded evidence and drafts cannot be approved or finalized. Use the explicit
regeneration checkbox to create a new draft or retry after a provider failure;
this preserves previous runs and incurs new provider calls in live mode.

Configure live models locally in the ignored `.env` file (see `.env.example`):
`LLM_PROVIDER=openai`, an explicit `LLM_MODEL`, and `LLM_API_KEY`. For a local
model use `LLM_PROVIDER=openai_compatible` and its `LLM_BASE_URL`, such as
`http://127.0.0.1:1234/v1`. Restart the preview after changing settings. Keep
keys out of source control. Live execution is never part of automated tests.
Inputs exceeding `LLM_MAX_INPUT_BYTES` are rejected without truncation; requests
have a timeout and are not automatically retried or billed repeatedly.

## Try the real NVIDIA filing

```powershell
$env:DEBUG = 'false'
.\.venv\Scripts\python.exe -m app.cli --database data/sec_demo.db --output output/sec_demo run-sec-case NVDA_2026 --bundle examples/sec/nvda-2026/bundle.json --entity-name 'NVIDIA Corporation' --cutoff 2026-03-01T00:00:00Z
.\.venv\Scripts\python.exe -m app.preview --database data/sec_demo.db --output output/sec_demo --port 8001
```

Open <http://127.0.0.1:8001/> and select `NVDA_2026`. No credentials or live
downloads are needed. If this preview is already running, simply open the URL.

- **Sources:** 13 admitted files; the May proxy is excluded by the March cutoff.
- **Canonical Data:** 1,305 facts, 281 contexts and 91 presentation sections;
  expand the data for dimensions, inline IDs, source hashes, mapped/unmapped
  observations, candidate passages and three subsidiary rows.
- **Metrics:** operating margin about 60.38% and revenue growth about 65.47%,
  both unverified and requiring review. Missing inputs remain unavailable.
- **Exceptions:** mapping review and a mandatory missing-metrics condition.
  The airline-oriented illustrative configuration is not NVIDIA policy.
- **Memo / JSON:** saved draft artifacts. Candidate passages are available
  to inspect, while generated AI analysis remains empty with the fake backend.

For a later-cutoff comparison, use a new case ID, for example `NVDA_2026_LATE`,
with `--cutoff 2026-06-01T00:00:00Z`; the proxy then passes its own gate and
its passages cite its own document. An existing case's cutoff cannot change.
See [SEC_INTEGRATION_REPORT.md](SEC_INTEGRATION_REPORT.md) for live SEC commands
and database migration. Older preview databases need that additive migration
before using the new schema.

## Create a case and upload a file

Open <http://127.0.0.1:8000/docs>. Expand `POST /cases`, click **Try it out**,
and submit:

```json
{
  "case_id": "MY_UPLOAD_TEST",
  "as_of_date": "2024-12-31",
  "evidence_cutoff_timestamp": "2025-01-15T00:00:00Z"
}
```

Then expand `POST /cases/{case_id}/documents`, enter `MY_UPLOAD_TEST`, choose
`examples/synthetic-case/operations.csv`, and set `available_at` to
`2025-01-10T00:00:00Z`. It should return a document ID and SHA-256 hash.
Changing availability to `2025-02-01T00:00:00Z` should return HTTP 422 because
the file was not available at the case cutoff. Creating the same case twice
should return HTTP 409.

This API-explorer endpoint stores evidence without running the pipeline. Use
the `/inspect` upload form above to create and process a new review case.

## Test the approval gate yourself

Run these commands from a second terminal while the preview runs. First try
finalizing an unapproved draft:

```powershell
.\.venv\Scripts\python.exe -m app.cli --database data/browser_demo.db --output output/browser_demo finalize SYNTHETIC_2024 --version 1 --reviewer your.name
```

Expected: `SignOffRequiredError`; the draft remains unfinalized. If you choose
to approve the synthetic full case after reviewing it, run:

```powershell
.\.venv\Scripts\python.exe -m app.cli --database data/browser_demo.db --output output/browser_demo approve SYNTHETIC_2024 --version 1 --reviewer your.name --reason "Reviewed the synthetic demo"
.\.venv\Scripts\python.exe -m app.cli --database data/browser_demo.db --output output/browser_demo finalize SYNTHETIC_2024 --version 1 --reviewer your.name
```

Refresh the homepage/workbench. Version 1 should now be frozen, the memo marked
final, and a human approval linked to it. Changing the content requires a new
version and approval. The missing/conflicting examples also require their
mandatory exceptions to be resolved; approval alone cannot clear that gate.

## What remains

- Live LLM quality and provider/model compatibility validation, after configuring
  credentials locally. HTTP integration and error handling are tested offline.
- Review the default case's actual conflicts, OCR uncertainties, input definitions
  and missing gross-interest/revolver facts before drawing credit conclusions.
- Binary memo PDF export needs an optional native rendering backend. The browser
  supports complete draft JSON and HTML without it.
- Shared deployment needs authentication, durable storage and deployment-specific
  migrations. The current interface remains local to this machine.

## Run automated checks

```powershell
$env:DEBUG = 'false'
$testTemp = Join-Path $env:TEMP ([guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q --basetemp $testTemp
```
