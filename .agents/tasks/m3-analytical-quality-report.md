# Module 3 — Analytical Quality, Validator 7 & Real-Model Readiness — Report

Branch: `feat/m3-analytical-quality` (worktree `.worktrees/m3-analytical-quality`,
off `feat/m3-structural-credit`). Targeted enhancement, not a rebuild.

Plain statement up front: **software correctness is NOT credit-analytical
correctness**. The HCA corporate structure is SIMULATED. **No live model was
run** in this task (no provider/credentials configured), so live analytical
capability is UNVERIFIED. Every finding and the consolidated conclusion remain
drafts requiring human credit-analyst review; nothing here is a lending
recommendation.

## 1. Summary

Three priorities were implemented against the existing standalone Module 3
specialist (`m3_corporate_management`) and Validator 7, preserving all existing
capabilities:

1. **Consolidated conclusion made meaningful.** `m3_output.py::_section_c` is now
   a deterministic, schema-driven assembler that, for each material finding that
   SURVIVES Validator 7, explains the core observation, credit direction,
   credit-risk mechanism, materiality, evidence references, qualifications,
   unresolved gaps and *why it matters to a lender* — across Pillar A (corporate
   structure) and Pillar B (senior management). The five demo scenarios now
   produce materially different conclusions; each statement traces to the
   governed finding id it rests on. No LLM call was added.
2. **Validator 7 numerical/evidence verification strengthened.** Absence of
   support is no longer treated as contradiction. The deterministic checks now
   distinguish VERIFIED / UNSUPPORTED (unverified) / CONTRADICTED (rejected) /
   REQUIRES_HUMAN_REVIEW, reuse the existing `Disposition` enum (no competing
   enums), add canonical-financial verification (reusing the repo's resolved
   metric values + definitions — no competing engine), guard unit/basis/period
   confusions, and cap causal/legal claims at human review.
3. **Opt-in real-model pathway implemented (not executed).** `run_m3` accepts an
   optional `live_backend`/`live_config`; the `run-m3 --live` CLI constructs the
   configured provider through the existing abstraction. Disabled by default,
   bounded to one M3 specialist execution, schema-validated, fails safely with
   no fabricated output and no silent fallback, and the manifest states whether a
   real model was invoked. No paid call is made.

## 2. Files created / modified

Modified:
- `app/services/bo/m3_output.py` — Priority 1 conclusion assembler (`_section_c`,
  new `_finding_statement`, `_pillar_summary`, lender-relevance maps).
- `app/services/bo/m3_validation.py` — Priority 2 four-state verification:
  absence≠contradiction in `_deterministic_claim_checks`; new
  `_canonical_financial_checks`, `_identify_canonical_metric`,
  `_is_causal_or_legal_claim`, `_percentage_point_confusion`; causal/legal
  human-review cap; `canonical_metrics` threaded through `_govern_finding` and
  `validate_m3_assessment`.
- `app/services/bo/m3_standalone.py` — Priority 3 opt-in `live_backend`/
  `live_config`; `_canonical_metrics_from_prep` wired into Validator 7 (benefits
  offline AND live); manifest records execution_mode / real_model_invoked /
  bounded-pass / retry safeguards; fail-safe model_called derivation.
- `app/cli.py` — `run-m3 --live` now constructs the provider backend via
  `_live_backend()` (clear credential-free blocker when unconfigured); offline
  `--responses` remains the default; execution_mode/real_model_invoked surfaced.
- `app/services/bo/m3_demo.py` — scorecard extended with
  `conclusion_integration_accuracy`, `numerical_verification_quality`, and
  `real_llm_analytical_consistency = not_yet_evaluated`.
- `examples/bo_m3/structural_credit/README.md` — regeneration, offline reproduce
  command, and a separate (NOT-run) real-model invocation example.
- `examples/bo_m3/structural_credit/demo_output/**` — regenerated artifacts for
  all five scenarios (byte-stable).
- Tests: `tests/integration/test_bo_m3_standalone.py`,
  `tests/integration/test_bo_m3_structural_credit.py`,
  `tests/unit/test_bo_m3_senior_management.py`.

Created:
- `tests/unit/test_bo_m3_validation_numeric.py` — Group B numeric verification.
- `tests/integration/test_bo_m3_live_readiness.py` — Group C real-model readiness
  (fake backend only, no paid call).

No schema bump was required: the four verification states map onto existing
dispositions + validation issues (`app/schemas/bo_m3.py` unchanged). This keeps
the change additive and backward compatible.

## 3. Improved consolidated-conclusion logic

