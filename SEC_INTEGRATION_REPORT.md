# SEC / Inline-XBRL integration

Implemented locally on `feat/sec-inline-xbrl-integration`, starting from
`fix/credit-memo-hardening` at `936bf9876f730a31595dc850f518baa37b3f6f80`.
The supplied `AI-Credit-Memo-main.zip` and handoff were readable. Its parser was
inspected without importing or running the Flask application.

## Architecture and ported behavior

The existing `CreditMemoPipeline`, FinancialMapper, reconciliation, metric
engine, AI validation, audit, review, finalization and memo rendering remain the
processing architecture. The standalone `XbrlParser` remains available.

| Groupmate behavior | Integrated location / contract |
| --- | --- |
| `valid_user_agent`, `edgar_get` | `app/services/acquisition/sec_edgar.py`: contact identification, injected transport, bounded responses, gzip, timeout, retries, rate limiter and request metadata |
| `lookup_ticker`, `list_filings`, `annual_reports`, `list_annual_reports` | Ticker/CIK lookup; recent and historical submissions; explicit annual/amendment distinction; `sec-list` CLI |
| `proxy_for`, `download_proxy` | Date-based association; separate accession and metadata; associated proxy still passes its own cutoff |
| `select_filing_files`, `download_filing` | Primary, local taxonomy/linkbases and HTML exhibits; complete bundles published atomically; no existing destination overwritten |
| `locate_files`, `load_filing` | Explicit `SecFilingBundle` and local manifest; directory layout does not define parser state |
| `parse_contexts`, `parse_units`, `parse_facts` | `extraction/sec/contexts.py`, `inline_xbrl.py`: periods, entity identifiers, explicit/typed dimensions, units, numeric/text observations and continuations |
| `load_labels`, `load_role_definitions`, `load_presentation`, `build_sections` | `linkbases.py`: labels, ordered presentation sections and observation lineage, used as diagnostics |
| `document_text`, `split_items`, `find_excerpts`, overview/heading retrieval | `xml.py`, `narrative.py`: readable text, SEC Items and bounded candidate passages with source/location; keyword topics are retrieval hints |
| `find_proxy`, `parse_proxy`, `add_proxy_to_overview` | Explicit bundle role and `proxy.py`; proxy passages join candidate evidence with their own document identity |
| `find_exhibit21`, `parse_exhibit21` | Explicit role/filename and heading fallback; header rows filtered; names/jurisdictions retained as unverified subsidiary evidence |

This is a refactoring of capabilities, with new evidence/security contracts;
it is not a verbatim copy of every original helper. HTML overview builders do
not generate final credit conclusions. Flask routes, background jobs, sessions,
cached UI state, templates and static assets were not merged. The original
dashboard remains in the ignored local source extraction under
`data/sec_merge_source`; it is not a runtime dependency. The existing FastAPI
workbench provides inspection, including a SEC diagnostic summary and raw data.

## Evidence controls

- Downloading is distinct from admission. Every primary, XSD, label,
  presentation, calculation, definition, proxy and exhibit file independently
  enters the existing ingestion service. Rejected future evidence is logged
  and never parsed or supplied to AI.
- Raw bytes are hashed and stored unchanged. File metadata retains CIK,
  accession, form, filing/report dates, official acceptance timestamp when
  available, retrieval time, source URL and availability granularity.
- An official acceptance timestamp gives precise availability. When only the
  filing date is known, availability is the **next midnight in US Eastern**,
  converted to UTC. It does not assert an exact publication time. Bundle
  validation rejects earlier availability, naive timestamps, conflicting CIKs
  and mismatched primary metadata. Retrieval time does not decide eligibility.
- Facts and source references carry context ID, inline ID, accession and
  presentation role. Facts retain a sorted dimension identity and raw text;
  typed dimensions retain the typed domain and attributes, not just its text.
- Reconciliation groups by entity, fiscal year, period type, end, canonical
  field, dimensions and start. Consolidated, Gaming and Data Center revenue
  stay separate. Core metrics accept dimensionless consolidated facts by
  default. Dimensional evidence remains available for inspection.
- Transform/scale/sign produce the reported amount once. The adapter then
  converts currency base units to millions once. Pure units remain ratios;
  shares and divided units retain their meaning. Nil is missing, never zero.
  Unknown transforms and nonfinite amounts retain raw evidence and warnings
  without inventing a normalized value.
- Parsing creates unverified facts. Repeated tags and re-downloads of one SEC
  accession do not provide independent corroboration. Conflicts remain
  conflicts. A deliberate second compatible source can still enter the
  existing reconciliation workflow.
- Unknown concepts use explicit mapping review. The added mappings cover
  standard operating cash flow, property/plant/equipment acquisition,
  interest expense and noncurrent long-term debt. They do not infer EBITDA,
  unrestricted cash or total debt from loosely related concepts.
