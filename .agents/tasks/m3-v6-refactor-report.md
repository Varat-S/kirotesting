# Module 3 Corporate Structure Refactor — Business Overview Architecture V6

Targeted refactor of the existing Business Overview Module 3 (Corporate Structure
& Senior Management). Module 3 was **not** rebuilt from scratch. One specialist
(`m3_corporate_management`), Validator 7, the BO V3 architecture and all existing
source-grounding safeguards are retained.

> Deterministic validation does not guarantee economic or legal correctness. The
> HCA structure used throughout development is a **simulated educational
> example**; its entities, ownership percentages, borrower designation and
> guarantor assignments are hypothetical and must not be described as HCA
> Healthcare's verified actual corporate structure.

---

## 1. Revised Module 3 architecture

**Old flow:** rich, already-formatted v2.0 corporate-structure JSON → validation
→ SVG rendering.

**New flow (V6):** simplified v1.0 onboarding corporate-tree JSON → structural
validation → **deterministic v1.0→v2.0 layout + formatting transform** → refined
v2.0 JSON (`m3_corporate_structure.json`), which the existing v2.0 validator and
SVG renderer then consume downstream.

The core new piece is a **deterministic v1.0→v2.0 transformer**. The existing
v2.0 adapter (`build_corporate_structure_artifact`) and SVG renderer are reused,
not rewritten — a legacy v2.0 document is still accepted directly (backward
compatible). Module 3 now emits **two connected deliverables** (Architecture V6):

- **A — `m3_corporate_structure.json`** (primary output): the refined,
  presentation-ready v2.0 JSON (refined structural facts + generated
  presentation specs). The memo renderer consumes this. Never substituted by an
  SVG/PNG; the SVG remains an optional downstream preview.
- **B — `m3_analysis.json`** (governed analytical record): structural credit
  observations, material credit signals, the senior-management assessment,
  supporting/contradictory source references, evidence-verification status,
  materiality and limitations, information gaps, the consolidated Module 3
  conclusion, and a **stable reference to A**. The cross-module synthesizer
  consumes B.

A and B are linked by stable artifact identifiers plus source provenance
(`document_hash`, `transform_version`).

---

## 2. Files created / modified

**Created**

- `app/services/bo/corporate_diagram_template.py` — reusable, **versioned**
  (`bo-m3-corp-diagram-template-2.0.0`) company-agnostic presentation template
  (colours by role, node styling, connector rules incl. single-child / bus
  rules, legend, footnote, negative requirements, simulation disclaimer/status
  templates, how-to-use). No company facts, ids, names or coordinates.
- `app/services/bo/corporate_structure_onboarding.py` — the heart: parse +
  validate the simplified v1.0 onboarding tree into a canonical graph
  (`build_onboarding_graph`), deterministic layout + v1.0→v2.0 transform
  (`transform_to_refined_v2`), and round-trip reconciliation
  (`reconcile_roundtrip`). Transform version `bo-m3-corp-structure-transform-1.0.0`.
- `app/services/bo/m3_artifacts.py` — builds the two V6 deliverables (A/B) with
  stable cross-references.
- `tests/unit/test_bo_m3_onboarding_transform.py` — 26 offline tests.
- `tests/integration/test_bo_m3_v6_integration.py` — 7 offline tests.

**Modified**

- `app/schemas/bo_m3_structure.py` — added onboarding schema/document-type
  constants, transform version, onboarding/transform provenance fields,
  `refined_structure` + `roundtrip_reconciliation` artifact fields (additive,
  backward compatible).
- `app/services/bo/corporate_structure.py` — the v2.0 adapter now detects a v1.0
  onboarding payload, validates + transforms it to v2.0, reconciles round-trip
  (fail-closed on mismatch), and records onboarding provenance; direct v2.0
  input path unchanged. Document hash now taken over the original ingested
  source.
- `app/services/bo/completion.py` — SVG preview now rendered from the refined
  (generated) v2.0 `visual_spec`; `_module_conclusions` attaches the two V6
  deliverables; `write_completion_artifacts` writes `m3_corporate_structure.json`
  (only when available) and `m3_analysis.json` (always); added
  `admit_corporate_structure_source` case-associated admission seam.

Not touched unnecessarily: Modules 1/2/4; scoring weights; agent roster (still
one `m3_corporate_management`); `m3_validation.py` (Validator 7 narrative
governance) and `bo_m3.py` (narrative contract) are unchanged.

---

## 3. Both HCA reference JSONs were read in full from `examples/bo_m3/`

- **Input** `Simplified HCA Corp Structure.json`: `schema_version` **"1.0"**,
  `document_type` **"corporate_structure_onboarding"**. Nested `corporate_tree`,
  14 entities, 13 parent-child edges, 5 `is_guarantor=true`
  (hca_regional, hca_atlantic, hca_hosp_ops, hca_east, hca_west), two
  `record_type: "aggregate_group"` nodes (hca_other, hca_jv). No
  colours/coordinates/canvas.
