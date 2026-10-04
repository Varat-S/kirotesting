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

| Case | What to inspect | Expected behavior |
| --- | --- | --- |
| `SYNTHETIC_2024` | Sources, metrics, exceptions, memo | Eight source files; operating margin 0.2 (20%); net debt / EBITDA 3x. Some operating metrics have weaker evidence and require review. Memo remains draft. |
| `SYNTHETIC_MISSING` | Missing sources and unavailable financial metrics | Critical financial statements are absent. Financial results remain unavailable; exceptions require human attention. |
| `SYNTHETIC_CONFLICT` | Revenue observations, metric quality, mandatory exceptions | An additional source reports different revenue. Dependent metrics such as operating margin are unavailable; the conflict is preserved and blocks finalization. |

Use the nine section links and expand the data panels to inspect fact IDs,
source references, selected financial values, and configuration versions.
Metric results distinguish calculation status from evidence quality. Ratio
fractions are displayed as stored: 0.2 means 20%. Open **Memo** for the rendered
document or **JSON** for its canonical payload.

Viewing any page is read-only: it creates no approval, finalization, or audit
event. The offline fake AI returns empty analysis rather than simulated credit
judgments, so narrative sections will be empty. All bundled examples are
synthetic, and the configuration is illustrative rather than bank policy.

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

Uploading stores evidence; it does not run the pipeline. Processing requires
a source package with entity, period, unit, scope, and extraction metadata.
An upload-only case has no calculated draft until that step runs.

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

1. **Real-source validation:** run one actual Delta filing with a reviewed
   package and ground truth, then address demonstrated XBRL/PDF/unit/period
   gaps. Synthetic tests do not prove accuracy on a real filing.
2. **Real LLM integration:** implement the provider backend, connect provider
   and model settings, configure credentials, timeouts/retries/cost limits,
   and evaluate actual extraction, analysis, and challenge responses. Existing
   schema validation, logging, grounding, and deterministic arithmetic remain
   the acceptance boundary.
3. **Interactive workflow:** add browser controls for source-package setup,
   processing, reviewing/resolving exceptions, and approval/finalization.
   Current inspection works in the browser; write workflows use API/CLI/services.
4. **Optional document backends:** install native PDF libraries for binary
   PDF export and Tesseract for OCR, then test real image-only documents.
5. **Deployment work:** authentication/authorization, a reviewed deployment
   database migration, durable storage, and stronger storage-level immutability.
   A shared remotely hosted service has not been deployed.

## Run automated checks

```powershell
$env:DEBUG = 'false'
$testTemp = Join-Path $env:TEMP ([guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q --basetemp $testTemp
```