- SEC Item passages and proxy topics are unverified candidates. Analysis
  receives them as snippets. Their citation IDs are recognized by grounding;
  citation presence does not prove entailment. Without a narrative judge or
  human adjudication, narrative entailment remains not verifiable.
- Exhibit 21 headers are not subsidiaries. Subsidiary names do not create
  borrower or guarantor roles. Human approval and mandatory escalation gates
  remain in force; viewing a workbench makes no approval or audit writes.

New audit event types cover filing discovery, downloads, admission, inline
parsing, narrative extraction, proxy association and subsidiary extraction.
Existing rejection and mapping-review events are reused. Acquisition failures
expose request metadata; successful CLI acquisition commits its audit events.

## Real NVIDIA fixture and observed result

The checked-in [fixture](examples/sec/nvda-2026/README.md) preserves the supplied
ZIP's filing bytes with manifest SHA-256 hashes. The main accession is
`0001045810-26-000021`, filed **2026-02-25**, reporting **2026-01-25**.
The supplied proxy has accession `0001045810-26-000036`, filed **2026-05-12**.
No timezone was guessed for the index page's displayed acceptance time: this
offline fixture explicitly uses conservative filing-date granularity.

At cutoff **2026-03-01T00:00:00Z**, the actual pipeline produced:

| Observation | Result |
| --- | --- |
| Admitted files / rejected files | 13 / 1; the future proxy is rejected |
| Contexts | 281 |
| Canonical observations | 1,305: 1,127 numeric, 102 text, 76 text blocks |
| Presentation sections | 91 |
| Candidate narrative passages | 156 |
| Mapped numeric observations | 77 |
| Unmapped numeric observations requiring review | 1,050 |
| Subsidiaries after filtering headers | 3; only NVIDIA is registered as the borrower |
| FY2026 consolidated revenue | USD 215,938 million |
| FY2026 operating margin | 0.6038168363, about 60.38% |
| FY2026 revenue growth over FY2025 | 0.6547353579, about 65.47% |
| Outputs | Draft JSON and HTML; no automatic approval/finalization |

Revenue uses the filing's 52/53-week annual contexts, rather than assuming
December 31. Both available metrics require evidence review. Nine other
configured metrics remain unavailable: cash conversion, capex/revenue, CASM,
free cash flow, interest coverage, liquidity, load factor, net debt and
net debt/EBITDA. The missing-input condition is a mandatory escalation.
The illustrative configuration contains airline metrics and peers; its policy
outputs are not a validated NVIDIA credit policy. Issuer extensions remain
visible rather than being guessed into canonical fields.

## Schema and database compatibility

`CanonicalFact`/`Fact` gain `dimensions`, `xbrl_context_id`,
`inline_element_id`, `sec_accession`. `SourceRef`/`FactSourceRef` gain context,
inline, accession and presentation role. The extraction enum and precedence
include `inline_xbrl`.

Snapshot schema **1.1** adds `sec_filings` and `narrative_evidence`, and validates
the dimension/context/inline fields in fact JSON. The original **1.0** JSON
schemas are retained separately in `app/schemas/legacy/snapshot_1_0.json` and
shipped as package data. Historical stored JSON is not rewritten.

New databases need no migration. **Back up existing SQLite databases**, then:

```powershell
Copy-Item -LiteralPath data/browser_demo.db -Destination data/browser_demo.before-sec.db
.\.venv\Scripts\python.exe -m app.migrate --database data/browser_demo.db
```

The additive, idempotent migration supplies `{}` dimensions and null new
provenance fields for old rows, retaining evidence, audit, review and frozen
snapshot bytes/hashes. Stop a running preview before backing up/migrating its
database. The migration is for SQLite; PostgreSQL deployment migration is a
separate task. New configuration content creates new registry versions;
existing pinned versions are preserved.

## How to run locally

Use the locked dependencies, including **lxml 6.1.3**:

```powershell
uv sync --locked --python 3.11 --extra dev
$env:DEBUG = 'false'
.\.venv\Scripts\python.exe -m app.cli --database data/sec_demo.db --output output/sec_demo run-sec-case NVDA_2026 --bundle examples/sec/nvda-2026/bundle.json --entity-name 'NVIDIA Corporation' --cutoff 2026-03-01T00:00:00Z
.\.venv\Scripts\python.exe -m app.preview --database data/sec_demo.db --output output/sec_demo --port 8001
```

Open <http://127.0.0.1:8001/> and choose `NVDA_2026`. Sources lists admitted
files; Canonical Data shows diagnostic counts, contexts/dimensions/units,
labels, presentation, mapped/unmapped facts, passages and subsidiaries in its
expandable data panel. Metrics distinguishes calculated values from evidence
quality. Exceptions shows missing inputs and mapping review. Memo and JSON
open the saved draft. Local bundles need no contact setting or network.
Repeating the pipeline command creates a new draft version, leaving earlier
versions intact.