- **Reference output** `Sample HCA Corp Structure Final Output.json`:
  `schema_version` **"2.0"**, `artifact_type`
  **"llm_reproducible_corporate_structure_diagrams"**. Top-level `visual_spec`
  (canvas 1440×1080, header, colors_by_role, node_style incl. guarantor_shield,
  ownership_links_style incl. single_child/bus rules, legend, footer,
  negative_requirements), `simulation_disclaimer`, `how_to_use`, and
  `companies.HCA` with `root_node_id`, 14 `nodes`, 13 `ownership_links`,
  `plain_text_tree`, `layout_note`.

Confirmed these are two distinct contracts (simplified onboarding input vs rich
presentation-ready output). The rich v2.0 file is never used as an onboarding
input. `examples/bo_m3/` files are development fixtures, never auto-admitted for
live cases.

---

## 4. Generated `m3_corporate_structure.json` path

Written by `write_completion_artifacts(packet, output_dir)` to
`<output_dir>/m3_corporate_structure.json` (alongside the existing
`handoff_v1_2.json` / `bo_completion_packet.json`). It is also available in
`module_conclusions["M3"]["m3_corporate_structure"]`. It is emitted **only** when
a valid, case-associated corporate-structure JSON was ingested. The governed
record `m3_analysis.json` lands next to it at
`<output_dir>/m3_analysis.json` (and `module_conclusions["M3"]["m3_analysis"]`).

---

## 5. Comparison vs the supplied HCA final-output reference

Driving the simplified v1.0 HCA input through the transform and comparing the
generated v2.0 output to the reference final output:

- **Structural facts match exactly** — identical entity id set (14), identical
  display names, roles and guarantor flags per id; identical 13 ownership links
  with identical percentages; identical `root_node_id` (`hca_top`); the generated
  `plain_text_tree` reproduces the reference byte-for-byte (same ordering +
  guarantor shields). Verified by
  `test_hca_transform_matches_reference_structural_facts`.
- **Presentation generated** — styling, connector instructions (single-child
  straight vertical + multi-child bus), legend, simulation footnote/disclaimer
  and negative requirements are produced from the reusable template, not copied
  per-company. Verified by `test_hca_transform_generates_presentation_specs`.
- **Deterministic layout** — positions are computed from hierarchy depth, sibling
  order, node dimensions and available space, then uniformly scaled to fit the
  1440-wide canvas with no overflow or sibling overlap. Single-child chains share
  one centreline (top=intermediate=borrower; regional=atlantic). Exact reference
  pixel coordinates are not reproduced (not required); the deterministic
  algorithm yields a clear, accurate, byte-stable result.
- **Byte-deterministic** — identical input produces identical output
  (`test_hca_transform_is_byte_deterministic`).

---

## 6. Generalisation results

A small synthetic fictional company ("Nimbus Logistics Group", 4 entities, 3
links, 2 guarantors) processes with **no company-specific code change**: correct
counts, correct guarantor set, round-trip consistent, company-agnostic key
derived from the display name, renders via the existing renderer. Verified by
`test_synthetic_company_transforms_without_company_specific_code`. The generic BO
code contains no hardcoded HCA/Galen/Stryker tokens (enforced by the existing
`test_bo_remediation.py` guardrail, which passes).

---

## 7. Missing-JSON (fail-closed) test results

When no valid, case-associated corporate-structure JSON is ingested, Module 3
does **not** produce a corporate structure: no `m3_corporate_structure.json`, no
diagram, no inference from 10-K / SEC Exhibit 21, no auto-load of the fixture.
It records `corporate_structure_status: "unavailable"` with the exact existing
`NO_JSON_MESSAGE`, preserves the information gap, lets senior-management
commentary proceed when supported, and is not marked fully complete (the
`m3_output` completion is `partial`). Verified by:
`test_no_source_yields_mandated_unavailable`, `test_wrong_case_association_rejected`,
`test_cross_case_leakage_rejected`, `test_fixture_in_examples_not_auto_loaded_v6`,
`test_tenk_exhibit21_do_not_trigger_structure_v6`,
`test_missing_json_writes_analysis_only_not_structure`,
`test_deliverable_a_absent_when_structure_unavailable_b_still_emitted`. Malformed
input, duplicate ids, out-of-range ownership %, invalid guarantor flag, missing
mandatory fields (role / ownership) and cyclic/contradictory relationships each
withhold the structure with a precise recorded problem.

---

## 8. Senior-management regression results

The governed `M3CorporateAssessment` + `validate_m3_assessment` (Validator 7) are
unchanged. The 15 existing management-governance tests
(`tests/unit/test_bo_m3_corporate_refinement.py`) still pass, and a new
post-refactor regression (`test_management_commentary_regression_after_refactor`)
confirms commentary stays functional and source-grounded: a demonstrated-outcome
claim keeps its evidence reference and disposition, while a tenure/credentials-
only claim is **not** promoted to supported (unsupported assertion stays distinct
from demonstrated performance). Credit directions {positive, negative, neutral,
mixed} and verification {supported, qualified, unverified, rejected} are retained;
insufficient evidence stays distinct from neutral; no outcome is forced.

