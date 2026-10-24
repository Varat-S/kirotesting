# Module 3 V6 — simplified onboarding → refined v2.0 corporate-structure transform

The change aligns Business Overview Module 3 with Architecture V6. The old flow consumed a rich, already-formatted v2.0 corporate-structure JSON and rendered it. The new flow ingests a simplified v1.0 onboarding corporate tree (entities, roles, guarantor flags, ownership percentages, grouping markers, no presentation), validates it structurally, runs a deterministic v1.0→v2.0 layout-and-formatting transform, reconciles the output back to the input, then feeds the generated v2.0 payload into the existing adapter/renderer. Module 3 now emits two connected deliverables — the primary `m3_corporate_structure.json` (refined v2.0 structure + presentation) and the governed `m3_analysis.json` (analytical record referencing the structure) — and the senior-management commentary path is preserved untouched. The refactor is additive: no file was rebuilt, the single `m3_corporate_management` specialist, Validator 7 and all source-grounding safeguards remain.

Watch for: nothing blocking. One cosmetic doc/code mismatch (the report says `refined_structure` is "empty for a direct v2.0 source" but the code sets it to the validated payload — correct behavior, stale prose) — **possible**, non-blocking.

**Verdict**: APPROVED

## High-level view

The core new piece is `corporate_structure_onboarding.py`: a parser/validator (`build_onboarding_graph`), a deterministic layout+transform (`transform_to_refined_v2`), and a round-trip reconciler (`reconcile_roundtrip`). The existing v2.0 adapter (`build_corporate_structure_artifact`), SVG renderer and narrative Validator 7 (`validate_m3_assessment`) are reused, not replaced — a legacy v2.0 document is still accepted directly, so the change is backward compatible.

The transform is byte-deterministic: stable BFS/sibling traversal order, canonical coordinate rounding, no timestamps or random ids. Identical input yields identical output, verified by a dedicated test. Layout derives positions from hierarchy depth, sibling order, node dimensions and available space, with single-child chains aligned on one centreline and multi-child groups packed under a bus, then uniformly scaled to fit the canvas without overlap.

For the HCA fixture the generated structural facts match the supplied reference final output exactly: 14 entities, 13 ownership links, 5 guarantors, identical ids/names/roles/percentages/root, and a byte-for-byte identical `plain_text_tree`. The two aggregate grouping nodes (`hca_other`, `hca_jv`) are flagged, qualified as not independently established legal persons, and never promoted into the legal-entity reasoning.

Round-trip reconciliation deterministically proves every entity, ownership link and guarantor marking in the v2.0 output reconciles to the v1.0 input; a mismatch fails closed (no diagram). Missing/invalid/wrong-case input also fails closed with the exact `NO_JSON_MESSAGE`, no fixture auto-load, no inference from 10-K / Exhibit 21 evidence; management commentary still proceeds and the module downgrades to partial rather than silently completing.

Guardrails hold: schema changes are purely additive, no new agents, Validator 7 / the narrative contract / Modules 1-2-4 untouched, no scoring-weight or rubric changes, no hardcoded HCA facts (presentation lives in a versioned company-agnostic template), simulation disclaimer retained. The full offline suite is green modulo the known pre-existing R5 integration errors.

<details>
<summary>Issues (1)</summary>

1. **Stale report prose** — the report states `refined_structure` is "empty for a direct v2.0 source"; the code assigns it the validated payload (which is the correct, desirable behavior). Cosmetic only; optionally reword the report. Non-blocking.

</details>

<details>
<summary>Details</summary>

### Reuse vs rebuild

`corporate_structure.py` keeps its entire v2.0 validation path. The new branch at the top of `build_corporate_structure_artifact` detects a v1.0 onboarding payload (by `document_type` / `schema_version`), parses+validates it into a canonical graph, transforms it to v2.0, reconciles the round-trip, records transform provenance, then falls through to the unchanged v2.0 validation against the generated payload. The direct v2.0 path is unchanged; `test_v2_source_still_accepted_backward_compatible` confirms a legacy v2.0 document still validates with no transform provenance and no round-trip record. `m3_validation.py` (Validator 7 narrative governance) and `bo_m3.py` (narrative contract) carry no diff. One `m3_corporate_management` specialist remains; no agent roster change.

