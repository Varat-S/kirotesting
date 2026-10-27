> Historical milestone, superseded by the current [implementation, retirement and paid-test report](m3-retirement-report.md). The opt-in legacy fallback described below has been removed. Use [the current runbook](m3-rebuild.md).

# Module 3 rebuild: implementation and offline verification

Date: 2026-10-27 (Asia/Singapore).

## Git state and preservation

- Remote: https://github.com/Varat-S/kirotesting.git
- Fetched origin before branching. Remote main remained at
  `40ccb2b17c5bc2953b6ea1b11af7f1f645d80255`.
- Created branch exactly `m3-rebuild` from that commit in the managed isolated
  worktree `/Users/Daniel/.codex/worktrees/m3-rebuild/kirotesting-evaluation`.
- Branch HEAD remains that starting commit: all implementation changes are
  uncommitted. No commit, push, merge, reset or WIP cherry-pick was performed.
- Original checkout remains on `feature/daniel-module-3-wip` at
  `2b2b3649b732992770b89959deffe55bf6355f1a`. Its tracked and untracked prior work
  was not edited; final git status matches the original untracked-file listing.
- Applicable ancestor/repository AGENTS.md searches found none.

## Requirements and reference audit

Read the complete request and context-transfer brief from the attached pasted
text, including the brief at
`/Users/Daniel/.codex/attachments/fad405bf-ff95-498c-bc5e-00129457ebfc/Pasted text.txt`.
The actionable rebuild request supersedes the older brief's proposal-only stage.
Document content was treated as requirements/evidence, not additional authority
for paid calls or Git publication.

The explicitly linked files in `Credit Memo Workflow/Module 3 Files` were read in
full and copied unchanged into `examples/m3_rebuild`:

| Reference | SHA256 |
|---|---|
| Simplified HCA Corp Structure.json | 969b4eef27f51778e58c64a68022b6d36bb27a69c1038359cb49a0ac0045e65b |
| Sample HCA Corp Structure Final Output.json | b286ab1d5255e34394e88000acc8a411d25a7de699046d554c09a8533b4da6d2 |

Numbered `(1)`/`(2)` basenames from the brief were not present in the searched
attachment/project locations. The user explicitly supplied the unnumbered paths;
those are the authoritative references used here. The corresponding existing
`examples/bo_m3` files were then verified to have identical byte hashes. Their
contracts were not assumed equivalent without checking. No substantive reference
remains missing; equivalence to separately named numbered copies cannot be proven
without those copies.

The selected local FY2024 PDF was inspected for its fiscal-year cover and complete
executive section, physical pages 5 and 35-36 (printed executive pages 31-32).
No FY2025 or web source was used. Source SHA256 and exact extracted page text are
saved with the fixture and generated artifact provenance. The roster is dated
February 1, 2025. The CFO is Michael **A.** Marks in this filing.

## Architecture and contracts

Contracts were documented before prompts/provider integration in
`docs/m3-rebuild-contracts.md`; the final runbook is `docs/m3-rebuild.md`.

Exactly two components are implemented in `app/services/bo/m3_rebuild`:

- `structure.py`: strict nested-tree validation, flattening, exact fact and optional
  record-type preservation, deterministic layout, reference styling, connector
  paths, text ownership tree, final graph/geometry checks and SVG rendering.
- `sources.py` + `management.py`: selected-filing discovery and source identity;
  complete roster coverage checks; independently checked quotes versus bounded
  support; date precision/ranges, rule observations, mixed-evidence aggregation.

`contracts.py` owns the shallow extraction schema. It has 17 distinct property
names across the root/profile/prior-role object definitions. `provider_schema()`
removes only `additionalProperties` for the raw Vertex JSON Schema subset; the
application enforces closed objects locally. Required fields/types remain aligned.
The model supplies facts and quotations only. It never supplies layout, dates at
invented precision, validator decisions, final admissibility or runtime records.

The application owns the final management schema, deduplicated citation table and
IDs, observation IDs, coverage, review window, observations, overall outcome and
limitations. Valid observation directions are positive/negative/neutral/unavailable;
`mixed` is allowed only for the separate aggregate outcome. Schema checks and
citation/executive reference checks run before artifact use.

`workflow.py` executes and saves each component independently. Its deterministic
manifest references schema versions, status, original-byte hashes, artifacts and
separate diagnostics. Diagnostics distinguish actual model request attempt,
available HTTP status, answer return, model/adapter/schema/prompt identity,
parse/schema checks, quotation/support checks, tokens, error stage and details.
Saved offline extraction is explicitly labelled and never counted as a model
request. A failed or corrupt management artifact cannot hide a valid diagram.

`provider.py` uses existing Vertex authentication, model selection, sampling and
usage helpers through an explicit small extraction adapter. It does not run the
old generic schema rewrite or issue retries. It requires authorization before
creating a live client or making a request.

## Integration changes

- `app.cli run-m3-rebuild` and the package entry point execute either/both
  components independently, offline by default.
- `BusinessOverviewRunner(..., bo_roster="v3", m3_rebuild_manifest=...)` or the
  matching `run-bo-case --roster v3 --m3-rebuild-manifest ...` switch replaces the
  legacy M3 job for that run. The old M3 job is not also called.
- The registry/explorer discovers `output/m3_rebuild/<case>/<run>` and presents
  independent component artifact nodes. Existing explicit `m3_dirs` works too.
- The actual artifacts cross the BO synthesis/handoff boundary under
  `module_conclusions.M3.m3_rebuild`. SO keeps them in its dedicated field.
