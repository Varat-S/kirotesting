# Module 3 — Layer 2 Evidence-Grounded Structural Credit Reasoning — Final Report

## 1. Summary

This is a targeted extension (not a rebuild) of Business Overview Module 3
(Corporate Structure & Senior Management). It adds **Layer 2 — Evidence-Grounded
Structural Credit Reasoning** on top of the existing deterministic Layer 1
extraction, and fixes the specialist-ordering bug so the validated corporate
structure reaches the ONE specialist `m3_corporate_management` **before** it
reasons. No new agent was added; Validator 7 was extended (not replaced). The
deterministic `_derive_structural_signals()` remains the sole factual extractor;
the LLM only interprets credit implications and must reference exact stable
node/link ids. The final `m3_analysis.json` now visibly distinguishes factual
deterministic observations from validated AI structural credit findings. Pillar
B (senior management) is unchanged.

Plainly stated: deterministic validation does NOT guarantee economic or legal
correctness; the HCA structure is **simulated**; the scripted tests do not prove
real-world analytical reliability; and live analytical capability is
**UNVERIFIED** (no live model was run).

## 2. Files created / modified

Created:
- `app/services/bo/m3_structure_context.py` — `build_structural_facts()`: the
  compact, provenance-preserving structure representation injected into the
  specialist context.
- `tests/unit/test_bo_m3_structure_context.py` — unit coverage of the builder.
- `tests/integration/test_bo_m3_structural_credit.py` — T1–T12 + ordering-proof
  + T-demo.
- `examples/bo_m3/structural_credit/` — scripted/offline fixtures:
  `hca_simulated_single_pass.json`, `fictional_synthetic_structure.json`,
  `fictional_positive_single_pass.json`, `fictional_negative_single_pass.json`,
  `fictional_mixed_single_pass.json`,
  `fictional_unsupported_guarantee_single_pass.json`, `README.md`,
  `generate_demo.py`, and the committed `demo_output/<case>/` six-file sets.

Modified:
- `app/schemas/bo_m3.py` — additive Layer-2 fields on `StructuralCreditFinding`;
  version bump to `bo-m3-corporate-assessment-1.1` / `validator-7-1.1.0` with
  1.0 still accepted (backward compatible).
- `app/prompts/bo_prompts.py` — mirrored the additive optional finding fields;
  widened the `schema_version` enum; appended Layer-2 evidence standards + the
  simulation safeguard to the M3 specialist instructions.
- `app/services/bo/comparison.py` — optional `PreparedSpecialist.structural_facts`;
  `_single_pass_inputs` includes a `corporate_structure` block only when present
  (M1/M2 inputs unchanged).
- `app/services/bo/m3_standalone.py` — the critical reorder (see §3).
- `app/services/bo/m3_validation.py` — Validator 7 structural cross-checks (§5).
- `app/services/bo/m3_output.py` — Section A carries `ai_structural_credit_findings`
  separately from deterministic observations.
- `app/services/bo/m3_artifacts.py` — `m3_analysis.json` exposes the full AI
  findings and `admitted_structural_credit_finding_ids`; rejected/unverified
  items retained under `evidence_verification`.
- `app/services/bo/m3_demo.py` — `structural_credit_quality` scorecard section
  with explicit numerator/denominator/population; `expected_labels` arg.

## 3. How the corporate structure now reaches the specialist (ordering fix + proof)

Previously `run_m3` ran `prepare_specialist -> run_single_pass` (specialist
executed) and only then admitted + built the structure. The structure was never
in the specialist's input context.

`run_m3` now runs, in order: (1) admit the case-associated simplified structure
JSON (`admit_corporate_structure_source`); (2) validate the onboarding doc +
build the canonical graph + derive deterministic observations
(`build_m3_corporate_structure`, which runs `_derive_structural_signals`);
(3) build the compact provenance-preserving representation
(`build_structural_facts`) with validated nodes+links (stable ids), borrower
id+role, ownership percentages, guarantor indicators, grouping classifications,
source doc id+hash and simulation status + the deterministic observations;
(4) inject it via `prep = replace(prep, structural_facts=...)` so
`_single_pass_inputs` carries a `corporate_structure` block; (5) **then**
`run_single_pass` executes the specialist; (6) Validator 7 runs with
`structure_facts`; (7) governed JSON is produced. The manifest records
`execution.structure_in_specialist_context`.