`_section_c` draws ONLY on findings admitted by the same `_is_admitted` gate the
analytical record uses (imported from `m3_artifacts.py`, single source of truth):
a `rejected` disposition, an `insufficient_evidence` availability, or a
material-level (`high`/`medium`) `unverified` finding never contributes. For each
admitted material finding it emits a structured statement carrying: `source_id`
(finding/observation id), `core_observation`, `credit_direction`,
`credit_risk_mechanism`, `materiality`, `supporting_evidence_ids`,
`qualifications`, `unresolved_information_gaps`, and a
`why_it_matters_to_a_lender` sentence keyed on analytical dimension + direction.
Both pillars are assembled; a simulated hierarchy marks every Pillar-A statement
`hypothetical_simulated_basis: true` and prepends a hypothetical qualification.
Positive and negative findings are preserved side by side and explicitly NOT
netted. Output is deterministic (sorted by id) and byte-stable. The conclusion
sets `is_lending_recommendation: false`, `requires_human_review: true`,
`forced_outcome: false`.

## 4. Before / after (fictional positive / negative / mixed)

BEFORE (generic across scenarios) — `integrated_commentary` was two bullets:
"A validated corporate-structure diagram is available (N entities, …)" and
"Management commentary was tested …; observed credit directions: neutral." The
five scenarios were indistinguishable.

AFTER (differentiated, from the regenerated demos):
- **positive**: "Pillar A … potentially supportive factor(s) for lender recourse
  …" + "[positive] The freight operating subsidiary carries a guarantor marking.
  A guarantee from this entity may improve the lender's recourse … subject to the
  guarantee's enforceability, ranking, caps …" (trace: `SCF-POS-GUARANTOR`).
- **negative**: "Pillar A … factor(s) that may weaken recourse or constrain
  repayment resources …" + "[negative] … A restriction on upstream distributions
  may reduce the borrower's financial flexibility and constrain the cash
  resources available to service the proposed facility …" (trace:
  `SCF-NEG-CASHFLOW`).
- **mixed**: BOTH statements retained, with "Supportive and adverse factors are
  preserved separately and are not netted into a single score." (trace:
  `SCF-MIX-POS`, `SCF-MIX-NEG`).
- **HCA (insufficient)**: "a validated ownership hierarchy is available … but no
  debt, guarantee or cash-distribution finding is sufficiently supported … What
  CAN be established: the ownership hierarchy …; What CANNOT: whether any entity
  guarantees the facility … (insufficient evidence is not a neutral finding)."
- **unsupported-guarantee**: `SCF-BAD-GUARANTEE` excluded from the authoritative
  conclusion, retained under `evidence_verification.excluded_from_conclusion_ids`.

## 5. Validator 7 numerical-verification improvements

- **Absence ≠ contradiction.** A claimed value/period that is simply ABSENT from
  the cited evidence (no conflicting token of the same class) now fails with
  `contradiction=False` → `unverified` (UNSUPPORTED). A conflicting value/period
  of the same class → `contradiction=True` → `rejected` (CONTRADICTED).
- **Four states, existing enum.** VERIFIED → supported/qualified; UNSUPPORTED →
  unverified; CONTRADICTED → rejected; REQUIRES_HUMAN_REVIEW → qualified/unverified
  + a recorded `m3.requires_human_review` reason. No new enum.
- **Canonical financial checks** (gated on availability): metric identity (a
  conservative alias map; unmapped/ambiguous → skip), fiscal period, numerical
  value within rounding tolerance, and pp-vs-% confusion. Reuses the resolved
  `ParameterResult` values + `ResolvedMetricDefinition` units/period via
  `_canonical_metrics_from_prep`; never recomputes a metric. With no canonical
  datum it falls back to the corrected textual gate (no fabrication).
- **Unit/basis guards.** pp vs %; dollars vs millions/billions (token conflict);
  quarterly vs annual (period class); consolidated vs subsidiary (canonical scope
  value); revenue-growth vs EBITDA-growth (alias map resolves "revenue grew" to
  `revenue_growth` and declines EBITDA phrasing).
- **Causal/legal cap.** A still-supported claim carrying causal or legal markers
  is capped at human review (qualified) even when numbers agree; the
  credibility-vs-direction separation is untouched (a credible explanation of a
  negative development keeps its negative `credit_direction`).

## 6. Evidence-grounding + qualification behaviour

Findings retain claim ids, source references, periods, supporting/contradictory
evidence, verification status, credit direction, materiality, causal mechanisms,
qualifications and info gaps. Only admitted findings inform the authoritative
conclusion; rejected/unverified material items are retained under
`evidence_verification` and listed in `excluded_from_conclusion_ids`. A qualified
hypothetical is never turned into an established fact; simulated-structure
statements stay hypothetical and do not assert real HCA 10-K
debts/guarantees/restrictions.

## 7. Standalone real-model execution readiness (implemented vs gated)

Implemented: `run_m3(live_backend=…, live_config=…)` loads the admitted evidence
packet and the VALIDATED structural facts (injected BEFORE the pass), includes
the canonical financial map, builds the existing M3 specialist prompt, invokes
ONLY `m3_corporate_management`, parses + schema-validates the response, runs
Validator 7, and builds the governed JSON + manifest. CLI `--live` constructs the
provider via the existing abstraction.

