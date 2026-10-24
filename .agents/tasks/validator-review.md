# Comparability gate for Validator 7 numeric contradiction

Validator 7's deterministic and canonical numeric checks could call a financial claim CONTRADICTED whenever a *different* number sat in the cited evidence, regardless of whether that number described the same metric, period, scope, scale or currency. This change inserts a comparability gate: a numeric difference is a contradiction only when claim and evidence are demonstrably about the same metric, same stated period, compatible scale/currency and same reporting scope, with a directly conflicting value present. Absent or non-comparable numbers now resolve to UNSUPPORTED, and a coincidental token match for an unrelated metric no longer counts as support. The work is confined to `_deterministic_claim_checks`, `_canonical_financial_checks`, new surface-level helper functions, and the two affected test files.

Watch for: nothing blocking. The gate is surface-vocabulary only (metric identity rests on a small alias map), which the author documents as an intentional conservative limitation that routes anything unrecognised to UNSUPPORTED / human review rather than to a verdict — this is the correct fail direction for credit analysis (confirmed).

**Verdict**: APPROVED

## High-level view

The fix is narrowly scoped. Four files change: the validator source, its two affected test files, and the task report. No new agent, no scoring or `credit_direction` change, no JSON-contract change, no new financial engine — the existing four-state vocabulary and caller mapping are reused unchanged (confirmed via `git diff --name-only` and grep of the source diff showing no `credit_direction`/score/disposition-mapping edits).

The deterministic path gains a `_comparable_basis(claim, passage)` gate. A claimed number is now SUPPORTED only when the token is present *and* the basis is comparable; CONTRADICTED only when a different number is present *and* the basis is comparable; everything else is UNSUPPORTED. An absent claimed fiscal period is unconditionally UNSUPPORTED (`contradiction=False`), correcting the prior rule that treated a different year as a conflict (confirmed in the diff).

The canonical path gates its period-mismatch and value-mismatch contradictions on `basis_comparable = scope_comparable and ccy_comparable`, using the datum's own `reporting_scope` and `currency` metadata. A subsidiary claim against a consolidated datum, or an unstated-scope claim, is downgraded to UNSUPPORTED with the reason recorded rather than rejected (confirmed).

Genuine contradiction detection is preserved. The 8%-vs-12% same-metric/same-period case, the subsidiary-vs-subsidiary case, and the conflicting-FY2024-value case all still assert `rejected`. The fix removes false positives only; it does not weaken real validation (confirmed by reading the retained and new tests).

Causal attribution stays separate from numeric verification: a numerically verified outcome does not verify a management causal claim, which remains capped at human review. Evidence non-verification does not flip `credit_direction` — that logic is untouched by the diff (confirmed).

<details>
<summary>Issues (0 blocking)</summary>

No blocking findings. One informational note, already documented by the author and not requiring action:

1. **Alias-map vocabulary ceiling (informational)** — metric identity depends on `_METRIC_ALIASES` substring matching, so phrasings outside the vocabulary (e.g. "revenue was X") resolve to UNSUPPORTED rather than a verdict. This is the safe fail direction and is explicitly called out in the report's limitations section; no change required.

</details>

<details>
<summary>Details</summary>

### Comparability gate in the deterministic path

`_comparable_basis` is the core of the fix. It returns `(comparable, reason)` and requires, in order: both sides name a recognised canonical metric and share at least one; if both state a period, they share one; stated scales match; stated currencies match; stated reporting scopes match. Any failure returns `False` with a human-readable reason that is threaded into the check `detail`, which matters for auditability — the memo records *why* a mismatch was not treated as a conflict.

The rewritten loop in `_deterministic_claim_checks` splits the old boolean into `token_present` and `present = token_present and comparable`. This closes both failure directions at once: an absent-but-different number is a conflict only when `comparable` (otherwise UNSUPPORTED), and a *present* number that happens to match an unrelated metric's figure yields a distinct "coincidental token match does not establish support" detail rather than silent verification. That second direction (matching number, wrong metric) is the subtle one and the diff handles it explicitly — this is the Scenario 3 (revenue 12% vs EBITDA 12%) case, which the test asserts resolves to `unverified`.

The fiscal-period branch is now unconditional `contradiction=False`. A citation about FY2024 can no longer disprove a claim about FY2023; it simply fails to substantiate it. This is the correct reading of the central rule and is covered by both a direct helper test and the senior-management integration test.

### Scope and currency gating in the canonical path

`_canonical_financial_checks` computes `basis_comparable` from `_scope_comparable(claim, datum.reporting_scope)` and a currency check against `datum.currency`, reusing the existing canonical datum rather than building anything new. `_scope_comparable` is conservative in the right direction: a claim that states no scope returns `None` → not comparable (uncertainty preserved, not assumed equivalent); a datum with no recorded scope is likewise not comparable. Only consolidated-vs-consolidated or subsidiary-vs-subsidiary passes. The period-mismatch and value-mismatch checks then carry `contradiction=basis_comparable`, and when not comparable the detail string appends "not treated as a conflict because …". The pp-vs-% confusion flag and the numeric tolerance (`max(abs(value)*0.02, 0.5)`) are untouched.

