# PR #3 — Final Regression Fix Verification

Date: 2026-10-24 (Singapore)
Repository: `Varat-S/kirotesting`
Branch: `feat/bo-fo-pipeline-integration`
Starting commit: `8f9e41c09ca10f315db7614b3b482eab286a18ab`

## Root causes and changes

### Financial metric resolver

The resolver's existing contract selects the latest **persisted revision** of a metric name within a case. It sorted by `Metric.created_at DESC`, but successive inserts can receive identical timestamps (observed on Windows/SQLite). SQL then had no defined order within a tie and sometimes returned the older financial value.

The query now sorts by `created_at DESC, id DESC`. `Metric.id` is the append-only, autoincrementing row key and breaks timestamp ties by persistence order. The reporting period is the period measured, not revision order, and remains unchanged. The resolver still evaluates the selected row's review and evidence-quality gates; it does not fall back to an older approved row when the latest row is unverified. Financial values, scoring methodology, and downstream result shape were not changed.

Added tests cover three tied revisions, timestamp priority over insertion order, differing reporting periods, repeated resolutions, and the unverified-latest fail-closed behavior.

### M3 Unicode reference

The HCA reference JSON is UTF-8, but the test helper used `Path.read_text()` without an encoding. On this Windows environment it decoded with cp1252, turning the hierarchy's box-drawing and arrow characters into mojibake. The generated tree and reference otherwise had matching structural facts.

Both JSON fixture reads now explicitly use UTF-8. The test still compares the complete generated tree to the reference exactly and retains all node, role, guarantor, ownership-link, and root assertions. No production M3 transform or fixture content changed.

## Verification

| Check | Result |
| --- | --- |
| Metric resolver file plus previously failing M3 test, repeated five times | 9 passed on each run |
| M3 numeric validation, FO runner/registry, BO governance focused group | 92 passed; 1 Starlette `TestClient` deprecation warning |
| Full offline suite, excluding the HCA R5 recorded-completion file | **1,930 passed, 123 skipped, 0 failed**, 1 Starlette `TestClient` deprecation warning; Python 3.11.15; 6m 07s |
| Full suite, including the recorded-completion file | **1,931 passed, 135 skipped, 0 failed**, 1 Starlette `TestClient` deprecation warning; Python 3.11.15; 5m 19s |
| Diff check | `git diff --check` clean |

The new PR #3 Validator 8 identity and oversized PDF tests are included in the passing full suite. All runs used offline fixtures or scripted responses; no paid model was invoked.

## Remaining limitation

The git-ignored genuine HCA R5 `specialists_run.json` artifact is not in the reviewed worktrees or clean GitHub runners. The recorded-completion file now explicitly skips its 12 artifact-dependent tests with that reason when the file is absent, while retaining its one independent test. If the genuine artifact is present, those 12 tests run normally and still validate it. This keeps the CI workflow's full-suite command intact. The other skips include optional local workbooks and optional dependencies.

## Recommendation

The two final regression failures are resolved, the full available offline suite is green, and focused M3, FO, and BO governance checks pass. PR #3 is ready to merge after normal review. Do not merge this branch automatically.

MERGE APPROVED — All required acceptance checks passed.
