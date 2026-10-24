# Module 3 — Validator 7 V7: Final Numerical Evidence Binding & Validator 7 Correction

Branch: `feat/m3-numeric-binding-v7`
Worktree: `/Users/Daniel/Desktop/SMU/AI in Fin/Credit Memo Testing/kirotesting-integrated/.worktrees/m3-numeric-binding-v7`
Scope: a narrowly-scoped correction to the EXISTING Module 3 Validator 7 numerical
verification. No new agents/validators, no new modules, no competing financial
engine, no scoring/weight changes, no live LLM calls.

---

## 1. Root causes of the remaining A/B/C/D issues

All four stemmed from the same structural weakness: after V6, the deterministic
and canonical checks still compared numbers against a **flat set of numeric
tokens** rather than against the value **bound to the claim's metric, entity and
period**.

- **Problem A (numbers matched to unrelated metrics).** `_deterministic_claim_checks`
  built a single `corpus_numbers` set from the whole passage. The claim's number
  (`12`) could match any token in that set — including the `12` from "net debt was
  12 million" — so a revenue-growth claim was VERIFIED by a net-debt figure. The
  comparability gate confirmed the metric *appeared* in the passage but never
  isolated which number *belonged to* that metric.

- **Problem B (years mistaken for values).** `_NUMBER_RE` extracted the bare year
  `2024` out of `FY2024` into the corpus number set. Only the claim side stripped
  period tokens; the corpus side did not. For "Revenue increased during FY2024" the
  corpus number set was `{2024}`, so the claimed `12` was judged a conflict with
  `2024` on a comparable basis → false CONTRADICTED.

- **Problem C (cross-period canonical comparisons).** `_canonical_financial_checks`
  set `contradiction = basis_comparable` where `basis_comparable` covered scope and
  currency but **not reporting period**. A FY2023 claim versus a FY2024 canonical
  datum was therefore recorded as a period mismatch AND a value mismatch, both with
  `contradiction=True` → false CONTRADICTED. The latest canonical datum effectively
  superseded a historical-period claim.

- **Problem D (subsidiary entity matching).** `_scope_comparable` treated any two
  subsidiary-scope figures as comparable. "Subsidiary A net debt 100" versus a
  canonical "subsidiary" datum of 500 (a different legal entity) was judged
  comparable purely because both were classified `subsidiary` → false CONTRADICTED.
  Legal-entity identity was never required.

---

## 2. Files changed

- `app/services/bo/m3_validation.py` — the Validator 7 numerical logic (the only
  production file changed).
- `tests/unit/test_bo_m3_validation_numeric.py` — 19 new V7 adversarial tests
  (successful and unsuccessful matching cases) plus the existing 42 retained
  unchanged.
- `.agents/tasks/m3-numeric-binding-v7-report.md` — this report.

No other module, schema, contract, agent, scoring config or UI was touched. All
existing Module 3 behaviour (specialist architecture, corporate-structure
onboarding/transformation/credit reasoning, senior-management assessment,
consolidated conclusion assembly, standalone execution, real-model opt-in
safeguards, both JSON output contracts, source provenance + case isolation, and
the credibility-vs-credit-direction distinction) is preserved.

---

## 3. The corrected numerical-binding logic

New helpers in `m3_validation.py`:

- `_period_value_tokens(text)` — returns the numeric forms that are actually
  **reporting-period tokens** (the `2024` inside `FY2024`, `2022` inside `Q3 2022`).
  These are excluded from value comparison on BOTH the claim and corpus side.
  Crucially it does **not** blanket-drop four-digit numbers: a `3000` that is not
  part of a period phrase is still a comparable value (Problem B).

- `_metric_bound_values(text, metric_id)` — isolates the numeric value(s) that
  belong to a specific metric by scanning each metric-alias occurrence and
  collecting the numbers in its local clause, stopping at the next *different*
  metric mention or a clause separator. In "Revenue grew 8% … while net debt was
  12 million" it returns `{8}` for `revenue_growth` and `{12}` for `net_debt`
  (Problem A).

- `_claim_mentions_entity(claim_text, entity_id, entity_name)` — whole-token match
  of a canonical legal-entity identifier / verified entity name in the claim. Loose
  string similarity is **not** accepted as identity proof (Problem D).

