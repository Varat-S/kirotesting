# Module 3 Standalone — Implementation Plan (gap-fill, targeted improvement)

Branch: `feat/m3-standalone` (off `feat/m3-v6-refactor`). Worktree:
`/Users/Daniel/Desktop/SMU/AI in Fin/Credit Memo Testing/kirotesting-integrated/.worktrees/m3-standalone`.
Run tests with the worktree as cwd:
`DEBUG=false .venv/bin/python -m pytest <paths> -q`.

This is a TARGETED IMPROVEMENT, not a rebuild. Preserve the single specialist
`m3_corporate_management`, Validator 7 and its interfaces, the narrative contract
`app/schemas/bo_m3.py`, evidence-admission/provenance, missing-JSON safeguards,
and the credit-signal vocabulary. All schema changes are additive/versioned and
backward compatible. No downstream/out-of-scope work. Offline only; mock/scripted
model responses (`ScriptedBoBackend`, or recorded-proposal replay); no paid calls.

## What already satisfies the requirements (verified by reading code + green tests)

- v1.0 onboarding parse/validate/transform/round-trip:
  `app/services/bo/corporate_structure_onboarding.py` (`build_onboarding_graph`,
  `transform_to_refined_v2`, `reconcile_roundtrip`) and
  `app/services/bo/corporate_structure.py` (`build_corporate_structure_artifact`,
  `CaseCorporateStructureSource`). Rejects out-of-range %, duplicate ids, cycles,
  malformed hierarchies, invalid guarantor flags, missing mandatory role/ownership,
  wrong-case association (fail closed with exact `NO_JSON_MESSAGE`). HCA verifies
  14 entities / 13 links / 5 guarantors, 2 aggregate groups flagged distinct from
  legal persons; byte-deterministic; matches the reference final output exactly.
  Covered by `tests/unit/test_bo_m3_onboarding_transform.py` (26) and
  `tests/unit/test_bo_m3_corporate_structure.py` (29).
- Narrative Validator 7: `app/services/bo/m3_validation.py::validate_m3_assessment`
  — evidence integrity, borrower fail-closed, ICFR scope limits, management-proxy
  downgrade, direction/verification kept separate, insufficient-evidence handling.
- Two deliverables builder: `app/services/bo/m3_artifacts.py::build_m3_deliverables`
  (+ `m3_output.py::assemble_m3_output` sections A/B/C + completion status).
- Admission seam + completion wiring: `completion.py::admit_corporate_structure_source`,
  `build_m3_corporate_structure`, `_module_conclusions`, `write_completion_artifacts`.
- Senior-management semantic harness: `semantic_entailment.py`, `m3_semantic_eval.py`,
  fixtures `examples/bo_m3/independent_evaluation/` (18 developer-authored cases,
  `awaiting_external_review`). Real HCA 10-K htm files tracked under
  `examples/healthcare/hca-2025/filing/0001193125-26-044769/` (incl. ex21/ex22).

## Actual gaps to fill

1. **No standalone M3-only execution pathway / CLI.** The runner runs all BO
   specialists; only `run_m1s2_mode` is single-specialist. There is no `run_m3`
   and no CLI subcommand, and the normal runner never attaches
   `corporate_structure_source`.
2. **Exported `m3_corporate_structure.json` violates the v2.0 output contract.**
   `build_m3_corporate_structure_artifact` nests the v2.0 payload under
   `refined_structure` beside `artifact_kind`/`artifact_id`/`provenance`/
   `roundtrip_reconciliation`. The exported file must have `schema_version`,
   `artifact_type`, `visual_spec`, `companies` at ROOT; provenance/audit goes to a
   sidecar.
3. **Senior-management reasoning + Validator 7** need the explicit
   credibility-vs-credit-direction distinction enforced and tested, plus the
   hardening points in §VALIDATOR below (completion-status consistency; rejected
   findings excluded from the authoritative conclusion; citation-id ≠ entailment).
4. **No standalone demo dir / evaluation_scorecard.json / run_manifest.json**, and
   no real-10-K management-claim benchmark.

---

