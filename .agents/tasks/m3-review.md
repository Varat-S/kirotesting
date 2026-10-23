# Module 3 — Analytical Quality, Validator 7 & Real-Model Readiness — Review

Meaningful consolidated conclusion, four-state Validator 7, and an opt-in real-model pathway for the existing standalone Module 3 specialist (`m3_corporate_management`).

This is a targeted enhancement of the existing Module 3 on branch `feat/m3-analytical-quality` (one commit `aa0afde` on top of base `feat/m3-structural-credit`). It rewrites the consolidated conclusion assembler in `m3_output.py`, strengthens Validator 7 numeric/evidence verification in `m3_validation.py`, and adds a default-off live-model path in `m3_standalone.py`/`cli.py`. No module was rebuilt; no new specialist/validator agent was introduced; the schema is unchanged. The five offline demos were regenerated and are byte-stable.

Watch for: nothing blocking. The conclusion is now materially differentiated across scenarios, traces to validated finding ids, excludes rejected findings, and preserves the HCA simulation disclaimer (confirmed). The live path is wired but **was not executed** — no paid call was made, and live analytical capability is honestly reported as UNVERIFIED (confirmed). The canonical-financial checks are surface/token + resolved-value based and cannot prove semantic entailment or causation; causal/legal claims are correctly capped at human review (confirmed).

**Verdict**: APPROVED

<details>
<summary>Issues (0 blocking)</summary>

No blocking findings. Informational notes (not action items):

1. **Canonical accounting-basis/currency fields carried but not fully populated** (confirmed, informational) — `_canonical_metrics_from_prep` sets `accounting_basis` and `currency` to `None` because the current indicator layer does not expose them; basis/currency mismatches therefore remain a human-review responsibility rather than a deterministic check. The report discloses this (section 13). Acceptable for this task; not a regression.
2. **Live path unverified against a real provider** (confirmed, expected by brief) — exercised only with `FakeLLMBackend`. This is the mandated posture (no paid calls); recorded as `real_llm_analytical_consistency: not_yet_evaluated`.

</details>

<details>
<summary>Details</summary>

## Consolidated conclusion made meaningful (Pillar A + Pillar B)

`m3_output.py::_section_c` was rewritten from a generic two/three-bullet availability summary into a deterministic, schema-driven assembler. It imports the single-source `_is_admitted` gate from `m3_artifacts.py` (no duplicated logic) and admits only material (`high`/`medium`) findings that survive Validator 7; rejected, material-unverified, and insufficient-evidence findings never contribute. For each admitted finding `_finding_statement` emits a structured object carrying `source_id`, `core_observation`, `credit_direction`, `credit_risk_mechanism`, `materiality`, `supporting_evidence_ids`, `qualifications`, `unresolved_information_gaps`, and a `why_it_matters_to_a_lender` sentence keyed on `(analytical_dimension, credit_direction)`. No LLM call was added; ordering is sorted by id, so re-assembly is byte-stable (`test_groupA_identical_governed_findings_reassemble_byte_identically`).

Reading the three regenerated `m3_analysis.json` demos confirms the conclusions now differ materially rather than being generic:

- **fictional_positive**: Pillar A emits a `[positive]` guarantor-coverage statement — "A guarantee from this entity may improve the lender's recourse ... subject to the guarantee's enforceability, ranking, caps ...".
- **fictional_negative**: Pillar A emits a `[negative]` cash-flow-access statement — "A restriction on upstream distributions may reduce the borrower's financial flexibility and constrain the cash resources available to service the proposed facility ...".
- **fictional_mixed**: BOTH the negative and positive statements are retained side by side, with the pillar summary stating "Supportive and adverse factors are preserved separately and are not netted into a single score." No numeric netting occurs.

`test_groupA_five_scenarios_are_materially_distinct` asserts the five commentaries are unique prose (`len(set(...)) == len(...)`), each >200 chars, and carries direction-specific mechanism language ("recourse" for positive, "restrict"/"constrain" for negative) — not merely finding-id lists or counts. The conclusion sets `is_lending_recommendation: false`, `requires_human_review: true`, `forced_outcome: false`.

Insufficient evidence is kept distinct from neutral: both `hca_simulated` and `fictional_unsupported_guarantee` render "a validated ownership hierarchy is available ... but no debt, guarantee or cash-distribution finding is sufficiently supported ... (insufficient evidence is not a neutral finding)", and `test_groupA_insufficient_evidence_is_not_neutral` guards it.

## Rejected-finding exclusion with audit retention

The `fictional_unsupported_guarantee` scenario's `SCF-BAD-GUARANTEE` does not appear in `consolidated_conclusion.conclusion_trace.pillar_a_source_ids` (empty), confirming it is excluded from the authoritative conclusion. It is retained for audit under `evidence_verification.excluded_from_conclusion_ids: ["SCF-BAD-GUARANTEE"]`. `test_groupA_rejected_finding_excluded_from_authoritative_conclusion` covers this. A qualified hypothetical is never promoted to an established fact.

## Simulation distinction preserved

