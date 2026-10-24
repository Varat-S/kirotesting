# BO + FO Pipeline Integration — Merge & Conflict-Resolution Report

**Integration branch:** `feat/bo-fo-pipeline-integration`
**Base (authoritative):** `main @ cb7918fe47faad0e3d02672144db3655c90166e1`
**Date:** 2026-10-24

## 1. Baseline verification

All remote branches were fetched (`git fetch --all --prune`). The authoritative baseline
named in the handoff matched the live remote exactly:

| Ref | Commit | Notes |
|-----|--------|-------|
| `origin/main` | `cb7918fe47faad0e3d02672144db3655c90166e1` | `feat(bo): complete recorded specialists with challenge` — **matches handoff** |

`main` had **not** advanced beyond the documented baseline, so no intervening-change
review was required. The integration branch was created from `origin/main`.

Each branch to integrate shares `cb7918f` as its merge-base with `main`:

| Branch | Head commit | Ahead of main | Subject |
|--------|-------------|---------------|---------|
| `feat/m3-numeric-binding-v7` | `5ca45d8` | 14 | bind numeric evidence to metric/entity/period in Validator 7 |
| `Ernest's-Branch` | `5af0546` | 2 | Module 4 live test runner and general PDF report builder |
| `Alan's-Branch` | `7a57654` | 4 | FO trigger agents (Debt/EBITDAR, RCF/Debt, EBITDAR Margin, DSO, Liquidity) |

## 2. Merge order and outcome

Merges were performed with `--no-ff` to preserve branch provenance, in dependency order
(M3 foundational corporate structure → M4 which references entity relationships → FO):

1. **`feat/m3-numeric-binding-v7`** → clean merge. 91 files, +13,319 / −12.
2. **`Ernest's-Branch`** (Module 4) → clean merge. 15 files, +2,670. No overlap with M3.
3. **`Alan's-Branch`** (Financial Overview) → **one conflict** in `app/cli.py`.

### Conflict: `app/cli.py`

* **Cause:** M3 and FO both added new subcommands and their `_run_*` helper functions in
  the same regions of the file. `argparse` parser registration and the dispatch block
  auto-merged cleanly (additive, non-overlapping lines); only the two helper-function
  definitions (`_run_m3` from M3 and `_run_fo_case` from FO) landed in the identical
  insertion point and conflicted textually.
* **Resolution:** Kept **both** functions verbatim — no behaviour dropped. Verified that
  all four new parsers (`run-m3`, `run-fo-case`, `fo-review`, `fo-override`) and all four
  dispatch handlers survived the auto-merge, and that the module imports and `--help`
  lists every command. This was a purely mechanical, non-architectural resolution.

No other file required manual resolution. `app/main.py`, `app/migrate.py`,
`app/models/orm.py` (all touched only by FO) auto-merged.

## 3. Shared-infrastructure reconciliation

| Surface | M3 | M4 (Ernest) | FO (Alan) | Reconciliation |
|---------|----|----|----|----------------|
| `app/cli.py` | `run-m3` | — | `run-fo-case`, `fo-review`, `fo-override` | Conflict resolved; all commands coexist |
| `app/prompts/templates.py` | — | appends `MODULE4_PROMPT` to `PROMPT_CATALOGUE` | — | Append-only; catalogue now 9 entries, registers clean |
| `app/main.py` routes | — | — | FO workbench router | Auto-merged |
| `app/models/orm.py` | — | — | 3 FO tables | Auto-merged |
| `app/migrate.py` | — | — | `FINANCIAL_OVERVIEW_TABLES` additive group | Auto-merged |
| `README.md` | — | Module 4 section | FO README under `app/services/fo/` | No overlap |

### Database migration compatibility

* `init_db` creates all **40** tables on a fresh DB, including
  `financial_overview_runs`, `financial_overview_reviews`, `financial_overview_overrides`.
* The additive `app/migrate.py` correctly adds the three FO tables to an older DB that
  predates them (verified by dropping them and re-running the migration).
* No columns were dropped or renamed; the migration remains additive and observable.

## 4. Integration decisions applied

### Module 1 — preserved unchanged
The accepted six-agent design (BO1–BO5 primary + BO6 resilience, plus the roster-v3
`m1_*` agents incl. Agent 6 synthesis), the orchestrator (`business_orchestrator`),
challenger (`business_challenge`), bounded synthesis revision (synthesis V1 → challenger →
synthesis V2), deterministic validation, scoring and human-review controls are untouched.
No previously accepted analytical limitation was reopened.

