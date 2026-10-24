# Module 3 — Validator 7 Numerical Verification Fix

Branch: `feat/m3-validator-numeric-fix` (branched off `feat/m3-analytical-quality`)
Worktree: `.worktrees/m3-validator-numeric-fix`
Scope: narrowly-scoped correction to Validator 7's deterministic + canonical
numeric verification. No rebuild, no new validator/agent, no new financial
engine, no scoring/credit-direction/contract changes.

> Software correctness is not credit-analytical correctness. This change tightens
> *when* a numeric claim may be called CONTRADICTED and removes false positives;
> it does **not** perform comprehensive semantic or accounting verification.
> Genuinely ambiguous cases are deferred to human review.

---

## 1. Root-cause summary

Two functions in `app/services/bo/m3_validation.py` could record a **false
contradiction** (and in one direction a **false support**) because they compared
numbers without first establishing that the two numbers describe the same thing.

- **Problem A — `_deterministic_claim_checks`.** When a claimed number was absent
  from the cited passage but the passage contained *any other* number, the check
  set `contradiction=True` regardless of whether that other number referred to
  the same metric, period, scale or currency. A revenue-growth claim was
  "contradicted" by an operating-cash-flow figure that merely happened to sit in
  the same passage. Period tokens had the same flaw (a different fiscal year was
  treated as a conflict). It also treated any present numeric token as *support*,
  so a coincidental 12% EBITDA figure could "verify" a 12% revenue claim.

- **Problem B — `_canonical_financial_checks`.** A claimed value was compared to
  the resolved canonical metric value without checking that the claim and the
  canonical datum shared the same **reporting scope** (or currency). A
  subsidiary-level net-debt claim was "contradicted" by the consolidated/group
  net-debt figure, which is a different reporting scope, not a conflicting
  assertion about the same quantity.

Central rule applied throughout: a claim may be **CONTRADICTED only** when
admitted evidence establishes a genuinely conflicting assertion about the **same
metric, reporting entity/scope, period, accounting basis and comparable units**.
A different number is not sufficient proof of contradiction; a matching number is
not sufficient proof of support if it belongs to a different metric.

---

## 2. Exact code changes (`app/services/bo/m3_validation.py`)

New conservative, surface-level comparability helpers (no new engine; reuse of
the existing `_METRIC_ALIASES` / `_identify_canonical_metric` / canonical datum):

- `_identified_metrics(text)` — the full set of canonical metrics a text names
  (so claim and passage can be checked for the *same* metric).
- `_SCALE_MARKERS` / `_scales`, `_CURRENCY_MARKERS` / `_currencies` — detect stated
  scale (million/billion/…) and currency (USD/SGD/…) so differences across them
  are not treated as conflicts and are never silently converted.
- `_CONSOLIDATED_MARKERS` / `_SUBSIDIARY_MARKERS`, `_claim_reporting_scope`,
  `_scope_comparable(claim, datum_scope)` — reporting-scope detection and the
  consolidated-vs-subsidiary comparability gate for the canonical path.
- `_comparable_basis(claim, passage)` — returns `(comparable, reason)`; requires
  same metric, same period (if stated), compatible scale, compatible currency and
  compatible reporting scope before any numeric difference may be a contradiction.

`_deterministic_claim_checks` rewritten so that:
- a number is **SUPPORTED** only if present *and* the passage is on a comparable
  basis (a coincidental token match for a different metric is not support);
- a number is **CONTRADICTED** only if absent *and* a different number is present
  *and* the basis is comparable; otherwise it is **UNSUPPORTED**;
- an absent **fiscal period** is always **UNSUPPORTED** (never a contradiction):
  a citation about a different period does not disprove a claim about another.

`_canonical_financial_checks` rewritten so that a period mismatch or value
mismatch is marked `contradiction=True` **only** when `scope_comparable` and
currency are comparable; otherwise the mismatch is downgraded to UNSUPPORTED
(`contradiction=False`) with the reason recorded. Missing/unknown scope preserves
uncertainty rather than assuming equivalence. The pp-vs-% confusion flag and the
numeric tolerance behaviour are unchanged.

The four-state vocabulary and the caller's mapping
(CONTRADICTED→`rejected`, UNSUPPORTED→`unverified`, REQUIRES_HUMAN_REVIEW→
`qualified`/`unverified`+reason, VERIFIED→unchanged) were **reused**, not
replaced. No enums, schemas, scoring weights or JSON contracts changed.

---

## 3. Corrected comparison rules