---

## 9. Validator 7 + BO integration results

- **Case association / source grounding (deterministic):** the simplified JSON
  must belong to the correct case via trusted metadata (`registered_case_id` and
  `case_id` both equal the expected case); filename/company-name similarity is
  never sufficient. Verified by `test_admission_seam_records_explicit_case_association`,
  `test_wrong_case_association_rejected`, `test_cross_case_leakage_rejected`.
- **Round-trip fidelity:** `reconcile_roundtrip` deterministically proves every
  entity, ownership %, guarantor marking and parent-child link in the v2.0 output
  reconciles exactly to the v1.0 input; a mismatch fails closed (no diagram).
  Verified by `test_hca_transform_roundtrip_reconciles_exactly`.
- **Simulation handling:** simulated guarantor markings carry an enforceability
  qualification and are never represented as enforceable guarantees; ownership
  alone is not treated as unrestricted cash-flow access (existing structural
  signals preserved). Aggregate labels ("Other Subsidiaries", "JV / Minority
  Investments") are flagged, never treated as independently verified legal
  persons.
- **Provenance:** exact source hash, case association, document id, transform
  version and presentation-template version are preserved for audit.
- **Live BO integration seam:** the prior impl read the source from
  `inputs["corporate_structure_source"]` in the recorded M3 agent call, but
  nothing in production populated it. Added `admit_corporate_structure_source`,
  the smallest correct **case-associated** admission seam: a run/CLI driver with
  an onboarding JSON for a specific case uses it to attach the document (explicit
  `registered_case_id` + content hash) to the M3 call context. It never
  auto-selects a case, never auto-loads a fixture, and accepts either v1.0 or
  v2.0. Verified end-to-end offline by the V6 integration tests.

---

## 10. Full offline test results

Command (run from the worktree):

```
DEBUG=false .venv/bin/python -m pytest -q
```

Result: **1384 passed, 131 skipped, 1 warning, 12 errors**.

- **0 failures, 0 new regressions.**
- The **12 errors** are the KNOWN pre-existing/environmental ones in
  `tests/integration/test_bo_recorded_completion.py`: they require the untracked
  genuine R5 artifact
  `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/specialists_run.json`,
  which is absent from the worktree. Not caused by this change.
- Skips are pre-existing (benchmark workbook / PDF backend / optional provider
  modules not installed).

Targeted M3 suites (all pass):

```
DEBUG=false .venv/bin/python -m pytest \
  tests/unit/test_bo_m3_corporate_structure.py \
  tests/unit/test_bo_m3_corporate_refinement.py \
  tests/unit/test_bo_m3_services.py \
  tests/unit/test_bo_m3_independent_evaluation.py \
  tests/unit/test_bo_m3_onboarding_transform.py \
  tests/integration/test_bo_m3_corporate_structure_integration.py \
  tests/integration/test_bo_m3_v6_integration.py -q
# 86 passed
```

One pre-existing guardrail test (`test_bo_remediation.py::
test_case_coverage_checks_are_data_and_state_no_conclusion`) initially failed
because new docstrings mentioned the bare token "HCA"; the comments were reworded
to reference "the sample under examples/bo_m3/" generically (no functional
hardcoding was ever present) and the test now passes.

---

## 11. Remaining limitations

- **Live ingestion seam:** `admit_corporate_structure_source` is the correct,
  case-associated admission point and is exercised offline end-to-end, but it is
  a *seam* — a production run/CLI driver still has to call it with the trusted
  case id and the onboarding document. A full UI/evidence-store ingestion path
  (upload → persist → case registration) was intentionally **not** built; wiring
  it into the CLI's `run-bo-specialists` input assembly remains future work. This
  avoids faking a broader ingestion framework and preserves recorded-run
  compatibility.
- **Layout vs reference pixels:** the deterministic layout reproduces hierarchy,
  sibling order, grouping, single-child alignment and multi-child buses, and
  fits the canvas, but does not reproduce the reference's exact per-node pixel
  coordinates (not required by the spec).
- **Economic/legal correctness:** deterministic validation and round-trip
  reconciliation guarantee the diagram faithfully represents the *input*; they do
  not establish that the input is economically or legally correct. All structural
  conclusions remain descriptive, qualified and for human review.
- The HCA structure is simulated; nothing here should be read as HCA's real
  corporate structure.

---

## 12. Git branch + working-tree state

- Branch: `feat/m3-v6-refactor` (worktree
  `.worktrees/m3-v6-refactor`). Work committed to this branch only.
- No push, no merge, no PR. No commits to `daniels-module-3` or
  `feat/m3-corporate-mgmt`.
- The review patch is generated from the committed state.