## Optional live SEC acquisition

[SEC's API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
states that authentication/API keys are not required. Configure your own real
contact identification, not a bearer token:

```powershell
$env:SEC_USER_AGENT = 'Your Name your.real.email@example.com'
.\.venv\Scripts\python.exe -m app.cli sec-list DAL --include-amendments
.\.venv\Scripts\python.exe -m app.cli sec-fetch DAL --fy 2025 --with-proxy --destination data/sec/DAL-FY2025
.\.venv\Scripts\python.exe -m app.cli --database data/dal_sec.db --output output/dal_sec run-sec-case DAL_2025 --bundle data/sec/DAL-FY2025/bundle.json --entity-name 'Delta Air Lines, Inc.' --cutoff 2026-03-01T00:00:00Z
```

Direct acquisition plus processing is also available through `run-sec-case
DAL_2025 --ticker DAL --fy 2025 --with-proxy --cutoff
2026-03-01T00:00:00Z`. A live run requires exactly one of `--fy` or
`--accession`; choose an accession from `sec-list` if the fiscal-year hint is
ambiguous. Annual amendments require explicit accession selection. Each
download destination must be new. `sec-fetch` does not admit evidence.

The client uses 30-second timeouts and at most two retries after the initial
attempt. HTTP 429 honors numeric/date `Retry-After`; HTTP 403 fails clearly
without repeated requests. Each client limits requests to eight/second,
below the [SEC's published limit](https://www.sec.gov/about/developer-resources).
Coordinate concurrent clients to keep aggregate traffic within SEC limits.
Only SEC HTTPS hosts/redirects are accepted. Contact identification improves
compliance with access rules but does not guarantee access from every network.
Live SEC downloads have **not** been tested with the user's actual contact
identity; automated tests use injected transports and block production SEC I/O.

## Verification and CI

Before: **375 passed, 1 skipped**. After: **446 passed, 1 skipped** on both
Python **3.11.15** (fresh locked environment) and **3.13.3**. The skip remains
optional native PDF rendering; JSON/HTML is tested. A pre-existing
Starlette/httpx deprecation warning remains. There are **71 new SEC tests**.

Coverage includes transformations/units/nil/continuations, explicit and typed
dimensions, exact source references, labels and presentation, contexts and
instant/duration periods, unsupported and overflowing amounts, XXE/network
entity isolation, path/hash/admission validation, rate limiting/retries,
historical submissions/amendments/proxy dates, interrupted downloads,
independent file cutoffs, same-day availability, subsidiary header filtering,
schema/migration preservation, mocked acquisition plus CLI processing, and
the real NVDA-to-memo/browser workflow. Two fresh real-fixture runs produce
identical canonical evidence and memo JSON. Escalations are serialized in
stable identifier order so timestamp resolution cannot alter memo content.

```powershell
$env:DEBUG = 'false'
$testTemp = Join-Path $env:TEMP ('sec-tests-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q --basetemp $testTemp
```

The existing GitHub Actions workflow still uses `uv sync --locked` and pytest
on Python 3.11/3.12/3.13. The new tests are automatically included. Remote CI
is verified separately after pushing; local results do not establish its
status. Python 3.12 and the Linux runner have not been exercised locally.

## Deferred work and Delta readiness

Calculation/definition linkbases are downloaded, independently admitted,
hashed and listed as deferred diagnostics; they are **not** used for roll-up
validation or dimensional semantics. No external taxonomy is fetched by the
XML/XSD parser. Limits are 32 MiB/file, 128 MiB/bundle, 100,000 facts and
bounded continuation/presentation traversal. XML entities are disabled;
downloaded HTML is not executed and workbench text is escaped.

This is a bounded Inline-XBRL parser, not a complete XBRL processor. It does
not handle `ix:fraction`, multi-primary-document inline document sets, the
entire transformation registry, or full taxonomy validation. Unsupported
numeric transformations remain warnings with unavailable values. Narrative
retrieval uses deterministic chunk/keyword heuristics and needs further
heading/table validation. An acquisition interrupted before publication does
not leave a complete-looking bundle folder; valid downloaded bundles remain
inspectable when later evidence admission rejects individual files.

A real Delta annual filing is now ready for the same acquisition/local-bundle
and controlled pipeline path. Next select its exact accession and cutoff,
manually verify a small financial ground-truth manifest, inspect extensions,
GAAP/non-GAAP EBITDA, unrestricted cash, debt totals, capex and operating
measures, then add reviewed mapping/config versions. The NVDA result does not
prove Delta extraction accuracy or airline credit-policy validity. The real
LLM backend, retrieval/token budgeting, narrative entailment judging, browser
processing/review controls, authentication/deployment, native PDF and OCR
remain separate integrations.
