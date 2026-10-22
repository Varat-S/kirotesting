# M3 Corporate Structure & Senior Management — Deterministic Diagram + Three-Section Output

A single commit on `feat/m3-corporate-mgmt` adds Deliverable A (the corporate-structure diagram pipeline) and the three-section Module 3 output, additively, on top of the existing narrative M3 pipeline. It introduces a versioned structure schema, a company-agnostic single-company JSON adapter with Validator-7 structural checks, a deterministic byte-stable SVG renderer, and a Section A/B/C assembler, then wires the result into `_module_conclusions["M3"]` through the completion seam. The existing roster, Validator 7, narrative contract, scoring, and module boundaries are left untouched. Watch for: nothing blocking — the fail-closed no-JSON path, case-association-by-metadata, single-company enforcement, deterministic rendering, and partial-completion routing are all implemented and tested (confirmed).

**Verdict**: APPROVED

## High-level view

The change is purely additive. Nine production files on the base (roster_v3.py, m3_validation.py, bo_prompts.py, reporting.py, bo.py, scoring) are not in the diff; the only modified production file is `completion.py`, and its `_module_conclusions` signature change is a backward-compatible keyword (`case_id=None`). The one M3 specialist `m3_corporate_management` is retained and still carries `validator_id="validator_7"`; `V3_SPECS` still lists exactly nine agents. No scoring weights, rating rubric, or Module 4 scope appear anywhere in the diff.

The no-JSON rule fails closed. `build_corporate_structure_artifact(None, ...)` returns `status="unavailable"` with the mandated message verbatim and no nodes; wrong case-association, wrong current case, malformed payload, multi-company, unsupported schema, out-of-range percentage, bad node reference and cycles all return `status="rejected"` with the precise reason and no diagram. Case association is decided solely by trusted metadata (`registered_case_id == expected_case_id` and `source.case_id == expected_case_id`); filename and company key never enter selection. The examples/ fixture is never auto-loaded by production code — the completion seam only reads a source from the M3 agent call's `inputs["corporate_structure_source"]`.

The renderer draws strictly from validated values and is deterministic: sorted parent iteration, canonical float formatting, no timestamps or random ids. Ownership percentages come only from `ownership_links`; guarantor shields are emitted only where `is_guarantor` is true. Single-child connectors are one perfectly straight vertical line (child snapped to the parent centreline); multiple children use one horizontal bus with vertical drops. `negative_requirements` are honoured (no extra header disclaimer line, no legend note beyond listed entries).

Credit signals keep direction, verification, and availability in separate fields, and the module completes PARTIALLY (not silently complete) when the structure is unavailable but the management assessment proceeds — the completion wiring only downgrades `complete`→`partial` and never promotes an already-incomplete module.

<details>
<summary>Issues (0)</summary>

No blocking or non-blocking findings. All acceptance criteria verified.

</details>

<details>
<summary>Details</summary>

## What I checked