### Deterministic v1.0→v2.0 transform

`transform_to_refined_v2` composes a company-agnostic presentation template with facts from the validated graph. Determinism is structural: `_bfs_order` fixes node ordering by depth then sibling index, `_round_px` canonicalises coordinates, the `companies` key is a slug of the display name, and nothing injects time or randomness. `test_hca_transform_is_byte_deterministic` compares `canonical_json` of two runs; `test_both_deliverables_written_to_disk_when_available` re-runs the whole module path and asserts identical bytes.

Layout (`_assign_positions`) computes subtree widths bottom-up, places single children on the parent centreline (no bend), packs multiple children left-to-right and centres the parent over them, then applies a single global scale if the packed extent exceeds the available canvas band. `test_single_child_aligned_below_parent_no_bend` checks the `hca_top→intermediate→borrower` chain and `hca_regional→hca_atlantic` share a centreline; `test_layout_fits_canvas_and_does_not_overlap` checks every box stays within the canvas and same-row siblings do not overlap.

### HCA fidelity to the reference

`test_hca_transform_matches_reference_structural_facts` drives the simplified input through the transform and asserts the generated company's node id set, display names, roles, guarantor flags, the full `ownership_links` map (parent/child/percent), `root_node_id` and the `plain_text_tree` all equal the supplied `Sample HCA Corp Structure Final Output.json`. Counts (14 nodes / 13 links / 5 guarantors) are asserted in `test_hca_onboarding_transforms_to_valid_v2_with_exact_facts`. I confirmed the input fixture independently: 14 entities, guarantors `{hca_regional, hca_atlantic, hca_hosp_ops, hca_east, hca_west}`, aggregates `{hca_other, hca_jv}`.

### Aggregate grouping handled, not legal persons

Grouping is detected by the authoritative `record_type == "aggregate_group"` marker plus conservative role/name fallbacks. Grouping ids are flagged on each `StructureNode` (`is_grouping_node`) and surfaced as `grouping_node_ids`, and the artifact carries an explicit limitation that aggregate nodes "are not treated as independently established legal persons." In `_derive_structural_signals` grouping nodes feed only the qualified minority/JV-exposure observation; they are never converted into a borrower, obligor or guarantor. `test_aggregate_groups_not_legal_persons_in_deliverable` asserts the limitation is present.

### Round-trip reconciliation (fail-closed)

`reconcile_roundtrip` compares the generated output to the validated graph on entity set, names, roles, guarantor markings and the ownership-link map (with normalised percentages), reporting any fabricated/missing/mismatched item. A non-`consistent` result makes `build_corporate_structure_artifact` return `rejected` with `NO_JSON_MESSAGE` rather than emit an unreconciled diagram. `test_hca_transform_roundtrip_reconciles_exactly` asserts `consistent`, empty discrepancies and equal input/output counts.

### Two connected deliverables

`m3_artifacts.build_m3_deliverables` builds Deliverable A (refined v2.0 payload wrapped with a content-hash-derived `artifact_id`, provenance and the round-trip record) and Deliverable B (`m3_analysis`, carrying structural observations, the senior-management assessment, evidence/verification posture, information gaps, the consolidated conclusion and a stable reference to A). `completion.write_completion_artifacts` writes `m3_corporate_structure.json` only when a validated structure exists and always writes `m3_analysis.json`. The SVG is rendered from the refined `visual_spec` purely as an optional preview. `test_both_deliverables_written_to_disk_when_available` confirms both files land and B references A by `artifact_id`.

### Fail-closed on missing / invalid / wrong-case input

