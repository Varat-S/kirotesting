# Module 3: sole supported workflow

Module 3 has two independent components: deterministic corporate-structure
conversion and filing-only senior-management assessment. The legacy specialist,
Validator 7, schemas, standalone experiments and selection fallback are removed.
No old M3 model job can be selected. The current `m3_validator_7` uses the compact
claim-level contract and does not restore the retired implementation.
Historical outputs are unsupported and may
only be inspected as such. See [the retirement report](m3-retirement-report.md)
and [the deletion/dependency inventory](m3-retirement-inventory.json).

## Run either component

Use an installed Python 3.11+ environment with pyproject dependencies. This local
worktree uses the existing environment without changing the original checkout:

```sh
cd /Users/Daniel/.codex/worktrees/m3-rebuild/kirotesting-evaluation
M3_PYTHON='/Users/Daniel/Desktop/SMU/AI in Fin/Credit Memo Testing/kirotesting-evaluation/.venv/bin/python'
"$M3_PYTHON" -m app.cli run-m3 HCA_2024 \
  --structure examples/m3_rebuild/hca_input.json --output output
```

Management offline requires a saved v2 facts-and-judgments response matching its
source IDs. The old hca_2024_extraction.json is a historical facts-only fixture
and cannot be passed as a current specialist assessment:

```sh
"$M3_PYTHON" -m app.cli run-m3 HCA_2024 \
  --filing '/Users/Daniel/Desktop/SMU/EYE Investment/ISP 1/HCA 10K/10K 2024.pdf' \
  --company 'HCA Healthcare' --filing-period 2024-12-31 \
  --extraction /path/to/saved-v2-response.json --output output
```

Supply both sets of arguments to run both components. Each run has its own folder
and deterministic manifest in `output/m3_rebuild/<case>/<run>`. Existing folders
are not overwritten. A valid sibling artifact is saved even if the other fails.
`python -m app.services.bo.m3_rebuild` remains the independent Python entry point.
The former `run-m3-rebuild` alias and old `run-m3 --responses/--live` specialist
syntax have been removed; `run-m3` now names only this two-component workflow.

## Normal orchestration

Every `BusinessOverviewRunner` run executes the same M3 workflow. Inputs can be
supplied in the normal source package's optional `module3` object:

```json
{
"sources": [
  {"filename":"corporate-structure.json","path":"corporate-structure.json","parser":"structured_json","tags":["m3","corporate_structure"],"available_at":"2025-01-10T00:00:00Z","entity_id":"BORROWER","scope":"consolidated","accounting_basis":"not_applicable"},
  {"filename":"selected-10k.pdf","path":"selected-10k.pdf","parser":"pdf_text","tags":["m3","management","form_10k"],"available_at":"2025-01-10T00:00:00Z","entity_id":"BORROWER","scope":"consolidated","accounting_basis":"GAAP"}
],
"module3": {
  "corporate_source":"corporate-structure.json",
  "management_source":"selected-10k.pdf",
  "company":"Example Corp",
  "filing_period":"2024-12-31",
  "extraction":null
}}
```

`module3` selects sources already declared in the package; it is not a private
file-input channel. Case preparation admits the JSON as typed structured evidence,
captures the selected filing's narrative passages, and applies the ordinary cutoff,
document identity and hash controls. After the deterministic foundation, the shared
router persists one M3 evidence packet. Corporate conversion consumes only the
packet's admitted payload, and management consumes only routed canonical passages.
Normal BO commands have no direct M3 file or manifest flags.

Pass a saved decoded v2 response in `module3.extraction`, or inject an explicitly
authorized shared LLMBackend as `BusinessOverviewRunner(..., m3_backend=backend)`.
Both pass through the same AgentRuntime/LLMClient schema and audit boundary.
The `m3_extractor` adapter is for existing offline fixture tests; the old direct
Vertex extractor rejects execution. A selected filing alone never silently
triggers paid execution. Missing or rejected sources produce precise independent
component statuses; they never invoke the old specialist. `run-m3` remains the
standalone interface for running either component. Previously generated rebuild
manifests remain readable through the compact `synthesis_block`/registry artifact
adapters, but are historical artifacts and cannot bypass admission in a new normal
BO run.

## Evidence and coverage

The three-year window is inclusive, ending at the filing's disclosed roster date.
Partial dates remain intervals; boundary uncertainty never invents a day/month.
Python calculates tenure intervals but assigns no qualitative direction. Gemini
identifies facts and judges their credit implications in one specialist call.
Neutral and insufficient-evidence conclusions are valid; tenure alone cannot
establish competence. Mixed evidence has no net score or confidence rating.
Facts and judgments remain separate, with judgment-to-fact-to-canonical-citation
references. Missing information is distinct from management concerns.

