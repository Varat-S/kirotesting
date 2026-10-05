# Default Delta FY2025 test case

The three supplied PDFs are preserved byte-for-byte and checked against
`sha256.json`. The XLS alternative is intentionally excluded.

| Document | Pages | Capture |
| --- | ---: | --- |
| FY2025 Form 10-K | 105 | Native text, expanded statements and cash note |
| Q4/FY2025 earnings release | 21 | Native text, statements and operating statistics |
| Non-GAAP supplement | 6 | Windows English OCR, selected adjusted measures |

`package.json` declares source availability, periods, units and statement recipes.
`ocr_capture.json` preserves the actual OCR output and word boxes, bound to the
original PDF hash and a payload checksum. The default reuses this checked capture
for reproducibility across machines; generic scanned uploads run OCR locally.
Disable the cache in a separate package to rerun OCR. OCR results stay unverified.

`reference_values.json` contains source-page/period/unit references and expected
metrics. `ocr_reference.json` transcribes 185 numeric cells from all six rendered
scans. Page-level numeric matching recognizes 182/185 cells; this does not verify
labels, column semantics, text accuracy or all narrative numbers. The three
unrecognized cells are 3,424 on page 3 and 2.4x / (52)% on page 4. Full raw text
and numeric-row candidates are preserved even when not mapped to canonical facts.

The refreshed case is `DELTA_2025_OCR_REVIEW`, as of 2025-12-31 with cutoff
2026-03-01 UTC. Earlier cases are retained. Availability follows conservative
end-of-day filing/release dates; the non-GAAP publication date remains provisional.
The [official 10-K index](https://www.sec.gov/Archives/edgar/data/27904/000002790426000013/0000027904-26-000013-index.htm)
and [release index](https://www.sec.gov/Archives/edgar/data/27904/000002790426000008/0000027904-26-000008-index.htm)
support the stated filing dates.

There are 358 observations and nine calculated metrics. Derived definitions
require review: debt is carrying-value debt/finance leases, EBITDA is unadjusted
operating income plus depreciation/amortization, capex is flight plus ground
cash additions, and cash excludes separately disclosed restricted cash. These
are distinct from Delta's adjusted debt, EBITDAR and adjusted FCF measures.
Actual audited/unaudited balance-sheet differences are preserved as conflicts.
Gross-interest coverage and structured revolver-based liquidity remain unavailable.

Run the local interface:

```powershell
$env:DEBUG = 'false'
.\.venv\Scripts\python.exe -m app.preview --database data/sec_demo.db --output output/sec_demo --port 8001 --seed-default
```

Open `/inspect/DELTA_2025_OCR_REVIEW/1`. Preparing the case makes no model calls
or approvals. Review controls create new versions; draft generation is explicit.