- Commit log and diff stat: one commit, nine files (`git log --oneline daniels-module-3..feat/m3-corporate-mgmt`).
- Full diff of all new modules (`bo_m3_structure.py`, `corporate_structure.py`, `corporate_structure_render.py`, `m3_output.py`) and the `completion.py` change.
- Roster integrity: `roster_v3.py` is not in the diff; `V3_SPECS` lists exactly nine agents; `m3_corporate_management` retained with `validator_id="validator_7"` (roster_v3.py:145-165, 205).
- Scoring/boundary scan of the production diff for weight/rubric/refinancing/debt-service/maturity/creditor-ranking terms — only matches are SVG `font-weight` attributes.
- The HCA fixture: 14 nodes, 13 links, 5 guarantors; `hca_regional`→`hca_atlantic` is a single-child edge with both center_x_px == 145.
- Base branch cleanliness: no commits on `daniels-module-3` since divergence; worktree on `feat/m3-corporate-mgmt`.
- Spot-run of the new unit + integration suites offline: 28 passed in 0.39s (matches the report's 22+6).

## No-JSON / rejection path (fail-closed)

`corporate_structure.py:95-200`. With `source is None` the adapter records a `recorded_gap` issue and returns `_unavailable(NO_JSON_MESSAGE, ...)` — `status="unavailable"`, empty nodes/links. `NO_JSON_MESSAGE` in `bo_m3_structure.py:46-49` is the exact mandated string. Every other failure (case mismatch, non-dict payload, unsupported schema_version/artifact_type, zero or >1 companies, non-dict company, missing nodes, bad coordinates, duplicate/missing node id, unknown/self link reference, out-of-range percentage, contradictory edge, cycle, multi-parent, unreachable node) returns `status="rejected"` with a specific `rule_id` in `validation_issues` and withholds the diagram. The source payload is deep-copied before inspection, so the original JSON is never mutated.

Case association is decided only by trusted metadata: `registered_case_id` must equal `expected_case_id` AND `source.case_id` must equal `expected_case_id` (lines 118-135). The single-company key is extracted dynamically via `next(iter(companies))` and plays no part in selection. Integration tests confirm a 10-K / Exhibit-21-only call and an empty-inputs call both yield `unavailable` with no SVG, and that the examples/ fixture is not auto-loaded.

## Deterministic renderer drawn from validated values

`corporate_structure_render.py`. `render_corporate_structure_svg` raises if the artifact is not `available`, so no diagram is ever drawn from an unvalidated structure. Percentages are read from `pct_by_edge` built from `artifact.links` only; guarantor shields are emitted only inside `_render_node` when `node.is_guarantor`, tagged `data-guarantor="true"`. Byte-stability comes from `sorted(by_parent)` iteration, `_fmt` canonical float formatting, and the absence of timestamps/random ids — verified by `test_hca_rendering_is_byte_stable` and the synthetic equivalent.

Connector rules (lines 118-168): a single child draws one `<line>` with `x1==x2==parent_cx` from the parent bottom to the child top, with the child on the parent centreline — confirmed by the fixture (both nodes at cx=145) and `test_hca_regional_to_atlantic_connector_is_perfectly_vertical`. Multiple children draw one parent drop, one horizontal bus, and per-child vertical drops. `negative_requirements` are honoured: `_render_header` emits no extra disclaimer line and `_render_legend` emits only the listed entries.

## Credit-signal separation and partial completion

`bo_m3_structure.py:60-92` keeps `credit_direction` (reused `Direction`), `assessment_availability` (`sufficient` | `insufficient_evidence`), `materiality`, and `confidence` in separate fields; it reuses `Direction`/`Materiality`/`Confidence` from the existing schemas rather than inventing a scoring system. `_derive_structural_signals` emits neutral-but-sufficient observations (complexity, borrower position) distinctly from insufficient-evidence ones (distribution dependency), verified by `test_structural_signals_keep_direction_and_availability_separate`.

`m3_output.py:_completion_status` marks the module `complete` only when A is available and B is supported; when B proceeds but A is unavailable it is `partial`; otherwise `incomplete`. The completion wiring in `completion.py` only downgrades an already-`complete` M3 to `partial` and never promotes an incomplete module, so missing narrative agents still dominate. Section B survives when the diagram is withheld (`test_senior_management_proceeds_when_structure_absent`).

## Grouping nodes kept out of the governed legal-entity graph

`corporate_structure.py:_is_grouping` flags aggregate labels (Other / JV / minority) by role and name hints; those ids go into `grouping_node_ids` and a limitation is recorded, but they are still drawable. `test_hca_grouping_nodes_flagged_not_treated_as_legal_entities` confirms `{hca_other, hca_jv}` are flagged.

## Test coverage

22 unit + 6 integration tests, all offline, spot-run green (28 passed). They cover the 14/13/5 HCA assertions, exact-name matching, percentages-only-from-links, guarantor-shield count, byte-stability, grouping flags, simulation treatment, the synthetic single-company generalisation with no code change, all rejection paths, the verbatim no-JSON message, partial completion, and the completion-seam integration (fixture not auto-loaded, 10-K does not trigger a replacement). The report's full-suite result (1351 passed, 131 env skips, 12 pre-existing errors in `test_bo_recorded_completion.py` requiring an untracked local R5 artifact) was not re-run per instructions; the coder verified the 12 errors reproduce on the clean base, so they are environmental, not regressions.

Not tested: real font-metric text wrapping (uses a fixed glyph estimate by design for determinism) and PNG rasterisation (optional no-op without cairosvg) — both documented limitations, neither a blocking concern.

## File map

- `app/schemas/bo_m3_structure.py` — new versioned structure contract, provenance, and separated-field credit signal.
- `app/services/bo/corporate_structure.py` — generic single-company adapter + Validator-7 structural checks + signal derivation.
- `app/services/bo/corporate_structure_render.py` — deterministic SVG renderer, optional PNG no-op.
- `app/services/bo/m3_output.py` — Section A/B/C assembly and completion status.
- `app/services/bo/completion.py` — wires the artifact into `_module_conclusions["M3"]`; backward-compatible `case_id` keyword; `complete`→`partial` downgrade only.
- `examples/bo_m3/Refined HCA Corp Structure.json` — dev fixture (test-only).
- `tests/unit/test_bo_m3_corporate_structure.py`, `tests/integration/test_bo_m3_corporate_structure_integration.py` — 28 offline tests.

Full diff: `git -C <worktree> diff daniels-module-3...feat/m3-corporate-mgmt`.

</details>