Changes to the two target functions:

- `_deterministic_claim_checks`: period-role tokens removed from both value sets;
  when the claim names exactly one recognised metric and the passage also names it,
  comparison uses the **metric-bound** value subset (`relevant_numbers`) instead of
  the whole corpus. A number present in the passage but bound to a *different*
  metric is explicitly reported as "value of a different metric" and does not
  establish support. When a metric cannot be bound, it falls back to the whole
  corpus but keeps the same `comparable` gate, so an unmatched-metric passage can
  never establish support or contradiction.

- `_canonical_financial_checks`: added a **`period_comparable`** gate (claimed
  period must equal the datum period before a value difference can be a conflict)
  and extended `_scope_comparable` with **entity identity** for subsidiary-level
  data. `basis_comparable = scope_comparable AND ccy_comparable AND
  period_comparable`. A cross-period datum is no longer value-compared as a
  conflict; a subsidiary datum without an established, claim-identified entity is
  not comparable. The canonical schema fields `entity_id` / `entity_name` are read
  if present; the production `_canonical_metrics_from_prep` map never sets them and
  is always `consolidated`, so real subsidiary claims are never auto-contradicted
  by it.

Outcomes continue to reuse the EXISTING four-state disposition mapping in
`_govern_finding` (`supported` / `qualified` / `unverified` / `rejected`); no new
enums were introduced.

---

## 4. BEFORE / AFTER for the critical scenarios

| Scenario | BEFORE (V6) | AFTER (V7) |
|---|---|---|
| **A1** claim "Revenue grew 12% FY2024"; passage "Revenue grew 8% FY2024, while net debt was 12 million" | VERIFIED (matched the net-debt `12`) | CONTRADICTED on revenue growth (8% ≠ 12%, bound to `revenue_growth`); net-debt `12` rejected as "value of a different metric" |
| **A2** claim "Revenue grew 12%"; passage "EBITDA grew 12%" | UNSUPPORTED (already correct in V6) | UNSUPPORTED (preserved) |
| **B** claim "Revenue grew 12% FY2024"; passage "Revenue increased during FY2024" | CONTRADICTED (claimed `12` vs year `2024`) | UNSUPPORTED (year `2024` no longer a financial value) |
| **C** claim "Consolidated net debt 100 FY2023"; canonical net debt 150 FY2024 | CONTRADICTED (period + value mismatch) | UNSUPPORTED (cross-period, `contradiction=False`) |
| **D** claim "Subsidiary A net debt 100"; canonical subsidiary net debt 500 (no entity id) | CONTRADICTED (same subsidiary scope) | UNSUPPORTED (entity identity not established) |

---

## 5. Genuine contradiction / support detection preserved

- claim "Revenue grew 12% FY2024" vs passage "Revenue grew 8% in FY2024"
  (same metric/entity/period) → still **CONTRADICTED** (the `8` is bound to
  `revenue_growth` and conflicts with the claimed `12`).
- claim "Revenue grew 12% FY2024" vs passage "Revenue grew 12% in FY2024" →
  still **SUPPORTED**.
- a legitimate four-digit amount (net debt 9000 vs cited 3000, same metric/period)
  → still **CONTRADICTED** (Problem B fix does not swallow real four-digit values).
- same named subsidiary + same period + conflicting value (entity id present on the
  datum) → still **CONTRADICTED** (Problem D fix does not disable subsidiary
  detection when identity is established).
- a numerically-verified outcome with an unsupported **causal** management
  attribution is still capped at human review (`qualified` + `m3.requires_human_review`),
  never auto-promoted — numeric verification and management-attribution remain
  separate, and `credit_direction` is unaffected by numeric-verification handling.

---

## 6. Focused test results

Command (cwd = worktree):

```
DEBUG=false .venv/bin/python -m pytest \
  tests/unit/test_bo_m3_validation_numeric.py \
  tests/unit/test_bo_m3_senior_management.py -q
```

Result: **61 passed** (42 pre-existing V5/V6 tests retained unchanged + 19 new V7
adversarial tests). No existing expectation required correction — every prior
assertion is still valid under the governing principle, so none was weakened.