Every Pillar-A statement derived from a simulated hierarchy sets `hypothetical_simulated_basis: true`, is prefixed with a hypothetical qualification, and the pillar summary appends "NOTE: the hierarchy is SIMULATED; every structural finding below is hypothetical and does not assert a real arrangement." The HCA demo commentary never asserts real 10-K debts/guarantees/restrictions; `test_groupA_simulated_scenario_retains_hypothetical_qualifications` and `test_t11_simulation_isolation` guard this.

## Validator 7: absence is no longer contradiction

The central Priority-2 correctness fix is in `m3_validation.py::_deterministic_claim_checks`. Previously a claimed token merely absent from the cited corpus was emitted with `contradiction=True`, which `_govern_finding` mapped to `rejected`. Now a claimed number/period that is absent with no conflicting token of the same class present yields `contradiction=False` → the caller downgrades to `unverified` (UNSUPPORTED); a conflicting value/period of the same class yields `contradiction=True` → `rejected` (CONTRADICTED).

The four verification states reuse the existing `Disposition` enum (no competing enum): VERIFIED→supported/qualified, UNSUPPORTED→unverified, CONTRADICTED→rejected, REQUIRES_HUMAN_REVIEW→qualified/unverified plus a recorded `m3.requires_human_review` reason. `test_unsupported_numeric_assertion_is_unverified_not_rejected`, `test_directly_contradicted_value_is_rejected`, `test_wrong_fiscal_period_is_contradicted_when_a_period_is_present`, and `test_absent_fiscal_period_with_no_period_present_is_unsupported` encode the mandated semantics directly. Two pre-existing senior-management assertions that encoded the old "absence=contradiction" bug were corrected.

## Canonical-financial verification, gated on availability

`_canonical_financial_checks` reuses the repo's resolved canonical values. `_canonical_metrics_from_prep` (in `m3_standalone.py`) builds the map from `prep.indicators.traces` (`ParameterResult` + `ResolvedMetricDefinition`) and never recomputes a metric — it exposes `value`/`raw_value`/`units`/`period` and a percentage view for ratio metrics so a "12%" claim reconciles with a stored 0.12. A conservative `_METRIC_ALIASES` map resolves claim phrasing to a canonical `metric_definition_id`; an unmapped or ambiguous phrase returns `None` and the check is skipped (falls back to the corrected textual gate, never fabricating). Checks cover metric identity, fiscal period, numeric value within tolerance, and pp-vs-% confusion, and guard dollars-vs-millions, quarterly-vs-annual, consolidated-vs-subsidiary, and revenue-vs-EBITDA-growth. `canonical_metrics` is threaded through `validate_m3_assessment` and `_govern_finding` as an optional parameter defaulting to `None`, so existing callers are unaffected. Group B (`test_dollars_vs_millions_do_not_spuriously_match`, `test_quarterly_vs_annual_period_distinguished`, `test_consolidated_vs_subsidiary_value_contradiction`, `test_revenue_growth_vs_ebitda_growth_not_conflated`, `test_rounding_and_tolerance_allows_close_values`, `test_missing_canonical_data_falls_back_without_fabrication`) covers these.

## Causal/legal claims capped at human review

A still-`supported` claim carrying causal (`because`, `due to`, `driven by`, ...) or legal (`enforceable`, `guarantees the`, `covenant`, ...) markers is downgraded to `qualified` with an `m3.requires_human_review` reason, even when the numbers agree — deterministic matching is not treated as proof of semantic entailment or causation. `test_numerically_correct_outcome_with_unsupported_causal_attribution` asserts a figure that matches canonical `revenue_growth` but attributes it to management is NOT promoted to `supported`. The `credit_direction` vs `credibility_vs_direction_note` separation is preserved (the `_MANAGEMENT_LENDER_RELEVANCE["negative"]` text states a credible explanation does not reverse an adverse direction).

## Opt-in real-model pathway (not executed)

`run_m3` gains optional `live_backend`/`live_config`; when `live_backend` is set it calls `run_single_pass(session, prep, backend=live_backend)` (the seam already existed in `comparison.py`), otherwise it drives the scripted `FakeLLMBackend`. The CLI `run-m3 --live` replaces the former hard `ValueError` with `_live_backend()` (pre-existing helper that constructs the configured Vertex/Real provider through `app/services/llm/providers*` and calls `check_configuration()`), reading model id/timeout/token limits from `Settings`; with no provider configured it raises a clear, credential-free blocker rather than inventing a run. Default remains offline (`--responses`).

Safeguards verified: disabled by default; bounded to one specialist pass (`max_retries` default 0 in `live_config`); timeout/token limits sourced from `Settings`; `run_single_pass` catches provider errors and returns a failed arm with no `parsed` output (fail-safe, no fabrication); there is NO silent fallback to scripted on live failure; the manifest records `execution_mode`, `real_model_invoked`, `model_called`, `specialist_pass_count`, `max_retries_configured`, and `cross_module_or_downstream_invoked: false`; credentials rely on `SecretStr` and are not logged/persisted. `_is_offline_call` treats a `fake`-prefixed model id as non-real so a fake backend never counts as a live invocation. Group C (`test_live_path_invokes_only_m3_corporate_management`, `test_schema_invalid_live_response_fails_safely`, `test_provider_error_fails_safely_without_fabrication`, `test_manifest_distinguishes_simulation_from_real_execution`) exercises the path with a fake backend only. No paid/live call was made in this task; the report states NONE were made.

