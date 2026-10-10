# Requirements — Medical-Device Industry Benchmarking Extension

Extends the agentic credit-analysis architecture
(`.kiro/specs/credit-memo-agentic-analysis/`) with sector benchmarking for
medical-device borrowers. Baseline: `spec/agentic-analysis` @ `18db2b9`.

Guiding rule, unchanged: code is the calculator, rule engine and scoring engine;
LLMs interpret, classify, challenge and synthesize.

## HB-1 Opt-in and isolation

1. Sector benchmarking runs only for a case whose `SourcePackage` declares
   `sector_benchmark`. A case without it produces the same snapshots, metrics,
   escalations, prompt inputs, scores and memo as before.
2. The `CanonicalEvidenceSnapshot` boundary and human sign-off are unchanged.
3. Everything works offline with fake LLM backends.

## HB-2 Workbook import and validation

1. The importer reads the original `.xlsx` bytes, never writes to the file, and
   records its SHA-256.
2. It imports the four worksheets, the six industry categories, row labels,
   formulas, cell references, definitions, caveats, source URLs and dates.
3. Every value has exactly one status: `observed`, `source_aggregate`, `derived`,
   `assumed`, `unavailable`, `invalid`.
4. It detects Excel error values, external-workbook dependencies, blank mandatory
   cells, implausible values, inconsistent formulas across industry columns,
   missing source dates and missing source documentation.
5. A cached formula result is used only if it is independently re-derived from
   the workbook's literal inputs and the two agree. A reference to an external
   workbook is re-pointed to the local sheet of the same name only when the
   link's cached value equals the local cell; the correction is logged.
   Otherwise the value is `invalid`. No defective formula is replaced by a
   constant.
6. Anything depending on the short-term-debt-share placeholder is `assumed` and
   is never eligible for comparison or presented as observed principal.
7. Output: an immutable, content-hashed normalized dataset and a
   machine-readable validation report. Every value is either eligible for its
   declared use or excluded with a reason; excluded values stay in the report.

## HB-3 Classification

1. Medical devices map provisionally to Healthcare Products.
2. A SIC code alone never decides the sector. Classification needs a CIK, a
   documented current SIC, and at least one corroborating signal.
3. Conflicting or diversified signals, or missing evidence, yield
   `requires_review` with no industry assigned, and benchmarking does not run.
   No revenue-weighted allocation is inferred.
4. Identity not read from SEC EDGAR for the case caps confidence.
5. A human may override with a rationale; the override is appended, audited and
   never edits the prior record.

## HB-4 Two benchmark methods

1. Method A, `industry_aggregate_damodaran_v1`: one validated aggregate per
   metric. Results carry no rank, percentile, median or peer distribution.
2. Method B, `empirical_rank_v1` (`PeerBenchmarker`), is unchanged, including
   small-sample suppression of P90/P95 and borrower exclusion. A cohort contract
   adds peer identity, period, definition, provenance and exclusion reasons.
3. An aggregate can never enter a peer cohort; no company-level observation is
   generated from the workbook.

## HB-5 Metrics and missing data

1. Existing `MetricEngine` metrics and `ParameterEngine` formulas are reused; new
   ratios use one guarded formula.
2. Outcomes are explicit: `ok`, `missing_input`, `requires_review`,
   `not_meaningful`, and (for a comparison) `not_comparable`. A missing input,
   zero denominator or negative EBITDA is never reported as zero or as a ratio.
3. DSCR stays `CFADS / contractual debt service`. Without those inputs it is
   unavailable. The workbook's proxy is never substituted or compared.

## HB-6 Comparability

Before comparing, check numerator, denominator, debt basis, EBITDA basis,
interest basis, lease treatment, balance basis, period basis, accounting basis,
units/currency, reporting period against data vintage, and whether the industry
figure is published, derived or assumed. Result: `comparable`,
`comparable_with_caveats`, `not_comparable` or `unavailable`, always with
reasons. An incompatible aggregate's number is withheld.

## HB-7 Configuration and scoring

1. A versioned, content-hashed sector configuration is registered through
   `ConfigRegistry`. It holds no benchmark values and may not define scoring keys.
2. No industry average is hard-coded in Python.
3. `config/poc/peers.json` is preserved.
4. Benchmark results never reach the scoring engine; official scores are
   identical with and without a sector benchmark.

## HB-8 Agentic integration

1. No new agents. The existing Financial Orchestrator receives a bounded
   `benchmark_context` of validated comparisons, compatibility flags, source
   metadata and accepted parameter-result ids — never the workbook.
2. Every number an agent may cite is an accepted deterministic
   `ParameterResult`; a number stated without a matching quote, a mismatched
   quote, an unknown evidence id, missing provenance, or any score/band/rating
   assignment rejects the whole answer.
3. Existing narrow agents receive their assigned medical-device risk dimensions
   as data and may return structured, evidence-backed observations that are not
   scores. Dimensions are optional per company.

## HB-9 Provenance, reproducibility, invalidation

1. Each comparison retains: workbook hash, sheet/cell, vintage, importer version,
   metric definition/version, borrower fact ids, method, sector config
   version/hash, analysis run id (if any), state, and exclusions with reasons.
2. Unchanged inputs give identical substantive outputs.
3. A changed workbook or mapping creates a new dataset version, supersedes prior
   comparisons for the case, and leaves unrelated evidence and prior artifacts
   unmodified.
4. A draft whose benchmark is stale or superseded cannot be finalized.

## HB-10 Memo and workbench

The memo's financial section shows a Healthcare Industry Benchmarking subsection
with borrower value, aggregate, comparison state, own-history direction, notes,
reference industry and date, methodology, limitations, missing data and source
lineage, and no percentile or rank. Comparisons are exposed through the existing
workbench and inspection paths.

## HB-11 Pilot

A reproducible Stryker case using real SEC filings where acquisition is possible,
otherwise a clearly labelled offline fixture, with the remaining real-source
steps documented.
