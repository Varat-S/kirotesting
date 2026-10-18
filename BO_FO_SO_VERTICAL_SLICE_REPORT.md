# Actual BO Agents + Simulated FO + Governed SO Vertical Slice

## Result

The offline integration test successfully produced a canonical JSON packet and
a 112-page PDF for the current HCA R5 run. The PDF combines:

- actual recorded outputs from all nine live BO specialists;
- the existing explicitly invented two-period FO fixture and deterministic
  metric calculations;
- the final recorded M3 proposal replayed through current Validator 7; and
- cross-domain observations, completion gates, next steps, source lineage,
  validation exclusions, and per-agent audit appendices.

No model was called during this integration test. Every PDF page identifies the
assessment as incomplete and distinguishes actual BO output from simulated FO
and offline-governed SO input.

## Source and output

BO source:

`output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/specialists_run.json`

Generated artifacts:

- `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/vertical_slice/bo_fo_so_vertical_slice.json`
- `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/vertical_slice/bo_fo_so_vertical_slice.pdf`

Artifact integrity:

- report content hash: `b645107481910e02962e5c2cc16d8d7facb01bae3e6e3f9de1901af343f174ed`
- PDF SHA-256: `4f7fabcf42a9e2582f23cc9e787b047fa4e4ca348d720cfe6be26b93492e2e5a`
- JSON file SHA-256: `f1e44eb11464aca901ec463a88cf3ae3d97c2fdf73b3c79a9f01533aa7840657`
- canonical BO source content hash: `3ee113c97ba5ee24330b7b283a761ecf2a9d7d5d36a6a8431e3a8822770f6e5f`
- source FO fixture content hash: `464812b209d60f2d023f3b1005ee9b658a493d88d98a30270c33f9071beb8a61`

## Actual BO coverage

The report preserves the R5 run as a specialists-only result rather than
inventing an accepted BO handoff. It contains:

- nine validated specialists and 13 recorded live-model calls;
- 28 validator-recorded hypotheses, of which 26 are usable;
- 19 accepted parameters and three held back;
- 10 coverage checks, nine addressed;
- citation failures, recoveries, review flags, validator adjustments, and
  retrieval attempts;
- 256,359 recorded tokens and per-call latency/usage;
- all validated hypotheses with evidence quotes and full evidence IDs;
- each call's structured model output and input identity summary;
- exact effective specialist instructions and prompt hashes; and
- source-document, stage, registry, model, and evidence lineage.

The BO topic conclusion, challenger, risk score, accepted handoff, and human
review were not executed in this specialist-only run. They are marked
`not_executed` or `not_performed`; the test does not manufacture them.

## Simulated FO

The existing `examples/credit_memo/synthetic_fo_v1.json` fixture is reused.
Its case label is deterministically rebound to the R5 case while every invented
number and the `synthetic_fixture` provenance remain unchanged. The binding and
both source/normalized hashes are recorded.

The metric engine calculates FY2024/FY2025 operating margin, net debt/EBITDA,
interest coverage, free cash flow, liquidity, and FY2025 revenue growth. For
FY2025 the illustrative outputs include 5% revenue growth, 14% operating
margin, 3.325x net debt/EBITDA, 5.441x interest coverage, 3,200 USD million free
cash flow, and 7,000 USD million liquidity. None describes actual HCA financial
performance.

## Governed SO result

The final M3 call is replayed through `validator-7-1.0.0` using only its recorded
19-item evidence packet and no transaction borrower specification. The replay:

- calls no model;
- retains HCA Healthcare, Inc. as the sole admitted reporting-parent legal
  entity;
- leaves the proposed facility borrower unavailable;
- admits no ownership edge, cash-flow location, or cash-transfer edge;
- retains one structural finding, one management observation, and one
  governance observation under their governed dispositions;
- records 11 validation issues and four rejected items in the audit boundary;
  and
- carries SO dependencies to confirm the obligor and assess structural
  subordination/ranking.

This is intentionally not a legal opinion or a completed Structural Overview.
Obligor identity, ranking, restrictions, guarantees, collateral, covenants,
facility terms, and enforceability remain outstanding.

## PDF organization

The PDF includes an executive completion map, actual BO portfolio summary,
simulated FO inputs/calculations, governed M3/SO result, M3 validation audit,
nine individual agent sections, all recorded call histories, and a final
source/integrity appendix. The long form is deliberate: it serves as both the
thin-slice result and the human-review audit packet.

Representative pages were rendered to images and visually checked. Tables,
notices, code blocks, headings, page numbers, and long identifiers remain
readable without clipping. Extracted text confirms that all 112 pages carry the
incompleteness disclosure.

## Reproduction

```bash
DEBUG=false .venv/bin/python -m scripts.bo_fo_so_vertical_slice \
  --bo-run output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/specialists_run.json
```

The command defaults to the existing synthetic FO fixture and writes beneath a
`vertical_slice` directory alongside the BO source artifact. It writes and
revalidates canonical JSON before rendering the PDF with ReportLab.

Validation on 2026-10-18 completed with:

- focused BO/FO/SO, prior credit-memo, SO adapter, and sampling suites:
  52 passed, 2 optional historical-handoff tests skipped;
- complete repository regression: 1,437 passed, 16 skipped; and
- Python compilation and Git whitespace checks: passed.

The complete run emitted one existing Starlette/httpx deprecation warning.
Skips were limited to unavailable optional fixtures/dependencies and the
explicitly disabled live-model test; the local R5 actual-artifact test ran and
passed.

## Decision state

The vertical slice demonstrates contract flow and auditability, not credit
completion. Facility underwriting and final synthesis remain blocked. Required
next steps are accepted BO synthesis/challenge and human review, real admitted
FO analysis, transaction-specific borrower evidence, full legal/facility SO,
and cross-topic challenge before the Credit Orchestrator can run.