## Ordered implementation items

- [ ] 1. **Fix the exported corporate-structure output contract (de-wrap) + sidecar.**
      In `app/services/bo/m3_artifacts.py`, add `build_m3_corporate_structure_export`
      returning the refined v2.0 payload AT ROOT (`schema_version`, `artifact_type`,
      `visual_spec`, `companies`, plus the payload's own `purpose`/`how_to_use`/
      `generated_by`/`simulation_disclaimer`). Keep `build_m3_corporate_structure_artifact`
      as the INTERNAL record (artifact_id + provenance + roundtrip + refined payload)
      but rename its purpose to a governed sidecar `build_m3_structure_sidecar`
      (fields: `artifact_kind`, `artifact_id`, `is_simulated`, `provenance`,
      `roundtrip_reconciliation`, `document_hash`, `transform_version`, and a
      `structure_artifact_ref` to the export's stable id). `build_m3_deliverables`
      returns `m3_corporate_structure` (de-wrapped export), `m3_structure_sidecar`
      (governed record), `m3_analysis` (Deliverable B, references A by the same
      stable `artifact_id`). Keep the artifact id derivation stable.
      Files: `app/services/bo/m3_artifacts.py`
      Verify: `DEBUG=false .venv/bin/python -m pytest tests/unit/test_bo_m3_onboarding_transform.py -q` — update the deliverable-shape assertions (item 3) and confirm pass.

- [ ] 2. **Write the de-wrapped file + sidecar from completion + propagate.**
      In `completion.py::write_completion_artifacts`, write `m3_corporate_structure.json`
      from the de-wrapped export (root = v2.0 shape) and a new
      `m3_structure_provenance.json` sidecar from the governed record (only when a
      validated structure exists). In `_module_conclusions`, attach
      `m3_structure_sidecar` alongside `m3_corporate_structure`/`m3_analysis`. The SVG
      stays optional and never replaces Deliverable A.
      Files: `app/services/bo/completion.py`
      Verify: `DEBUG=false .venv/bin/python -m pytest tests/integration/test_bo_m3_v6_integration.py -q` (after updating item 3).

- [ ] 3. **Update existing tests that encode the OLD wrapped shape (correct, not weaken).**
      In `tests/integration/test_bo_m3_v6_integration.py::test_both_deliverables_written_to_disk_when_available`
      change `structure["refined_structure"]["schema_version"] == "2.0"` to
      `structure["schema_version"] == "2.0"` and `structure["companies"]` at root;
      assert `m3_structure_provenance.json` exists and that B's
      `corporate_structure_reference.artifact_id` equals the sidecar `artifact_id`.
      In `tests/unit/test_bo_m3_onboarding_transform.py::test_deliverables_structure_and_analysis_linked_when_available`
      assert the de-wrapped root shape on `m3_corporate_structure` and that the sidecar
      carries provenance + roundtrip. Add a NEW test asserting the exported
      `m3_corporate_structure.json` root keys exactly equal the reference
      `Sample HCA Corp Structure Final Output.json` root key SET (schema_version,
      artifact_type, visual_spec, companies present; no `refined_structure`/
      `artifact_kind` at root). Add an export-vs-reference structural-fact equality
      test (nodes/links/guarantors/plain_text_tree) already proven in-memory — assert
      it on the EXPORTED object.
      Files: `tests/integration/test_bo_m3_v6_integration.py`, `tests/unit/test_bo_m3_onboarding_transform.py`
      Verify: both test modules pass.

- [ ] 4. **Strengthen Validator 7: completion-status consistency.**
      In `m3_output.py::_completion_status` / `_section_c`, make a run with no
      structure AND no management assessment `incomplete`, and NEVER report
      `complete` while any material critical analytical component is missing (A
      unavailable OR B not assessed). Preserve the existing `partial` behaviour (A
      unavailable, B assessed). Keep direction/verification/availability in separate
      fields.
      Files: `app/services/bo/m3_output.py`
      Verify: `DEBUG=false .venv/bin/python -m pytest tests/unit/test_bo_m3_corporate_structure.py -q`. Add a test: A unavailable + B unavailable ⇒ `incomplete` (not partial/complete).

- [ ] 5. **Strengthen Validator 7: exclude REJECTED material findings from the authoritative conclusion; keep them in the audit trail.**
      In `m3_artifacts.py::build_m3_analysis_artifact`, ensure `consolidated_conclusion`
      and `material_credit_implications` draw ONLY from findings whose disposition is
      not `rejected`/`unverified` at material level, while `evidence_verification`
      (validation_issues + rejected_items) retains every rejected/unresolved item.
      Add an explicit `human_review_required` disposition surfaced in section C where
      the schema supports it (no new enum values if avoidable; reuse existing
      `Disposition`). Do NOT force equal pos/neg counts.
      Files: `app/services/bo/m3_artifacts.py`, (if needed) `app/services/bo/m3_output.py`
      Verify: new unit test in `tests/unit/test_bo_m3_corporate_structure.py` — a rejected material management finding appears in `evidence_verification.rejected_items` but NOT in `consolidated_conclusion`.

- [ ] 6. **Strengthen senior-management reasoning: credibility ≠ credit direction.**
      In `m3_validation.py::_govern_finding` for `management_assessments`, add an
      explicit, SEPARATE preservation of (a) statement credibility/verification
      (`disposition`) and (b) the credit direction of the underlying observation
      (`credit_direction` on the ManagementObservation). Add a validation reason when
      a credibly-explained ADVERSE development is still credit-negative, so a credible
      explanation is never allowed to flip the credit direction to positive/neutral.
      Preserve the per-claim audit fields already in the `ManagementObservation`
      schema (claim id, assertion, source doc + period, evidence refs, supporting +
      contradictory evidence, credit_mechanism, disposition, materiality,
      qualifications, missing_information). If a field is genuinely missing for this
      distinction, add it ADDITIVELY to `app/schemas/bo_m3.py` with a default (e.g.
      `credit_direction: Direction` on `ManagementObservation`, defaulted to
      `Direction.NEUTRAL`, and a `credibility_vs_direction_note: str | None`).
      Files: `app/services/bo/m3_validation.py`, `app/schemas/bo_m3.py`
      Verify: `DEBUG=false .venv/bin/python -m pytest tests/integration/test_bo_m3_v6_integration.py -q` plus a NEW test proving the distinction (below, item 11).

- [ ] 7. **Validator 7: citation-id existence is NOT entailment.**
      Where `validate_m3_assessment` has `evidence_texts` available, route material
      management/structural claims that assert a specific value/period/entity through
      the existing `semantic_entailment` DETERMINISTIC checks (numeric/period/entity)
      only; where semantic entailment cannot be deterministically established, keep the
      existing conservative disposition (downgrade to `unverified`/`qualified` +
      recorded limitation + human-review) rather than claiming verification. Do NOT
      add live-model entailment. Reuse `DeterministicCheck` from `semantic_entailment.py`.
      Files: `app/services/bo/m3_validation.py`
      Verify: new unit test — a claim citing a real evidence id but with a mismatched
      number/period is downgraded (not accepted), while a faithful claim is retained.

- [ ] 8. **Add the standalone M3 execution pathway `run_m3`.**
      New module `app/services/bo/m3_standalone.py` with
      `run_m3(session, *, case_id, evidence, corporate_structure_document=None,
      m3_responses, transaction_borrower_entity_id=None, output_dir=None)`. It MUST:
      invoke ONLY the `m3_corporate_management` specialist via the existing sealed-
      packet prepare/run path (reuse `comparison.prepare_specialist` +
      `run_single_pass` exactly as `single_specialist.run_m1s2` does, but with
      `V3_SPEC_BY_CODE["M3"]`), attach `corporate_structure_source` via
      `completion.admit_corporate_structure_source` when a document is supplied,
      validate the proposal with `validate_m3_assessment`, build the structure
      artifact with `build_corporate_structure_artifact`, assemble A/B/C with
      `assemble_m3_output`, and build deliverables with `build_m3_deliverables`
      (de-wrapped). It MUST NOT run any other BO specialist, Agent 6, the orchestrator,
      challenger, synthesis or any downstream agent. Fail closed on missing/invalid
      inputs. Return a result object carrying analysis_run_id, packet_hash, the two
      deliverables, the sidecar, validation issues, and a run manifest (case id,
      source doc ids + hashes, specialist/validator/prompt/roster/transform versions,
      model/execution settings, output validation status, `model_called` flag).
      Files: `app/services/bo/m3_standalone.py`
      Verify: new integration test (item 12) runs it offline and asserts only M3 ran.

- [ ] 9. **Add the CLI entry point following repo conventions.**
      Add a `run-m3` subcommand to `app/cli.py` (mirrors `run-bo-specialists`:
      `case_id`, `--package`/`--bundle` + `--cutoff`, `--corporate-structure`
      (path to the simplified v1.0 onboarding JSON), `--responses` (scripted offline)
      or `--live` (guarded), `--transaction-borrower-entity-id`, `--output`). Wire a
      `_run_m3(session, database, args)` helper that loads the document, builds the
      evidence via the existing pipeline prepare path, calls `run_m3`, writes the
      standalone demo dir, and prints a JSON summary. Keep it offline-first; `--live`
      requires the explicit provider and is not exercised in tests.
      Files: `app/cli.py`
      Verify: `DEBUG=false .venv/bin/python -m app.cli run-m3 --help` exits 0 and shows the flags; covered functionally by item 12.

- [ ] 10. **Standalone demo dir + evaluation_scorecard.json + run_manifest.json.**
      New module `app/services/bo/m3_demo.py` with `write_m3_demo(result, output_dir)`
      that writes `m3_corporate_structure.json` (de-wrapped), `m3_analysis.json`,
      `m3_structure_provenance.json`, `validation_report.json` (Validator 7 issues +
      rejected items + dispositions), `evaluation_scorecard.json` (EXPLICIT
      denominators + test populations for corporate-structure accuracy and management
      analytical quality; computed via `m3_evaluation.evaluate_architecture` and
      `m3_semantic_eval.evaluate`; never labelled perfect merely because a scripted
      set passes), and `run_manifest.json` (the manifest from item 8 + the EXACT
      reproduce command string). Reuse `canonical_json` for byte-stable output.
      Files: `app/services/bo/m3_demo.py`, (write hook) `app/cli.py`
      Verify: item 12 asserts all five files exist with correct root shapes.

- [ ] 11. **Senior-management benchmark from REAL admitted HCA 10-K excerpts (provisional).**
      Extend the existing `examples/bo_m3/independent_evaluation/` pattern: add
      `examples/bo_m3/management_benchmark/cases.json` with ~5–10 MATERIAL management
      claims whose passages are VERBATIM excerpts from the tracked HCA 10-K
      (`examples/healthcare/hca-2025/filing/0001193125-26-044769/hca-20251231.htm`,
      `hca-ex21.htm`, `hca-ex22.htm`) — each with exact source location/anchor,
      reporting period, the credibility label AND the separate credit-direction label,
      `label_status: developer_authored_awaiting_external_review`, and a manifest with
      source sha256 hashes. Include at least one case where management credibly
      explains an ADVERSE development (credible statement, still credit-negative) and
      one unsupported causal-attribution case. Label all LLM-generated interpretation
      PROVISIONAL. Do NOT invent claims; if a passage cannot be located verbatim, drop
      it. If no offline admitted-evidence path turns out to be feasible for a case,
      mark that case SCRIPTED/MOCK and state mocked output is not proof of real
      performance.
      Files: `examples/bo_m3/management_benchmark/cases.json`, `.../manifest.json`, `.../README.md`
      Verify: new unit test (item 13) checks source hashes resolve and the
      credibility-vs-direction labels are both present and independent.

- [ ] 12. **End-to-end standalone offline test.**
      New `tests/integration/test_bo_m3_standalone.py`: run `run_m3` offline with a
      `ScriptedBoBackend` and the simplified HCA onboarding doc; assert the two
      deliverables are produced (A de-wrapped root shape, B references A), the sidecar
      exists, all five demo files are written, the run_manifest records `model_called`
      appropriately + versions + source hashes, and — critically — assert that NO
      other BO specialist and NO downstream agent ran (inspect persisted
      `AgenticAnalysisRun`/audit: only `m3_corporate_management`). Add missing-input
      (no onboarding JSON ⇒ A unavailable with `NO_JSON_MESSAGE`, B still assessed,
      status partial) and wrong-case (fail closed) cases. Add a reproducibility test:
      identical simplified JSON ⇒ byte-identical exported structure (hash compare).
      Files: `tests/integration/test_bo_m3_standalone.py`
      Verify: `DEBUG=false .venv/bin/python -m pytest tests/integration/test_bo_m3_standalone.py -q`.

- [ ] 13. **Senior-management + Validator 7 unit tests (analytical reliability).**
      New `tests/unit/test_bo_m3_senior_management.py` covering: supported; contradicted;
      neutral; mixed; insufficient-evidence (distinct from neutral); CORRECT financial
      values with UNSUPPORTED causal attribution (qualified/rejected, not accepted);
      numerical discrepancy; mismatched reporting period; unsupported execution claim;
      accurate disclosure of adverse performance (credible statement STILL
      credit-negative — the credibility-vs-direction distinction from item 6);
      unsupported/fabricated citation; rejected material finding EXCLUDED from the
      authoritative conclusion (item 5); incorrect completion status corrected (item 4).
      Plus the management-benchmark provenance test (item 11). Reuse the controlled
      judge/scripted pattern from `test_bo_m3_services.py` and `m3_semantic_eval.py`.
      Files: `tests/unit/test_bo_m3_senior_management.py`
      Verify: `DEBUG=false .venv/bin/python -m pytest tests/unit/test_bo_m3_senior_management.py -q`.

- [ ] 14. **Analytical-consistency (stability) test across repeated runs.**
      In `tests/integration/test_bo_m3_standalone.py` (or a sibling), run `run_m3`
      twice with a ScriptedBoBackend whose script returns the SAME proposal, assert
      the governed MATERIAL outputs (claims/evidence/verification/direction/materiality/
      contradictions/gaps/conclusion) are materially identical; add a second scripted
      backend returning a MATERIALLY different proposal on the second call and assert
      the harness REPORTS the instability (does not silently average). Do not demand
      word-for-word identical LLM text.
      Files: `tests/integration/test_bo_m3_standalone.py`
      Verify: that module passes.

- [ ] 15. **Full offline suite + report.**
      Run the FULL offline suite and record exact pass/skip/fail/error counts. The 12
      known errors come solely from `tests/integration/test_bo_recorded_completion.py`
      (untracked `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/...`); distinguish them
      from any NEW regression. Weaken NO existing expectation (test edits in item 3 are
      contract CORRECTIONS of the de-wrap, not weakenings). Clean up any temp files.
      Files: none (verification only)
      Verify: `DEBUG=false .venv/bin/python -m pytest -q` — new M3 tests pass; totals
      vs baseline (1384 passed / 131 skipped / 12 errors) show only the 12 known
      errors, zero new failures.

## Additive/versioned/backward-compatibility notes

- New export shape is produced by a NEW function; the internal artifact record is
  retained (renamed to a sidecar), so existing readers of the in-memory record keep
  working. Add a new `generated_by`/version stamp if needed; do not bump v2.0 of the
  presentation contract (the exported shape already matches the reference).
- Any `ManagementObservation` field added in item 6 is optional with a default
  (strict models use `extra="forbid`, so only add fields, never require them).
- `run_m3` and `run-m3` are new; they do not touch the v2 default run or `run_m1s2_mode`.
- The management benchmark is new fixtures; labels are `awaiting_external_review`.

## Out of scope (do not build)

Modules 1/2/4, cross-module synthesizer, independent challenger, synthesis revision,
final cross-module validation, memo renderer, HTML/PDF/DOCX, visualisation UI,
onboarding UI, lending-recommendation engine, any new specialist or scoring/weighting
framework, any live/paid model call.
