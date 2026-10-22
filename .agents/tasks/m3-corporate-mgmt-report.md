# Module 3 — Corporate Structure & Senior Management: Implementation Report

Branch: `feat/m3-corporate-mgmt` (worktree). Base `daniels-module-3` untouched.
First iteration (no `m3-review.json` present at start).

## 1. Architecture summary

Module 3 already had a mature, well-governed **narrative** pipeline: the single
specialist `m3_corporate_management` (roster V3), the strict `bo_m3.py` contract
(`M3CorporateAssessment`: legal entities, ownership relationships, cash-flow
locations, cash transfers, structural findings, management observations,
governance screening), and **Validator 7** (`m3_validation.validate_m3_assessment`)
which conservatively downgrades unsupported claims, separates grouping nodes from
legal persons, and preserves rejected items for audit. That existing capability
was **reused unchanged**.

The genuine gap was **Deliverable A's corporate-structure diagram pipeline** and
the **three-section output assembly**. This was added additively:

- `app/schemas/bo_m3_structure.py` — new, versioned contract for the onboarding
  corporate-structure JSON, the validated render-ready artifact, structural
  credit signals (direction / verification / assessment-availability kept in
  separate fields), and full provenance. Reuses `Direction`, `Materiality`,
  `Confidence` from `app/schemas/bo.py` rather than inventing a scoring system.
- `app/services/bo/corporate_structure.py` — the generic single-company JSON
  adapter + Validator 7 structural checks + case-association + structural-signal
  derivation. Company-agnostic (no entity name, count, percentage, role,
  guarantor flag or coordinate hardcoded).
- `app/services/bo/corporate_structure_render.py` — the deterministic
  (byte-stable) SVG renderer with optional PNG export.
- `app/services/bo/m3_output.py` — Section A / B / C assembly and the overall
  completion status (including partial completion).
- `app/services/bo/completion.py` — wired the three-section output into
  `_module_conclusions["M3"]` via the new `build_m3_corporate_structure`.

Boundaries held: one M3 specialist (not changed); Validator 7 extended (not
replaced); no Module 4 scope; no scoring-weight or rating-rubric changes; no
live model/network calls; schema changes are additive and versioned; the
original JSON is never mutated.

## 2. Files created / modified

Created:
- `app/schemas/bo_m3_structure.py`
- `app/services/bo/corporate_structure.py`
- `app/services/bo/corporate_structure_render.py`
- `app/services/bo/m3_output.py`
- `tests/unit/test_bo_m3_corporate_structure.py` (22 tests)
- `tests/integration/test_bo_m3_corporate_structure_integration.py` (6 tests)
- `examples/bo_m3/Refined HCA Corp Structure.json` (dev fixture; see §3)

Modified:
- `app/services/bo/completion.py` — imports; `build_m3_corporate_structure(...)`;
  `_module_conclusions(..., case_id=None)` (backward-compatible keyword) attaches
  `m3_output`, `corporate_structure_status`, and downgrades a `complete` M3 to
  `partial` when the structure is unavailable but management proceeded.

## 3. Where the HCA JSON lives / how it is used for dev

The attachment `Refined HCA Corp Structure.json` was present in the **main
workspace** at `examples/bo_m3/` but absent from this worktree (the branch
predates it). I copied it verbatim into the worktree at
`examples/bo_m3/Refined HCA Corp Structure.json` and use it **only as a test
fixture** (unit + integration tests read it there). It is never auto-loaded by
production code — `build_corporate_structure_artifact` requires an explicitly
case-associated source object; there is no path that reads this file during a
real analysis run.

## 4. How corp JSON is associated with a case and ingested

`CaseCorporateStructureSource` carries trusted case metadata:
`case_id`, `registered_case_id` (the case the JSON was registered against at
ingestion), `payload`, `source_document_id`, `document_hash`. The adapter
confirms association via `registered_case_id == expected_case_id` **and**
`source.case_id == expected_case_id`; **filename and company key play no part in
selection**. In the completion seam, the source is read from the M3 agent call's
`inputs["corporate_structure_source"]`; if absent, the artifact is `unavailable`.

Production selection order (adapter): case-association → supported
`schema_version`/`artifact_type` → exactly one company → dynamic extraction of
the sole company → full hierarchy validation → diagram only after success.

## 5. How the renderer works

`render_corporate_structure_svg(artifact, visual_spec)` draws strictly from
validated JSON values. Byte-stability: no timestamps/random ids, parents iterated
in sorted order, canonical float formatting. It follows `visual_spec`: canvas,
`colors_by_role`, node geometry, wrapped names + `(role)`, `header`/subtitle,
`legend` (only listed entries — no extra note), `footer` (simulation footnote).
Ownership percentages come **only** from `ownership_links`; guarantor shields are
drawn **only** where `is_guarantor` is true (`data-guarantor="true"`). Connectors:
one child → one perfectly straight vertical line with the child snapped to the
parent centreline (no bus/elbow/jog); multiple children → one horizontal bus with
vertical drops. `negative_requirements` honoured (no extra header disclaimer line,
no legend note). PNG export is optional and a no-op when no rasteriser is present
(SVG is authoritative; no network call).