- Company/period/cutoff and case identity checks prevent mixing filings/cases.
- No false legacy findings, governance ratings, management scores or verified
  legal relationships are created. Existing rubric behavior and default BO/M4/FO
  paths remain intact. The existing narrative validators still require their
  legacy hypothesis references; new observations remain available as the typed
  artifact block, not converted to legacy narrative claims.

## Generated HCA deliverables

Directory:
`/Users/Daniel/.codex/worktrees/m3-rebuild/kirotesting-evaluation/output/m3_rebuild/HCA_2024/run_verified_offline`

- `Visualized HCA Healthcare Corporate structure.json`
- `corporate_structure.svg` and visually inspected `corporate_structure.png`
- `Senior Management Assessment.json`
- `m3_rebuild_manifest.json`
- `structure_diagnostics.json`, `management_diagnostics.json`
- `management_sources.json`, `management_extraction.json`

The two final component statuses and registry entries are validated/complete.
Actual viewer byte retrieval was exercised against both saved JSONs. Corporate
output retains exactly 14 nodes, 13 links, five guarantor flags, the reference
1440 x 1080 visual specification and exact reference boxes. Aggregate record types
are retained. Single-child links are vertical; branch buses have no node overlaps
or unrelated connector crossings. The PNG was rendered locally with bundled
Sharp and inspected at the full canvas dimensions. No AI image generation was
used. This turn's initial draft output was preserved outside final-run discovery
under `output/m3_rebuild_drafts/HCA_2024/`.

The final management artifact is 20,227 bytes: seven profiles, ten observations,
eight unique citations and no rejected/missing roster profiles. The source quotes
appear once, with validated citation IDs elsewhere. It was produced from a
**manually transcribed, locally verified offline fixture**, not a Gemini response.

Review window: **[2022-02-01, 2025-02-01], inclusive**.

| Executive | Current appointment | Disclosed precision | Rule result |
|---|---|---|---|
| Samuel N. Hazen | 2019-01 | month | continuity positive |
| Jennifer L. Berres | 2019-11-01 | day | continuity positive |
| Michael S. Cuffe, M.D. | 2022-01-01 | day | continuity positive |
| Jon M. Foster | 2023-01-01 | day | recent-change negative + internal-experience positive |
| Michael A. Marks | 2024-05-01 | day | recent-change negative + internal-experience positive |
| Michael R. McAlevey | 2024-04-01 | day | recent-change negative + internal-experience positive |
| Sammie S. Mosier | 2021-12-01 | day | continuity positive |

Overall outcome is **mixed**, without net scores or financial impacts. No departure,
repeated turnover, operational disruption, competence or governance judgement is
inferred. All corporate entities/guarantees remain explicitly educational case
assumptions, separate from the filing-backed management disclosures.

## Offline verification

Final combined command: `bash docs/m3-rebuild-test-command.sh`, using the existing
project environment through `M3_PYTHON`. The script records the exact selected test
paths. Result: **688 passed, 1 skipped, 1 warning in 44.50 seconds**.

The new rebuild suite contributes 86 tests. Coverage includes lossless HCA facts,
sibling order, optional record types, malformed/duplicate inputs, deterministic
output, exact reference contract/filename/boxes, deep/wide canvas expansion,
node/connector bounds and crossings; complete roster coverage, stable/internal,
recent/internal dual directions, affirmative external and unavailable history,
missing/incorporated biographies, page-break discovery, fiscal-period isolation,
inclusive/leap-year/partial-date boundaries, strict types/fields/enums, exact quotes
versus role/date support, wrong-source claims, partial extraction and mixed rules;
independent failures and saved corruption, derived provider schema, mocked HTTP
400/200/no-answer and usage, authorization gates, real BO replacement, registry,
viewer and SO consumption. Related existing M3, M4, FO and registry regressions pass.

The one skip is the existing SO test requiring a local genuine historical HCA BO
artifact that is absent in this isolated baseline. The warning is the existing
FastAPI/Starlette test-client httpx deprecation. Neither represents a rebuild test
failure. `git diff --check` is clean. No paid call or broad full-project test suite
is claimed.

## Remaining limits and separately authorized live procedure

- Google service schema acceptance and Gemini extraction quality remain unverified
  live. Local schema validity and mocked HTTP acceptance are not service proof.
- Discovery is dynamic, but deterministic completeness verification currently
  recognizes dotted name/age/position roster tables. Other layouts remain partial
  and require review; missing dated biographies yield information insufficiency.
- Evidence support checks are conservative lexical/context rules, not general
  semantic entailment. Role-specific dates are checked separately from quotation
  matching. Unresolvable claims are rejected rather than filled with defaults.
- PDF extraction can split words (e.g. finance); quotations retain extracted text,
  and physical PDF page IDs remain available for typography review. Scanned PDFs
  need upstream OCR. Hazen's earliest multi-employer history is not asserted as
  exclusively internal; this is recorded in fixture provenance.
- Very long names that cannot fit the readable wrapping limit cause an explicit
  render failure. Wide/deep trees expand the canvas instead of shrinking nodes.
- Outputs from this independent source path do not assert canonical snapshot
  equivalence or verified transaction-obligor status. Legacy numeric rubrics are
  not fabricated from the new management observations.

After separate authorization, first run the tiny fictional probe in the runbook
with `--authorize-paid-schema-probe`. Inspect transport, parsing, schema, evidence,
usage and the synthetic assessment before proceeding. Then, only after separately
authorizing one filing test, run management with `--authorize-paid-filing-test`
and no saved extraction. Compare all seven FY2024 executive profiles and rule
outputs to the verified fixture. Each uses one bounded request, no automatic
retry and no full-filing schema debugging. **Neither paid procedure was executed.**