### Module 3 — full branch integrated (authoritative for entity relationships)
The complete `feat/m3-numeric-binding-v7` branch was merged (not just the latest patch),
preserving: corporate-structure onboarding/transformation (`m3_structure_context.py`,
onboarding transform), standalone M3 execution (`m3_standalone.py`, CLI `run-m3`),
corporate-structure diagrams, structural-credit reasoning, senior-management assessment,
Validator 7 + its numeric evidence binding (`m3_validation.py`), and the existing governed
JSON contracts / evidence references. M3 is authoritative for verified corporate/entity
relationships.

### Module 4 — both specialist capabilities preserved (one governed output)
There are **two internal Module 4 specialist capabilities**, and the integration keeps
both as distinct capabilities under a single governed Module 4 output surface:

1. **Strategy & Capital Allocation / Financial Stewardship** — the existing roster-v3
   agent `m4_strategy_capital` (`app/services/bo/roster_v3.py`), executable in the BO v3
   roster, owning `acquisition_strategy_assessment`, `integration_risk_assessment`,
   `capex_strategy_assessment`.
2. **Debt Structure, Refinancing History & Financial Stewardship** — Ernest's independent
   `app/services/modules/module4/` specialist (`Module4Specialist`) with its own
   schema-validated four-deliverable output (`app/schemas/module4.py`) and a deterministic
   **Validator 8** (`validator8.py`) that raises a **mandatory escalation**
   (`R-MODULE4-VALIDATOR8-01`, severity `MANDATORY`) on failure via the existing
   `EscalationEngine`.

**Validator-id reconciliation.** Both capabilities were associated with the label
"Validator 8". They are kept separate and disambiguated (see
`BO_FO_OUTPUT_CONTRACT.md` §Module 4): the roster-v3 agent's declared `validator_id`
remains `validator_8` (strategy/capital-allocation checks), and Ernest's deterministic
debt-structure validator is identified by its five stable check ids
(`reconciliation`, `subordination_ratio`, `maturity_order`, `legal_entity`,
`double_counting`) under the same Module 4 governance umbrella. Neither capability was
silently replaced. Ernest's specialist reads only shared SEC/structured-financial inputs
and does **not** consume M1/2/3 outputs; it may use verified M3 entity relationships when
supplied but never treats unsupported relationships as fact (the `legal_entity` check
enforces borrower-vs-guarantor attribution).

> **Hardening update (PR #3 follow-up):** at initial merge the "both specialists feed
> one governed Module 4 output" claim was **declarative only** — the debt specialist ran
> solely standalone (scripts/tests). It is now **executable inside the normal BO run**
> via `_BoRun._module4_debt_phase` (`app/services/bo/runner.py`) + `app/services/bo/m4_debt.py`,
> gated by the opt-in `enable_m4_debt_specialist` flag (default off). Both outputs are
> aggregated in `module_conclusions["M4"]` and consumed by synthesis, the challenger and
> final governance. See `BO_FO_INTEGRATION_BASELINE_REPORT.md` §9 (P1).

### Financial Overview — integrated; rating methodology provisional
Alan's seven financial measures, scoring engine, pre-checks, trigger agents,
analyst-review (`fo-review`/`fo-override`), API/UI (`app/api/workbench` FO routes),
runner and reporting were integrated whole. Deterministic numerical calculations are
preserved. The rating methodology is treated as **provisional** and labelled as such.
FO filing identity/provenance is reconciled with the canonical case/evidence architecture
through the artifact registry (see §Artifact registry) so FO can be associated with the
same case and source version as BO without creating an independently authoritative version
of the same financial fact.

## 5. Test summary after integration

* **Clean-sandbox baseline on `main`:** `1323 passed, 131 skipped, 12 errors`.
  The 12 errors are **pre-existing and environmental** — `tests/integration/test_bo_recorded_completion.py`
  requires a git-ignored live HCA R5 artifact (`output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/...`)
  not present in a clean sandbox. They are not regressions.
* **Integrated branch (excluding that environmental file):**
  **`1829 passed, 131 skipped, 0 failed`** — +506 tests from the three branches, all green
  together. No test regressed.
* Skips are the documented local-asset conditions (benchmark reference workbook, offline
  HTML→PDF backend, `google.genai` SDK), identical in kind to the baseline.

## 6. Known limitations / unresolved items carried forward

* `test_bo_recorded_completion.py` (12 tests) cannot run without the git-ignored HCA R5
  live artifact. Documented; not a regression.
* FO rating methodology is provisional (per handoff).
* No live model was invoked. Live HCA runs remain gated behind the explicit opt-in paths
  (`--live`) and were not exercised.