This is exactly the Problem B correction: "Subsidiary A net debt 100" against a consolidated datum of 5000 is no longer `rejected`. The test `test_subsidiary_net_debt_vs_consolidated_is_not_auto_contradicted` asserts `unverified`/`qualified`, and `test_unstated_scope_claim_vs_consolidated_datum_preserves_uncertainty` confirms the unknown-scope path also preserves uncertainty.

### Genuine contradictions still fire

The risk in a false-positive fix is over-correction that silences real conflicts. The tests guard against this explicitly: `test_revenue_growth_8pct_vs_12pct_same_basis_is_contradicted` (same metric, same FY2024 → rejected), `test_subsidiary_claim_contradicted_only_by_subsidiary_level_evidence` (subsidiary claim vs a different subsidiary-level amount → contradiction recorded), and `test_fy2024_revenue_claim_with_conflicting_fy2024_value_is_contradicted`. The senior-management suite's `rejected` arm and `test_contradicted_numeric_value_is_rejected_not_accepted` remain. The direction of the fix is strictly from false CONTRADICTED → UNSUPPORTED, never the reverse.

### Changed existing expectations are corrected false positives

Three deterministic tests were rewritten and two senior-management assertions changed. Each is a justified correction, not a silenced contradiction:

- dollars-vs-millions (`$3bn` vs `3000 million`) — same magnitude, different scale, and the gate refuses to fabricate a cross-scale conversion → now not a contradiction.
- quarterly-vs-annual (Q3 2024 vs FY2024) — different period → now not a contradiction.
- consolidated-vs-subsidiary value → different scope → now not a contradiction.
- `test_mismatched_reporting_period_is_rejected` → renamed `..._is_unsupported_not_contradicted`; "Leverage fell in FY2023" vs an FY2024 citation is a different period, so UNSUPPORTED is the correct outcome.
- `test_validator_outcomes_...` case C reworded from "Revenue **was** 99%" to "Revenue **grew** 99%" so the metric is identifiable and the case stays a *genuine* same-basis contradiction. "revenue was" is outside the alias vocabulary, so post-fix the gate could not establish metric identity and would conservatively return UNSUPPORTED; the rewording keeps the test's intent (a rejected contradiction) on a basis the gate can actually establish. The genuine-contradiction coverage is not weakened — it is relocated onto establishable ground.

### Scope confinement and non-regression

`git diff --name-only` shows exactly four files. Grep of the source diff finds no edits to `credit_direction`, scoring, weights or the `contradiction`→disposition mapping — the only `disposition` hit is a docstring word. All helper dependencies (`_PERIOD_RE`, `_METRIC_ALIASES`, `_NUMBER_RE`, `_percentage_point_confusion`, `_identify_canonical_metric`) pre-exist on `feat/m3-analytical-quality`. The JSON output contracts, senior-management, and structural-credit functionality are not touched by the diff.

### Recorded verification evidence

The report records the targeted file at 25 passed (was 13), the Module 3 regression bundle at 179 passed / 0 failed, and the full offline suite at 1471 passed / 131 skipped / 12 errors against a 1459 / 131 / 12 baseline. The +12 passed equals the net new adversarial cases; skips unchanged; the 12 errors are explicitly characterised as pre-existing environmental setup errors in `test_bo_recorded_completion.py` (missing untracked artifact, present on base too) and the report states plainly they are "not passing tests" and not regressions. The evidence is present, internally consistent, and does not misrepresent the 12 errors as passes — so per the instructions the full suite was not re-run. No specific doubt about the adversarial tests remained, so the optional narrow spot-check was not needed.

### Human-in-the-loop and the correctness caveat

The report states explicitly that software correctness is not credit-analytical correctness, that the gate performs no accounting reconciliation or semantic entailment, and that genuinely ambiguous cases (unknown scope/currency/period, causal/legal claims) are routed to UNSUPPORTED or REQUIRES_HUMAN_REVIEW for an analyst to adjudicate. This matches the North Star principles on uncertainty, evidence-first classification, and keeping humans in the loop.

</details>

<details>
<summary>File map</summary>

- `app/services/bo/m3_validation.py` — adds `_identified_metrics`, scale/currency/scope detectors, `_scope_comparable`, `_comparable_basis`; rewrites `_deterministic_claim_checks` and gates `_canonical_financial_checks` contradictions on comparable basis.
- `tests/unit/test_bo_m3_validation_numeric.py` — grows from 13 to 25 cases covering all 12 required adversarial scenarios; three over-strict contradiction expectations corrected.
- `tests/unit/test_bo_m3_senior_management.py` — period-mismatch test renamed to expect UNSUPPORTED; one case reworded to keep a genuine same-basis contradiction establishable.
- `.agents/tasks/m3-validator-numeric-fix-report.md` — coder's report.

Full diff: `git -C <worktree> diff feat/m3-analytical-quality...feat/m3-validator-numeric-fix`

</details>