Direct behavioural checks were also executed against the actual `_deterministic_claim_checks`
and `_canonical_financial_checks` functions for A1/A2/B/C/D plus the two preserve
cases; observed classifications matched the AFTER column above (false support
eliminated, false contradiction eliminated, genuine support/contradiction intact,
ambiguous cases fail conservatively).

The 14 mandated adversarial cases are all covered:
(1) net-debt-12 → contradicted on revenue; (2) EBITDA 12% → unsupported; (3) "revenue
increased FY2024" → unsupported; (4) FY2023 vs FY2024 canonical → unsupported; (5)
Subsidiary A vs canonical Subsidiary B → unsupported; (6) consolidated datum vs
subsidiary claim → not auto-comparable; (7) 8% vs 12% same basis → contradicted;
(8) 12% vs 12% same basis → supported; (9) multi-metric passage → relevant binding
only; (10) correct number + unsupported causal attribution → verify number, qualify
attribution; (11) currency mismatch without conversion → not contradicted; (12)
pp-vs-% confusion → not auto-verified; (13) metric alias outside vocabulary →
conservative; (14) missing canonical entity id → identity not assumed.

---

## 7. Full regression results vs baseline 1471 / 131 / 12

Command (cwd = worktree): `DEBUG=false .venv/bin/python -m pytest -q`

Result: **1490 passed, 131 skipped, 12 errors** in ~102s.

- Passed: 1490 = baseline 1471 + 19 new V7 tests. No prior test regressed.
- Skipped: 131 (unchanged).
- Errors: **12 — the KNOWN pre-existing environmental issue**, all in
  `tests/integration/test_bo_recorded_completion.py`. They fail at *setup* with
  `AssertionError: Genuine HCA R5 artifact is required…` because the untracked
  `output/bo/HCA_M3_REFINEMENT_LIVE_.../specialists_run.json` artifact is absent in
  this environment (absent on base too). These are **inherited environment errors,
  NOT new regressions**, and are **not** passing tests.

---

## 8. Outstanding deterministic-verification limitations (human review required)

This remains a conservative, surface-level textual + canonical gate. It does **not**
perform comprehensive semantic or accounting verification. Software correctness is
not credit-analytical correctness. Specifically:

- **Alias/vocabulary ceiling.** Metric identity relies on `_METRIC_ALIASES`. A metric
  phrased outside the vocabulary (e.g. "gross margin", bespoke KPIs) yields no
  canonical match and no binding → a conservative (unverified) outcome rather than a
  fabricated verification. Extending coverage is a vocabulary question, not a logic
  one.
- **Heuristic extraction limits.** Metric-value binding uses a bounded local-window
  heuristic around alias mentions. Highly nested or table-structured prose, footnote
  cross-references, or unusual phrasings may fail to bind; the gate then falls back
  to a conservative outcome. It does not parse financial statements structurally.
- **Entity identity.** Subsidiary comparison requires a canonical `entity_id` /
  verified `entity_name` already on the datum and an explicit mention in the claim.
  The production canonical map currently carries no such identifiers (and is always
  consolidated), so subsidiary-level numeric *contradiction* via canonical data
  defers to human review by design until a verified entity mapping is supplied.
- **Period / basis nuance.** Annual-vs-quarterly, point-in-time-vs-flow,
  restated-vs-original, and accounting-basis distinctions are handled only to the
  extent they surface as period tokens / currency / scale vocabulary; subtler basis
  differences are deferred, not adjudicated.
- **Causal and legal claims** are never settled by numeric agreement — they are
  capped at `REQUIRES_HUMAN_REVIEW`.

Genuinely ambiguous cases fail conservatively (UNSUPPORTED / REQUIRES_HUMAN_REVIEW)
and are surfaced for analyst attention rather than being resolved automatically.

---

## 9. Branch + worktree status

- Branch: `feat/m3-numeric-binding-v7` (committed here only; no push, no PR, no merge).
- Worktree: `/Users/Daniel/Desktop/SMU/AI in Fin/Credit Memo Testing/kirotesting-integrated/.worktrees/m3-numeric-binding-v7`.
- Commit hash: recorded in the final assistant message.