Gated/safeguarded: opt-in, disabled by default, separate from the scripted path;
bounded to one specialist execution (retries only if explicitly bounded);
timeout + token limits from `Settings`; schema-validated before use; fails safely
(`partially_failed`, no fabricated findings) with NO silent fallback; no
credentials logged/persisted (`SecretStr`); manifest records execution_mode and
real_model_invoked; no Modules 1/2/4, synthesizer, challenger, scoring or renderer
invoked (`cross_module_or_downstream_invoked: false`).

## 8. Were any actual model calls made?

**No.** No provider/credentials are configured; `run-m3 --live` raises a clear,
credential-free blocker (`Set LLM_PROVIDER=…`) rather than calling a model. All
live-path tests use the repo's deterministic `FakeLLMBackend`; a fake backend is
honestly NOT counted as a real model (`real_model_invoked` stays false). No live
run was invented or simulated as successful.

## 9. Updated demo JSON paths

Regenerated (byte-stable) under
`examples/bo_m3/structural_credit/demo_output/<case>/` for
`hca_simulated`, `fictional_positive`, `fictional_negative`, `fictional_mixed`,
`fictional_unsupported_guarantee`; each case writes `m3_corporate_structure.json`,
`m3_analysis.json`, `m3_structure_provenance.json`, `validation_report.json`,
`evaluation_scorecard.json`, `run_manifest.json`.

Regenerate: `DEBUG=false PYTHONPATH=. .venv/bin/python
examples/bo_m3/structural_credit/generate_demo.py` (twice → empty `git diff`).

## 10. Evaluation + reproducibility results

- Offline byte-stability confirmed: a second generator run leaves `git diff`
  empty; identical governed findings re-assemble byte-identically
  (`test_groupA_identical_governed_findings_reassemble_byte_identically`).
- Scorecard extended with explicit denominators: structural fact accuracy,
  source-reference validity, structural credit direction, unsupported-guarantee
  handling, simulation treatment; new `conclusion_integration_accuracy`
  (admitted↔traced, no excluded finding promoted) and
  `numerical_verification_quality` (unsupported vs contradicted kept distinct,
  requires-human-review count); management: evidence-citation validity,
  numerical-verification accuracy, causal qualifications, uncertainty.
- **Real-LLM analytical consistency = NOT YET EVALUATED** (no live run). Offline
  byte-stability is NOT substituted for real-model consistency; the scorecard
  states this explicitly.

## 11. Senior-management regression results

All senior-management cases pass (supported/contradicted/neutral/mixed/
insufficient). The credibility-vs-direction separation, management-proxy and ICFR
gates are unchanged. Two existing assertions that encoded the OLD (incorrect)
"absence = contradiction" behaviour were corrected to the mandated semantics
(now `unverified` for pure absence; `rejected` only for a genuine conflicting
value), and new positive coverage distinguishes the two
(`test_absent_numeric_value_is_unsupported_not_contradicted`,
`test_conflicting_numeric_value_is_contradicted_and_rejected`). The governance
guarantee is preserved: material unverified findings are still excluded from the
authoritative conclusion and retained in the audit trail. The 8-case HCA
management benchmark is retained; its developer-authored labels remain marked
awaiting external review and are not presented as independent ground truth.

## 12. Full offline test results

Commands (run from the worktree):
- Targeted: `DEBUG=false .venv/bin/python -m pytest -q tests/unit/test_bo_m3_*.py
  tests/integration/test_bo_m3_*.py tests/unit/test_llm_client.py` → **177 passed**.
- Full: `DEBUG=false .venv/bin/python -m pytest -q` →
  **1459 passed, 131 skipped, 12 errors** (98s).

The 12 errors are EXACTLY the known pre-existing environment issue in
`tests/integration/test_bo_recorded_completion.py` (missing untracked
`output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/
specialists_run.json`, absent on the base branch too). They are NOT regressions
and are NOT counted as passing. Versus the base (1426 passed / 131 skipped / 12
errors): +33 passed (new Group A/B/C/D tests), identical skips, identical 12 env
errors, zero new failures, no weakened valid expectation.

## 13. Known limitations + outstanding analytical risks

- The deterministic checks are surface/token + canonical-value based. They cannot
  prove semantic entailment or causation; qualitative unsupported inferences rely
  on evidence-sufficiency and management-proxy downgrades, and causal/legal claims
  are routed to human review, never auto-established.
- The canonical-metric alias map is deliberately conservative: an unrecognised or
  ambiguous metric phrase yields no canonical check (human review), never a wrong
  match. Accounting-basis and currency fields are carried but not all are
  populated by the current indicator layer; mismatches on those remain a
  human-review responsibility.
- The real-model path is wired and unit-exercised with a fake backend only. Live
  analytical capability, latency, token behaviour and real provider schema
  conformance are UNVERIFIED.
- Software correctness is not credit-analytical correctness; the HCA structure is
  simulated; the management benchmark labels await external review.

## 14. Git branch + worktree status

Work committed to `feat/m3-analytical-quality` only (no push, no PR, no other
branch touched). Worktree left in place at `.worktrees/m3-analytical-quality`.
Exact test commands + results are recorded in the commit message and above so the
reviewer need not re-run blind.
