# Stryker FY2024 — Business Overview on a real filing (offline simulation)

Secondary regression borrower. This runs the complete BO-only path on the real
Stryker Corporation FY2024 Form 10-K (accession `0000310764-25-000023`), using
`offline_responses.json` in place of a model.

**What this is and is not.** `offline_responses.json` is a reviewer-authored
script: no model produced those answers, and every output of the run is labelled
*offline simulation*. What the run does prove is real: the filing is ingested
through the normal SEC path, every quote in the script is re-verified verbatim
against the admitted passages, every number is bound to a deterministic
calculation, and the orchestrator, challenger, targeted rerun, memo, audit and
handoff are actually executed and recorded.

The script references evidence by anchor phrase, so it resolves against whatever
evidence ids the case produces. A phrase that is not in the admitted filing
fails validation.

## Run

The bundle lives under the git-ignored `data/` directory. Fetch it once:

```powershell
$env:SEC_USER_AGENT = "Your Name you@example.com"
python -m app.cli sec-fetch SYK --accession 0000310764-25-000023
```

Then:

```powershell
python -m app.cli --database data/bo_demo.db --output output/bo_demo run-bo-case SYK_FY2024_BO `
    --bundle data/sec/SYK/0000310764-25-000023/bundle.json `
    --cutoff 2025-03-01T00:00:00Z --entity-id SYK --entity-name "Stryker Corporation" `
    --responses examples/healthcare/stryker-bo/offline_responses.json
```

Scripted storyline: BO3 first infers pricing power from the patent count; the
challenger raises a material finding; BO3, BO6, the orchestrator and the
challenger are rerun once; the corrected conclusion is accepted with one
remaining qualification. BO2 requests a follow-up search for customer
concentration that finds nothing, which is recorded as a retrieval gap.

This case does not use the private benchmark workbook.
