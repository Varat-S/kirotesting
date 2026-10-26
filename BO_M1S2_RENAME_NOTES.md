# Misnomer cleanup: "M2" (workstream) → "M1S2"

## Why

The repo had **two unrelated things both called "M2"**:

1. **Module 2 — Industry Information** (`m2_industry`, Validator 6). This is the
   real Business Overview architecture module. **Left untouched — it is correct.**
2. **Workstream / milestone "M2"** — the second BO investigation-integration work
   package (per-specialist investigation + controlled comparison), which was only
   ever exercised on **Module 1 Specialist 2 (M1S2, Customer Stickiness &
   Concentration)**. The "M2" label here was a misnomer: it is about **M1S2**, not
   Module 2.

This note records what was renamed to remove the misnomer, and what was
intentionally left as-is (and why), so no one is surprised by either.

## What WAS renamed (human-facing: files, folders, docs, comments)

Folders / files (`git mv`, history preserved):

| Old | New |
|---|---|
| `examples/bo_m2/` | `examples/bo_m1s2/` |
| `tests/integration/test_bo_m2_comparison.py` | `tests/integration/test_bo_m1s2_comparison.py` |
| `tests/integration/test_bo_m2_hca.py` | `tests/integration/test_bo_m1s2_hca.py` |
| `app/services/bo/m2_live.py` | `app/services/bo/m1s2_live.py` |
| `BO_M2_COMPARISON_REPORT.md` | `BO_M1S2_COMPARISON_REPORT.md` |

Reference / prose updates (so nothing points at a dead name):
- Docstrings and the `-m` invocation path in `m1s2_live.py`
  (`python -m app.services.bo.m1s2_live`), its output dir default
  (`output/m1s2_live`), temp prefix (`m1s2live_`) and transcript titles.
- `app/services/bo/comparison.py` module docstring.
- The renamed test files' docstrings and their links to the report.
- Cross-file references in `BO_LIVE_EVALUATION_PLAN.md`,
  `BO_RELEASE_READINESS_REPORT.md`, `.agents/tasks/m3-structural-credit-plan.md`,
  `.agents/tasks/m3-analytical-quality-plan.md`.
- `examples/bo_m3/evaluation_inventory.json` paths (`examples/bo_m2/...` →
  `examples/bo_m1s2/...`).

All renamed-file references were verified: a repo-wide search for the old names
returns no matches, and the affected tests pass.

## What was INTENTIONALLY NOT renamed (machine-facing identifier VALUES)

These are internal identifier **string values** that no human reads in a UI but
that are **persisted in committed JSON fixtures** (some of them in Module 3's
fixtures). Changing the *values* would force regenerating those fixtures and
could shift hash-adjacent regression pins — i.e. it would risk other people's
code for zero readability gain. They are left exactly as-is on purpose:

| Identifier (value kept) | Where | Why not changed |
|---|---|---|
| `"ar_m2_"` analysis-run-id prefix | `app/services/bo/comparison.py` (and embedded in `examples/bo_m3/structural_credit/demo_output/**` fixtures, `examples/bo_m1s2/*/results.json`) | The prefix is baked into committed run-id strings across M3 fixtures; renaming the value would require regenerating all of them. |
| `COMPARISON_VERSION = "m2-comparison-1"` | `app/services/bo/comparison.py` (persisted in `examples/bo_m1s2/*/results.json`) | Version tag stored in committed comparison fixtures; a rename breaks the regression comparison unless fixtures are regenerated. |
| `"HCA_2025_M2"` case-id | tests + `examples/bo_m1s2/hca_m1s2/{manifest,results}.json`, `examples/bo_m1s2/judge_eval/hca_cases*.json` | A persisted DB key stored in committed fixtures; it must be renamed in tests and fixtures in lockstep, so it is left as a stable id. |

A short comment to this effect is in `app/services/bo/comparison.py` next to the
`COMPARISON_VERSION` / `ar_m2_` definitions.

## Also left as-is (ambiguous / not the misnomer)

- `app/models/orm.py` — the comment "skeleton for M2": an old generic
  placeholder, not clearly the M1S2 workstream nor Module 2. Left untouched.
- `.kiro/specs/credit-memo-business-overview-v2/tasks.md` — "BO-M2 Contracts and
  prompts": reads as a genuine Module-2 milestone, so treated as legitimate.
- All legitimate **Module 2 Industry** references (`m2_industry`, "M2 Industry",
  Validator 6, `required_module_coverage: [M1,M2,M3,M4]`): these are correct and
  were not changed.

## If a teammate wants the machine-facing values renamed too

It is doable but must be a deliberate, isolated change: update the Python
constant/prefix/case-id AND regenerate every committed fixture that stores the
old value (M1S2 results + the M3 `structural_credit/demo_output` manifests), then
run the full suite to re-pin regressions. Until then, treat `ar_m2_` /
`m2-comparison-1` / `HCA_2025_M2` as historical identifiers that happen to carry
the old "M2" tag.