A prior internal role needs employer evidence: role-local company attribution,
explicit company possessive/internal entity, or supported dated biography scope.
Generic 'previously served as' alone is insufficient. Outside-employer attribution
cannot create mitigation. Historical recruitment from outside does not make a
later internal promotion external. Rejected facts are retained in the raw/parsed
extraction and precise rejection record; the affected profile is held back,
coverage records the missing profile and status becomes partial. This conservative
implementation now rejects individual claims. A bad role citation does not reject
an independent appointment or fact. Matching can normalize traceable whitespace,
typography and allowlisted PDF-split words; original source text and offsets remain
available. Wrong passage IDs, names, dates, numbers, titles or negations still fail.

Inject a separately authorized `m3_reviewer_backend` to execute Validator 7 through
the shared BO runtime. For this milestone, use only offline fixtures. Each judgment
gets an accepted/qualified/unsupported decision, explicit supporting claim IDs,
reason and separate limited wording if qualified. Overall assessments use surviving
judgments. Missing/invalid review preserves facts but withholds qualitative output.
The review scope is three principal individuals plus the management team, subject
to disclosures; a seven-person individual-judgment quota is not imposed.

Roster discovery recognizes bounded name/age/position tables with dotted or
whitespace separators and conservative position continuations. It reconciles names
against biography starts across pages. Unknown table content, duplicated rows,
missing biographies or unmatched biography names make coverage unverifiable and
status partial while preserving confidently extracted profiles. Unknown total
roster size is null, not a falsely precise count. Scanned/complex layouts require
upstream OCR or review; universal PDF layout recognition is not claimed.

## Actual downstream memo boundary

Both contracts reach BO synthesis/handoff as `module_conclusions.M3.m3_rebuild`.
Dedicated management claims reference observation IDs and citation IDs; no legacy
hypotheses or subjective governance scores are fabricated. The SO adapter and
`build_partial_assessment` / `render_partial_html` include the supported statements
and a source appendix. Typed validators reject unsupported text, observation IDs,
references and directions, even if a caller recomputes the content hash.

The available integrated output is an **incomplete BO/FO partial credit memo**.
It has no final rating, facility approval or completed credit decision. Legal,
transaction and human-review dependencies remain explicit. Simulated corporate
assumptions retain their disclaimer and never become verified legal relationships.
The old management/governance rubric input is not fabricated; unchanged score
policy may leave the BO business score unavailable pending a separate policy
revision. FO and M4 numerical rules remain unchanged.

## Registry and offline checks

The existing registry/viewer discovers independent runs and records their status,
source identity and payload hashes. Historical M3 JSONs, if encountered, are
labelled `unsupported_legacy` and skipped, never reinterpreted. Current artifacts
remain consumable through their existing versioned manifest and file contracts.

```sh
PYTHONDONTWRITEBYTECODE=1 "$M3_PYTHON" -m pytest -q -p no:cacheprovider
```

## Milestone 3 single-request test

This section records historical Milestone 3 results. The current claim-level replay
and offline Validator 7 result are documented in [Milestone 4](m3-milestone4-report.md).

`python -m app.services.bo.m3_rebuild.credit_test --directory <fresh-folder>
--filing <selected-PDF> --corporate <submitted-JSON>` runs normal case preparation,
source admission, canonical snapshot creation and routing with zero model requests.
An explicit `--authorize-one-paid-request` uses ADC and exactly the configured
gemini-3.5-flash-lite; its persistent reservation refuses a second attempt,
including after failure. SDK attempts=1 disables paid retries. All other BO
agents use offline empty responses. `--saved-response <raw-response>` performs
an offline replay through the same normal BO path.

The Milestone 3 authorization has been consumed: one request, 9,056 input and
9,509 output tokens, estimated USD 0.0264893 at user-supplied rates. The raw answer
named seven executives and produced model credit judgments. Original evidence
validation failed on an overly broad roster-date gate. After that objective
scoping fix, an offline replay remained partial with zero admitted profiles;
quoted source/role/identity checks were preserved. No further paid call is
authorized. See [the Milestone 3 report](m3-milestone3-report.md).

The older two-request gemini-3.1-flash-lite batch is closed historical work,
documented in the retirement report. Its private execution harness is retired.

## Milestone 4 offline replay

Use the normal case-preparation test harness with `--saved-response` and
`--review-fixture examples/m3_rebuild/hca_validator7_m4_offline.json`; omit the paid
authorization flag. Every other BO agent also stays offline. The original raw
response is preserved. `m3_rebuild.comparison` generates a readable HTML and JSON
claim/citation comparison against a prior saved assessment, refusing changed raw
response bytes. See the Milestone 4 report for exact artifact paths and limitations.
