# Delta document validation and workflow implementation

The three supplied PDFs have been processed successfully. All 132 pages yield retained text, including six OCR pages. The majority of their textual and numeric content is captured. This does **not** mean the majority of every semantic financial field has been mapped or independently verified.

## Measured document coverage

| Document | Readable pages | Text characters | Numeric tokens | Structured observations |
| --- | ---: | ---: | ---: | ---: |
| DeltaAirlines_10k_2025.pdf | 105/105 | 353,492 | 4,528 | 163 |
| DeltaAirlines_EarningsRelease_Q4_2025.pdf | 21/21 | 52,302 | 1,359 | 164 |
| DeltaAirlines_Non-GAAPs_Q4_2025.pdf | 6/6 | 15,916 | 286 | 13 |

The two native-text PDFs retain 100% of their non-whitespace text glyph inventory (299,519 glyphs in the 10-K; 44,079 in the release). This checks character occurrence retention, not layout, diagrams, reading order or semantic completeness. Every captured page is reproduced fully in saved candidate passages; no page text is truncated at the LLM preview boundary.

The scan was actually OCRed on this machine using Windows.Media.Ocr, English, with PDF rendering at 3.5x scale. Its preserved OCR capture includes all word boxes and is bound to the original PDF SHA-256 and a payload checksum. The default fixture reuses that checked capture for repeatable tests; new scanned uploads invoke Tesseract, if installed, or Windows OCR. Engines are never presented as human verification.

## Source and OCR reference checks

All **71/71** explicit source-page/period/unit and metric reference checks pass. The references are in `examples/delta-2025/reference_values.json`.

All six scanned source pages were rendered and visually inspected. `ocr_reference.json` transcribes 185 numeric table cells, excluding dates, narrative numbers, footers and dash cells. A page-level occurrence check recognizes **182/185 (98.4%)**, including signs and duplicate counts. Per-page results: 23/23, 41/41, 19/20, 35/37, 35/35, 29/29. This checks numeric recognition, not labels, periods or table relationships.

Unrecognized/malformed OCR cells: 3,424 (page 3, rendered as `3 424`), and 2.4x / (52)% (page 4, missing from captured text). Some labels and percent symbols also have OCR spelling errors. Original scans remain available through source/page links. Raw page text, numeric-line candidates and word boxes are preserved even when values are not canonicalized.

## Implemented behavior

- Expanded GAAP statement extraction, restricted-cash reconciliation, debt components, capex components, depreciation, net interest, cash interest paid, RPM/ASM, fuel gallons and reported unit costs. There are **340 source observations plus 18 explicit derived observations**, all mapped; GAAP and adjusted figures reconcile in separate groups.
- Versioned definitions produce nine of eleven metrics. Derived inputs retain component IDs, source references and definition hashes. Debt means current/noncurrent carrying-value debt and finance leases; cash capex excludes strategic investments; EBITDA is an unadjusted operating-income-plus-D&A proxy; available cash excludes separately disclosed restricted cash. These definitions require human review. Seven calculated metrics remain marked for review.
- Real balance-sheet differences between the release and audited 10-K remain visible, including total assets 81,185 versus 81,317. They are not silently selected.
- Separate extraction/mapping outputs and complete document captures are saved during processing. Payload hashes and a chained record are verified on reads; original-source downloads verify their raw SHA-256. New Delta preview has 15 recorded downloadable artifacts. Hashes detect changes; they do not establish financial accuracy or approval.
- Browser review creates new evidence versions and recalculates metrics. Earlier snapshots, original observations, values, decisions and reasons remain available. Conflict selection, mapping review, verification/definition review, exception resolution, content-bound approval, and finalization are supported. Superseded evidence/drafts cannot be approved or finalized.
- Saved previews continue directly into qualitative extraction, analysis and challenge, without reparsing sources. Offline mode uses three empty scripted outputs. Explicit regeneration creates a new draft/model run; repeated submissions without that choice are rejected.
- OpenAI Responses and OpenAI-compatible Chat Completions are implemented, with strict structured output, input limits, timeouts, no automatic retries, sanitized errors, and recorded requests/responses. Refusals, incomplete/invalid responses, invalid citations, missing PDF pages and unsupported claims remain subject to validation/grounding and review gates. The API follows the [official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

## Remaining limits

Zero unmapped structured observations does not mean zero conflicts or complete semantic extraction of the raw page content. The original saved case has eight unresolved balance-sheet field conflicts for December 31, 2025, with all amounts in USD millions:

| Field | Earnings release | Audited 10-K |
| --- | ---: | ---: |
| Deferred income taxes, net | 3,369 | 3,444 |
| Other accrued liabilities | 1,988 | 1,945 |
| Other noncurrent assets | 4,291 | 4,421 |
| Total assets | 81,185 | 81,317 |
| Total current assets | 10,966 | 10,968 |
| Total current liabilities | 27,667 | 27,624 |
| Total liabilities and stockholders' equity | 81,185 | 81,317 |
| Total noncurrent liabilities | 32,765 | 32,840 |

These are eight conflicting fields, including the two matching balance-sheet totals, rather than eight independent economic discrepancies. Source values remain preserved for explicit reviewer selection. Seven calculated metrics need review, and source-profile coverage warnings remain visible; these warnings do not establish that the corresponding information is absent from the captured documents.

Gross interest expense is not supplied as a reviewed structured input; net interest and cash interest are deliberately distinct. The 10-K revolver availability narrative is captured but not a structured canonical input. Interest coverage and liquidity therefore remain unavailable. Missing structures do not mean the source lacks the information.

Live provider execution and narrative quality have not been tested with real credentials. Set a provider, explicit model and key locally in the ignored `.env` file, then restart the preview and choose live mode. A local compatible model can use a localhost URL without an API key. Mocked HTTP tests cover request construction, failure handling and schema validation. No paid model calls or human approvals were made on the actual Delta case.

Binary memo PDF export still requires an optional native rendering backend; draft JSON and HTML work without it. OCR remains unverified, the supplement publication date remains provisional, and a complete semantic mapping of every table/note/diagram is outside what these coverage counts establish. The supplied XLS alternative was intentionally excluded.

## Validation and local testing

Automated checks: **519 passed, 1 skipped** (58.48 seconds). The skip is optional binary memo PDF export because its native backend is absent. A follow-up focused test checks hash repeatability across identical reprocessing. Scoped correctness lint and whitespace checks pass. Two unrelated pre-existing unused-name lint findings remain in the evaluation/XBRL modules; this pass did not change them.

The running local interface was exercised with read-only requests: all source downloads match original hashes, all 15 artifact downloads match recorded hashes, every page is fully represented in passages, and review/draft controls render.

Open <http://127.0.0.1:8001/inspect/DELTA_2025_OCR_REVIEW/1>. Start with source/page links and the reference-check artifact, then try review controls or offline draft generation. The original Delta case remains unapproved and has zero model calls. Use the rerun-default button for an isolated test case.

Exact measured results: `output/sec_demo/DELTA_2025_OCR_REVIEW/validation.json`.