Proof test: `test_ordering_structure_in_specialist_context_before_pass`
monkeypatches `run_single_pass` to capture `prep.structural_facts` and the
actual `_single_pass_inputs` dict BEFORE the real pass returns, and asserts the
captured input contains a `corporate_structure` block with node ids AND link ids
and the deterministic observations.

## 4. The structural credit reasoning framework

Each material structural credit finding (additive, backward-compatible fields on
`StructuralCreditFinding`) captures: stable `finding_id`; `analytical_dimension`
(borrower_position, guarantor_coverage, cash_flow_access,
operating_debt_structure, minority_or_jv, structural_complexity);
`structural_observation`; `referenced_node_ids`; `referenced_link_ids`;
`onboarding_source_reference`; supporting/contradicting evidence ids; the
analytical `credit_hypothesis` + `causal_mechanism`; `credit_direction`
(positive/negative/neutral/mixed); `disposition` + `admissibility` (existing
enums); `materiality`; `confidence`; `qualifications`; `missing_information`; and
`assessment_availability` (`insufficient_evidence` kept SEPARATE from neutral).
Dimensions A–F are covered in the prompt and enforced in Validator 7: structural
subordination is never inferred from holdco layers alone; a guarantor flag never
proves an enforceable guarantee; ownership % alone never proves cash access;
minority/JV and complexity are never auto-negative; no invented subsidiary
debt/collateral and no debt-schedule/refinancing analysis.

## 5. Validator 7 improvements (extend, not replace)

`validate_m3_assessment(..., structure_facts=None)` — default keeps all existing
callers unchanged. When `structure_facts` is supplied, structural credit findings
are cross-checked (`_govern_structural_finding`):
- FACTUAL: `m3.structural_reference_integrity` (unknown node/link ids),
  `m3.structural_percentage_mismatch`, `m3.structural_borrower_invented`,
  `m3.grouping_node_not_legal_entity`.
- GUARANTOR: `m3.guarantee_not_proven_by_flag` (positive guarantee with no
  admitted legal/debt evidence; enforceability-as-fact → rejected),
  `m3.simulated_guarantor_not_verified`.
- CASH-FLOW: `m3.cash_access_not_proven_by_ownership`, `m3.cash_restriction_unknown`.
- SIMULATION ISOLATION: `m3.simulation_isolation`.
- MINORITY/JV + COMPLEXITY: `m3.minority_not_auto_negative`,
  `m3.complexity_not_auto_negative`.
All existing checks (evidence sufficiency/integrity, deterministic numeric/period
entailment, management credibility-vs-direction, ICFR scope) are retained.
Dispositions stay in the existing vocabulary (supported/qualified/rejected/
unverified; human-review via `requires_human_review`). Rejected and
material-unverified findings are excluded from the authoritative conclusion by
the existing `_is_admitted` gate but retained in `validation_issues` /
`rejected_items` / `evidence_verification` for audit.

## 6. Example findings (supported/positive, negative, neutral, mixed, insufficient)

- **Qualified positive** — `fictional_positive` SCF-POS-GUARANTOR: guarantor
  support with an admitted guaranty agreement → `qualified` positive.
- **Defensible negative** — `fictional_negative` SCF-NEG-CASHFLOW: admitted
  intercompany-distribution restriction → `qualified` negative with a mechanism.
- **Neutral / insufficient** — HCA SCF-HCA-BORROWER / SCF-HCA-CASHFLOW: neutral
  direction, `assessment_availability=insufficient_evidence`, `unverified`.