## 6. HCA output + verification (14 / 13 / 5)

Validating the fixture yields `status=available`, **14 nodes, 13 ownership links,
5 guarantor markers** (`hca_regional, hca_hosp_ops, hca_atlantic, hca_east,
hca_west`), with all display names matching the JSON exactly and percentages
attached only from `ownership_links`. Rendered SVG contains exactly 5 guarantor
shields, role colours from `visual_spec`, the simulation footnote, and the
`hca_regional → hca_atlantic` connector as a single line with `x1==x2==145`
(perfectly vertical). Repeated rendering of identical input is byte-stable.
Grouping nodes `hca_other` and `hca_jv` are flagged (`grouping_node_ids`) and not
treated as legal persons. (Tests: `test_hca_*`.)

## 7. Synthetic single-company JSON

`test_synthetic_company_parses_and_renders_without_code_change` uses a fictional
"Acme Fictional Group" with different ids, percentages and 2 guarantors, same
schema. The same adapter + renderer process it with no code change and produce a
byte-stable SVG with 2 guarantor shields. The HCA fixture is not modified.

## 8. Missing-JSON behaviour

`build_corporate_structure_artifact(None, ...)` returns `status=unavailable`,
`message` exactly:
"Corporate structure unavailable — no valid corporate-structure JSON was provided
during onboarding.", no nodes/links. Rejections (wrong case association, wrong
current case, malformed, multi-company, out-of-range percentage, bad node
reference, cycle, unsupported schema_version) return `status=rejected` with the
precise problem in `validation_issues` and no diagram. Integration tests confirm
the examples-dir fixture is not auto-loaded and that 10-K / Exhibit 21 evidence
does not trigger a replacement diagram. When the structure is unavailable but the
management assessment proceeds, the module completion is `partial` (not silently
complete).

## 9. Management-commentary grounding + credit signals

Section B is sourced from the existing governed `M3CorporateAssessment`
(management observations + governance screening), which Validator 7 already
grounds against admitted evidence ids, downgrades reported-experience/tenure/
"absence of misconduct" to `qualified`, enforces ICFR scope/date, and keeps
credit direction / disposition / admissibility in separate fields. Section B
carries those signals through with their verification and limitations; a neutral
finding is kept distinct from insufficient evidence. The structural signals in
Section A (e.g. illustrative guarantor coverage) carry explicit qualifications
("a guarantor marking does not establish an enforceable guarantee").

## 10. Validator 7 + integration test results

Command (targeted):
`env -C <worktree> DEBUG=false uv run --no-sync --python 3.11 python -m pytest tests/unit/test_bo_m3_corporate_structure.py tests/integration/test_bo_m3_corporate_structure_integration.py "tests/unit/test_bo_remediation.py::test_case_coverage_checks_are_data_and_state_no_conclusion" -q`
→ **29 passed**.

Full offline suite:
`env -C <worktree> DEBUG=false uv run --no-sync --python 3.11 python -m pytest -q`
→ **1351 passed, 131 skipped, 12 errors** in ~101s.

- The 12 errors are all `tests/integration/test_bo_recorded_completion.py`,
  which require a genuine locally-generated artifact
  `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/.../specialists_run.json` that is
  not tracked in git and not present in this worktree. Verified by stashing all
  my changes and re-running: the same 12 errors occur on the clean base, so they
  are **environmental and pre-existing**, not regressions.
- No existing test expectation was weakened. One guard
  (`test_case_coverage_checks_are_data_and_state_no_conclusion`, which forbids the
  token `HCA`/`Galen`/`Stryker` in `app/services/bo/*.py`) initially failed
  because of docstring text in my new files; I removed those tokens so the
  production code stays company-agnostic and the guard passes.
- 131 skips are environmental (benchmark workbook / HCA filing bundle / optional
  providers / PDF backend not present).

## 11. Known limitations / unresolved issues / next steps

- **Deterministic validation ≠ correctness.** Passing all structural checks does
  not guarantee the structure is economically or legally correct; the HCA
  structure is a **simulated** educational example (entities, percentages,
  borrower designation and guarantor markings are illustrative, not verified).
- Structural credit signals are intentionally descriptive and conservative;
  several are `insufficient_evidence` because the diagram alone cannot establish
  cash access, distribution restrictions or guarantee enforceability.
- SVG text wrapping uses a fixed average-glyph estimate (no real font metrics) to
  stay deterministic; exact pixel wrapping may differ from a font-aware renderer.
- PNG export is a no-op unless a rasteriser (e.g. cairosvg) is installed.
- The recorded-completion acceptance tests still require the local R5 artifact;
  generating/committing a sanitised fixture would let those run in CI (out of
  scope here, and would touch Module-boundary artifacts).
- Next steps: surface the Section A SVG in the BO workbench UI, and register the
  corp-structure source through the formal ingestion/onboarding path so
  `inputs["corporate_structure_source"]` is populated by the pipeline rather than
  by the caller.

## 12. Git branch + working-tree status

Branch `feat/m3-corporate-mgmt`. Work committed (see commit message for the
command list and counts). Base branch `daniels-module-3` was not committed,
pushed, merged, or made into a PR.