`test_no_source_yields_mandated_unavailable`, `test_wrong_case_association_rejected`, `test_cross_case_leakage_rejected`, `test_fixture_in_examples_not_auto_loaded_v6` and `test_tenk_exhibit21_do_not_trigger_structure_v6` show that without an explicitly case-associated onboarding JSON, Module 3 produces no structure, no SVG, no inference, no fixture auto-load — status `unavailable` with the exact `NO_JSON_MESSAGE`. `test_missing_json_writes_analysis_only_not_structure` shows A is not written, B still is, management commentary still `assessed`, and the module is `partial`, never silently `complete`. Malformed input, duplicate ids, out-of-range ownership, non-boolean guarantor flag, missing role/ownership and cycles each reject with a precise `rule_id`.

The `admit_corporate_structure_source` seam records case association explicitly via `registered_case_id`/`case_id` and a content hash; it never selects a case or loads a fixture. This is a seam, not a full ingestion path (the report flags that production CLI wiring remains future work) — acceptable and does not weaken any safeguard.

### Senior-management commentary preserved

`validate_m3_assessment` (Validator 7) is unchanged. `test_management_commentary_regression_after_refactor` confirms a demonstrated-outcome claim keeps its evidence reference and disposition, while a tenure/credentials-only claim is not promoted to supported — unsupported assertion stays distinct from demonstrated performance. The 15 existing management-governance tests still pass. Credit directions and verification statuses remain separate fields; insufficient evidence stays distinct from neutral; no outcome is forced (`forced_outcome: False`, `requires_human_review: True`).

### Guardrails and provenance

Schema edits in `bo_m3_structure.py` are additive only: new version constants, new onboarding/document-type constants, and new optional provenance/`refined_structure`/`roundtrip_reconciliation` fields with defaults — backward compatible. The presentation template (`corporate_diagram_template.py`) is versioned and company-agnostic (role-based colours and styling rules, no entity names/ids/coordinates); the simulation disclaimer and status templates are retained and applied for simulated sources. No Module 1/2/4 change, no scoring-weight or rubric change, no hardcoded HCA tokens (the existing `test_bo_remediation.py` guardrail passes).

### Verification

I read the committed diff against `feat/m3-corporate-mgmt`, the two reference fixtures and the key sources and tests. I ran the targeted M3 suite (86 passed) and the full offline suite once (1384 passed, 131 skipped, 12 errors). All 12 errors are exclusively `tests/integration/test_bo_recorded_completion.py`, the known pre-existing/environmental R5-fixture dependency — zero failures, zero new regressions. The recorded counts in the report and commit message match what I observed. No existing expectation was weakened. Work is committed only on `feat/m3-v6-refactor`; no push/PR.

</details>

<details>
<summary>File map</summary>

- `app/services/bo/corporate_structure_onboarding.py` — new: v1.0 parser/validator, deterministic layout+transform, round-trip reconciler.
- `app/services/bo/corporate_diagram_template.py` — new: versioned, company-agnostic presentation template.
- `app/services/bo/m3_artifacts.py` — new: builds the two V6 deliverables with stable cross-references.
- `app/services/bo/corporate_structure.py` — detects/transforms/reconciles v1.0 onboarding; v2.0 path unchanged.
- `app/services/bo/completion.py` — SVG from refined spec; attach + write both deliverables; `admit_corporate_structure_source` seam.
- `app/schemas/bo_m3_structure.py` — additive constants + provenance/refined/roundtrip fields.
- `tests/unit/test_bo_m3_onboarding_transform.py` — new: 26 offline tests.
- `tests/integration/test_bo_m3_v6_integration.py` — new: 7 offline tests.
- `examples/bo_m3/Simplified HCA Corp Structure.json`, `examples/bo_m3/Sample HCA Corp Structure Final Output.json` — reference fixtures.

Full diff: `git -C .worktrees/m3-v6-refactor diff feat/m3-corporate-mgmt...feat/m3-v6-refactor`

</details>