- **Mixed** — `fictional_mixed` SCF-MIX-POS + SCF-MIX-NEG coexist, not balanced.
- **Rejected/qualified unsupported** — `fictional_unsupported_guarantee`
  SCF-BAD-GUARANTEE: enforceable guarantee from a flag alone → `unverified` with
  `m3.simulated_guarantor_not_verified` + `m3.evidence_integrity`.

## 7. Demonstration `m3_analysis.json`

`examples/bo_m3/structural_credit/demo_output/<case>/m3_analysis.json` for each of:
`hca_simulated`, `fictional_positive`, `fictional_negative`, `fictional_mixed`,
`fictional_unsupported_guarantee`. Each separates `structural_observations`
(deterministic) from `ai_structural_credit_findings` (validated AI) and lists
`admitted_structural_credit_finding_ids` in the consolidated conclusion.

## 8. Demonstration `m3_corporate_structure.json`

`examples/bo_m3/structural_credit/demo_output/<case>/m3_corporate_structure.json`
— the de-wrapped refined v2.0 contract, unchanged (no AI commentary in the
diagram JSON).

## 9. Validation report + scorecard paths

Per case under `demo_output/<case>/`: `validation_report.json` and
`evaluation_scorecard.json` (includes the `structural_credit_quality` section
with explicit numerator/denominator/population), plus
`m3_structure_provenance.json` and `run_manifest.json` (manifest embeds the exact
`run-m3` reproduce command and `structure_in_specialist_context`).

## 10. Evidence-grounding + reproducibility

Findings reference stable node/link ids end-to-end; the de-wrapped Deliverable A
export is byte-reproducible across independent runs (existing test retained). The
scorecard `analytical_consistency.determinism_hash` is stable over identical
evidence; any material instability is reported, not silently reconciled (existing
standalone test retained). A valid citation id is not treated as substantive
proof (deterministic entailment + the new structural cross-checks).

## 11. Senior-management regression

Pillar B is unchanged: `test_t12_senior_management_unchanged` asserts the HCA
board observation keeps its `qualified` disposition and enters the consolidated
conclusion; the credibility-vs-direction framework and the existing
`test_bo_m3_senior_management.py` / `test_bo_m3_services.py` suites pass
unchanged.

## 12. Full offline test results

Commands (run from the worktree with cwd set to the worktree):
- `uv sync --locked --python 3.11 --extra dev`
- New/targeted first: `DEBUG=false .venv/bin/python -m pytest
  tests/integration/test_bo_m3_structural_credit.py
  tests/unit/test_bo_m3_structure_context.py -q` → 16 passed.
- Full offline suite: `DEBUG=false .venv/bin/python -m pytest -q` →
  **1426 passed, 131 skipped, 12 errors**.

Baseline on `feat/m3-standalone` was 1410 passed / 131 skipped / 12 errors. The
12 errors are the KNOWN pre-existing environmental issue in
`tests/integration/test_bo_recorded_completion.py` (a module-scope fixture that
requires a "Genuine HCA R5 artifact" / recorded-specialists artifact that is
absent on this branch) — NOT regressions and NOT passing tests. Passed count rose
from 1410 to 1426 solely due to the 16 new tests. **Zero new failures/errors.**

## 13. Remaining limitations

- Deterministic validation does not guarantee economic or legal correctness; it
  checks referential integrity, evidence admissibility and conservative
  entailment only.
- The HCA structure is SIMULATED; the Larkspur fixtures are FICTIONAL. The
  scripted specialist responses have developer-authored expected outcomes.
- No live model was run; live analytical capability is UNVERIFIED. The scorecard
  reports `actual_llm_semantic_accuracy = not_measured`.
- The full-pipeline (`completion.py`) M3 path still passes `structure_facts=None`
  (unchanged this round); the ordering fix + structural cross-checks apply to the
  standalone `run_m3` path.

## 14. Git branch + worktree status

- Worktree: `.worktrees/m3-structural-credit`; branch `feat/m3-structural-credit`
  (off `feat/m3-standalone`). Work committed ONLY to this branch. No push, no PR,
  no merge, no commit to any other branch.