## Outputs, schema, and senior-management framework preserved

`app/schemas/bo_m3.py` is byte-unchanged in the diff — the four states ride existing dispositions + validation issues, so the change is additive and backward compatible. Output A (`m3_corporate_structure.json`) remains the de-wrapped v2.0 presentation artifact: top-level keys are `artifact_type`/`companies`/`how_to_use`/`purpose`/`schema_version`/`simulation_disclaimer`/`visual_spec`, with no `integrated_commentary`, no `ai_structural_credit_findings`, and `purpose` explicitly "no debt tables or lender commentary" (the incidental "guarantor markings"/"shield"/"no lender commentary" strings are presentation instructions, not AI credit commentary). Output B keeps Sections A/B/C with stable finding ids. The senior-management framework (claims assessed from admitted disclosures, credit-direction-vs-credibility distinction, management-proxy/ICFR gates) is unchanged; Group D (`test_bo_m3_senior_management.py`, 36-line extension) passes with the two bug-encoding assertions corrected to the mandated semantics.

## Demos and scorecard

All five scenarios regenerate via `DEBUG=false PYTHONPATH=. .venv/bin/python examples/bo_m3/structural_credit/generate_demo.py`; a re-run leaves `git status --porcelain demo_output/` empty (byte-stable), confirmed in this review. The evaluation scorecard adds `conclusion_integration_accuracy` (with explicit numerator/denominator/population, `no_excluded_finding_promoted`, `traced_ids_are_all_admitted`), `numerical_verification_quality` (unsupported vs contradicted kept distinct, requires-human-review count), and `real_llm_analytical_consistency: not_yet_evaluated`. Each carries a caveat that it measures deterministic trace/token quality, not real-model semantic accuracy — i.e. software correctness is not represented as credit-analytical correctness.

## Test and git state

Targeted Group A/B/C/D suites: `tests/unit/test_bo_m3_validation_numeric.py tests/integration/test_bo_m3_live_readiness.py tests/integration/test_bo_m3_structural_credit.py tests/unit/test_bo_m3_senior_management.py` → **62 passed** (re-run in this review). Full offline suite `DEBUG=false .venv/bin/python -m pytest -q` → **1459 passed, 131 skipped, 12 errors** (re-run in this review), matching the report. The 12 errors are confirmed to be the known pre-existing env issue: every one is a setup `RecordedCompletionError: Recorded specialists artifact not found: output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/.../specialists_run.json` in `test_bo_recorded_completion.py`, absent on base too — NOT regressions, NOT counted as passing. Versus base (1426/131/12): +33 passed, same skips, same 12 env errors, zero new failures.

Git: single commit `aa0afde` on `feat/m3-analytical-quality`; working tree clean; no remote branch exists (not pushed, no PR); worktree left in place.

</details>

<details>
<summary>File map</summary>

- `app/services/bo/m3_output.py` — Priority 1: deterministic `_section_c` conclusion assembler + `_finding_statement`/`_pillar_summary`/lender-relevance maps.
- `app/services/bo/m3_validation.py` — Priority 2: absence≠contradiction fix, four-state mapping, `_canonical_financial_checks`, causal/legal cap, optional `canonical_metrics` thread-through.
- `app/services/bo/m3_standalone.py` — Priority 3: opt-in `live_backend`/`live_config`, `_canonical_metrics_from_prep`, manifest execution_mode/real_model_invoked/bounded-pass.
- `app/cli.py` — `run-m3 --live` constructs the provider via existing `_live_backend()`; offline `--responses` default; surfaces execution_mode/real_model_invoked.
- `app/services/bo/m3_demo.py` — scorecard extended (conclusion_integration_accuracy, numerical_verification_quality, real_llm_analytical_consistency=not_yet_evaluated).
- `examples/bo_m3/structural_credit/README.md` — offline reproduce command + separate NOT-run live example.
- `examples/bo_m3/structural_credit/demo_output/**` — regenerated five-scenario artifacts (byte-stable).
- `tests/unit/test_bo_m3_validation_numeric.py` (new) — Group B numeric verification.
- `tests/integration/test_bo_m3_live_readiness.py` (new) — Group C real-model readiness (fake backend only).
- `tests/integration/test_bo_m3_structural_credit.py`, `tests/integration/test_bo_m3_standalone.py`, `tests/unit/test_bo_m3_senior_management.py` — Group A/D/E extensions.
- `app/schemas/bo_m3.py` — unchanged (verified no diff).

Full diff: `git -C .worktrees/m3-analytical-quality diff feat/m3-structural-credit...feat/m3-analytical-quality`

</details>