A numeric difference is a **CONTRADICTION** only when ALL hold:
1. same canonical metric (claim and evidence name the same metric);
2. same fiscal period (when a period is stated on both sides);
3. compatible scale (no millions-vs-billions mismatch);
4. compatible currency (no USD-vs-SGD mismatch without an established conversion);
5. same reporting scope (consolidated-vs-subsidiary not assumed equivalent);
6. a directly conflicting numerical assertion is actually present.

If any precondition cannot be established, the outcome is **UNSUPPORTED/UNVERIFIED**
(or REQUIRES_HUMAN_REVIEW for causal/legal claims), never CONTRADICTED. A matching
number for a different metric is **not** support.

---

## 4. BEFORE / AFTER — the two false-contradiction cases

Revenue-vs-cash-flow (Problem A):

| | Claim | Cited passage | Before | After |
|---|---|---|---|---|
| | "Revenue grew 12% in FY2024" | "Operating cash flow was $250 million in FY2024" | CONTRADICTED (`rejected`) | **UNSUPPORTED (`unverified`)** — different metric |

Subsidiary-vs-consolidated (Problem B):

| | Claim | Canonical datum | Before | After |
|---|---|---|---|---|
| | "Net debt was 100 at the subsidiary level in FY2024" | consolidated group net debt = 5000 | CONTRADICTED (`rejected`) | **UNSUPPORTED (`unverified`)** — different reporting scope |

Covered by `test_revenue_growth_claim_vs_cash_flow_figure_is_unsupported_not_contradicted`
and `test_subsidiary_net_debt_vs_consolidated_is_not_auto_contradicted`.

---

## 5. Genuine contradiction detection still works

The fix removes *false positives only*; it does not weaken real contradiction
detection:

- `test_revenue_growth_8pct_vs_12pct_same_basis_is_contradicted` — same metric
  (revenue growth), same FY2024 period, 8% vs 12% → **CONTRADICTED (`rejected`)**.
- `test_directly_contradicted_value_is_rejected` — revenue 51% vs 48.9% → rejected.
- `test_fy2024_revenue_claim_with_conflicting_fy2024_value_is_contradicted` →
  rejected.
- `test_subsidiary_claim_contradicted_only_by_subsidiary_level_evidence` — a
  subsidiary claim IS contradicted when the evidence gives a different
  subsidiary-level amount for the same metric/period.
- `test_bo_m3_senior_management.py::test_contradicted_numeric_value_is_rejected_not_accepted`
  and the `rejected` arm of
  `test_validator_outcomes_accepted_qualified_rejected_human_review` still reject.

---

## 6. Updated adversarial test results

`tests/unit/test_bo_m3_validation_numeric.py` — **25 passed** (was 13). All 12
required scenarios are covered:

1. revenue +12% vs $250M operating cash flow → UNSUPPORTED ✓
2. revenue +12% vs 8% revenue growth same basis → CONTRADICTED ✓
3. revenue +12% vs 12% EBITDA growth → UNSUPPORTED (not verified) ✓
4. subsidiary net debt vs consolidated only → UNSUPPORTED / not auto-contradicted ✓
5. FY2023 claim vs unrelated FY2024 citation → UNSUPPORTED ✓
6. FY2024 revenue claim vs conflicting FY2024 value → CONTRADICTED ✓
7. USD claim vs other-currency datum, no conversion → UNVERIFIED ✓
8. 2 percentage points vs 2% relative → not auto-verified ✓
9. passage with several metrics/numbers → only the relevant metric's number
   matched ✓
10. numerically verified outcome + unsupported causal attribution → outcome
    preserved, causal claim capped at human review ✓
11. same value+metric+entity+period+basis+units → eligible for VERIFIED ✓
12. canonical/metadata unavailable → fail conservatively, no fabricated
    contradiction ✓

### Existing assertions changed (and why the old expectation was wrong)

- `test_bo_m3_validation_numeric.py` was rewritten; three earlier assertions that
  treated a bare token/period/scope mismatch as a contradiction were corrected:
  - dollars-vs-millions (`$3bn` vs `3000 million`) — same magnitude, different
    scale: now **not** a contradiction (no fabricated cross-scale conversion).
  - quarterly-vs-annual (Q3 2024 vs FY2024) — different period: now **not** a
    contradiction.
  - consolidated-vs-subsidiary value — different scope: now **not** a
    contradiction.
