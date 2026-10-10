# HCA Healthcare FY2025 — Business Overview pilot (source filing NOT yet acquired)

**Status: blocked at source acquisition.** The HCA FY2025 Form 10-K has not been
downloaded into this workspace, so the real-HCA demonstration and the real-HCA
acceptance tests have **not** been run. Nothing in this folder contains HCA
filing data, and no HCA figure has been asserted anywhere in the repository.

What is here:

| File | Purpose |
|---|---|
| `package.json` | Source package pointing at `filing/bundle.json` (absent until fetched). |
| `review_manifest.json` | Reviewer evaluation manifest **template**, `unverified_pending_filing`. Concepts only, no figures. |

## 1. Acquire the filing (existing SEC path)

SEC EDGAR requires a contact e-mail in the User-Agent. Set it locally (never
committed), then list and fetch:

```powershell
$env:SEC_USER_AGENT = "Your Name you@example.com"
python -m app.cli sec-list HCA
python -m app.cli sec-fetch HCA --accession <accession of the FY2025 10-K> `
    --destination examples/healthcare/hca-2025/filing
```

`filing/bundle.json` then records, per file: CIK, accession, form, filing date,
report date, acceptance time, source URL and SHA-256. Copy the CIK, accession,
filing date and primary-document hash into `review_manifest.json`.

Confirm the evidence cut-off in `package.json` is later than the filing's
acceptance time (files available after the cut-off are rejected by admission).

## 2. Verify the manifest against the filing

For every `source_confirmed_facts` entry, replace `source_patterns` with a
`verified_quote` copied from the filing. Add `recomputed_values` from the
deterministic indicators in the audit report. Only then set
`verification_status` to `reviewer_verified`. The groupmate's
`HCA_Module1_Six_Agent_Credit_Analysis_v3.docx` may suggest topics; it is not a
source of facts.

## 3. Run the Business Overview

Offline pipeline check (asserts nothing, calls no model):

```powershell
python -m app.cli --database data/bo_demo.db --output output/bo_demo run-bo-case HCA_2025 `
    --package examples/healthcare/hca-2025/package.json `
    --manifest examples/healthcare/hca-2025/review_manifest.json
```

Live model run (incurs provider cost; requires `LLM_PROVIDER` and the Vertex or
OpenAI-compatible settings in `.env`):

```powershell
python -m app.cli --database data/bo_demo.db --output output/bo_demo run-bo-case HCA_2025 `
    --package examples/healthcare/hca-2025/package.json `
    --manifest examples/healthcare/hca-2025/review_manifest.json --live
```

The gated tests in `tests/integration/test_bo_real_filing.py` run automatically
once `filing/bundle.json` exists; until then they are skipped, which is not a pass.