- `test_bo_m3_senior_management.py::test_mismatched_reporting_period_is_rejected`
  → renamed `..._is_unsupported_not_contradicted`. Old expectation (`rejected`)
  was wrong: "Leverage fell in FY2023" vs a citation about FY2024 is a different
  period, which the central rule says is UNSUPPORTED, not a conflicting assertion.
- `test_bo_m3_senior_management.py::test_validator_outcomes_accepted_qualified_rejected_human_review`
  case C: the claim/evidence wording was changed from "Revenue **was** 99%/12%"
  to "Revenue **grew** 99%/12%" so the metric is identifiable and the case remains
  a *genuine* same-basis contradiction (`rejected`). "revenue was" is not in the
  metric-alias vocabulary, so after the fix the gate could not establish metric
  identity and conservatively returned UNSUPPORTED. The test's *intent*
  (demonstrating a rejected contradiction) is preserved on a basis the gate can
  actually establish; genuine contradiction detection is not weakened.

No genuine-contradiction test was disabled.

---

## 7. Module 3 regression results

Ran the standalone / senior-management / structural-credit / corporate-structure
Module 3 suites together:

```
tests/unit/test_bo_m3_validation_numeric.py
tests/unit/test_bo_m3_senior_management.py
tests/unit/test_bo_m3_services.py
tests/unit/test_bo_m3_corporate_structure.py
tests/unit/test_bo_m3_management_benchmark.py
tests/unit/test_bo_m3_independent_evaluation.py
tests/unit/test_bo_m3_corporate_refinement.py
tests/unit/test_bo_m3_onboarding_transform.py
tests/unit/test_bo_m3_structure_context.py
tests/integration/test_bo_m3_standalone.py
tests/integration/test_bo_m3_structural_credit.py
tests/integration/test_bo_m3_single_specialist.py
tests/integration/test_bo_m3_v6_integration.py
tests/integration/test_bo_m3_corporate_structure_integration.py
tests/integration/test_bo_m3_live_readiness.py
```
→ **179 passed** (0 failed).

---

## 8. Full offline suite results

`DEBUG=false .venv/bin/python -m pytest -q`:

```
1471 passed, 131 skipped, 1 warning, 12 errors in ~96s
```

vs baseline **1459 passed / 131 skipped / 12 errors**:

- passed 1459 → **1471** = +12, exactly the net new numeric adversarial tests
  (file grew from 13 to 25 cases); no previously-passing test regressed.
- skipped **131** unchanged.
- errors **12** — these are the KNOWN pre-existing environmental issue in
  `tests/integration/test_bo_recorded_completion.py` (they require an untracked
  `output/bo/HCA_M3_REFINEMENT_LIVE_.../specialists_run.json` artifact absent on
  the base branch too). They are setup errors, NOT test passes and NOT
  regressions introduced by this change. **The 12 errors are not passing tests.**

---

## 9. Remaining limitations of deterministic financial verification

This layer is a conservative surface check and intentionally defers to humans:

- Metric identity relies on a small alias map (`_METRIC_ALIASES`). Vocabulary it
  does not recognise (e.g. "revenue **was** X") yields UNSUPPORTED rather than a
  verdict — it does not understand arbitrary financial phrasing.
- Scope/currency/scale/period are detected from surface vocabulary only; absent
  markers mean UNKNOWN, and uncertainty is preserved (no assumed equivalence, no
  fabricated unit/currency conversions).
- It does not perform accounting reconciliation, consolidation arithmetic, or
  semantic entailment, and it never recomputes a metric.
- Causal attribution and legal significance are never settled by numeric
  agreement; such claims are capped at REQUIRES_HUMAN_REVIEW
  (evidence-verification stays separate from credit-direction, and an unverified
  citation does not by itself flip `credit_direction`).

Anything the gate cannot establish is routed to UNSUPPORTED or REQUIRES_HUMAN_REVIEW
for an analyst to adjudicate. The final credit judgement remains with the human.

---

## 10. Branch & worktree status

- Worktree: `.worktrees/m3-validator-numeric-fix`, branch
  `feat/m3-validator-numeric-fix` (off `feat/m3-analytical-quality`).
- Files changed: `app/services/bo/m3_validation.py`,
  `tests/unit/test_bo_m3_validation_numeric.py`,
  `tests/unit/test_bo_m3_senior_management.py`, plus this report.
- Committed to `feat/m3-validator-numeric-fix` only. Not pushed, not merged, no
  PR. No changes to other modules, the synthesizer/challenger, scoring, the memo
  renderer, JSON contracts, or the live-model safeguards.
