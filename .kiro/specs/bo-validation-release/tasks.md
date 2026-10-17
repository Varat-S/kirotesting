# BO Validation, Benchmarking & Release Readiness — task tracking

Narrowly scoped to validating, evaluating and preparing the **existing** Business
Overview (BO) subsystem for release. This spec does **not** redesign the BO
architecture or add agents. It tracks the offline, pre-budget work authorized by the
BO Validation handoff.

Branch: `feat/bo-validation-release` (from `feat/bo-investigation-integration`
@ `ae8ec91c185c4107e8caa9fa6aa02afb3a2a7a44`).

## Phase 0 — Sync & baseline

- [x] 0.1 Verify git state; confirm `origin/feat/bo-investigation-integration` HEAD == `ae8ec91`.
- [x] 0.2 Create working branch `feat/bo-validation-release` from the verified SHA.
- [x] 0.3 Set up locked env (`uv sync --locked --python 3.11 --extra dev`); Python 3.11.15, pytest 9.1.1.
- [x] 0.4 Reproduce regression baseline. Result: **1,239 passed, 128 skipped, 0 failed** (process `DEBUG=false`). Delta vs documented 1,354/13 is 100% missing local git-ignored data (benchmark workbook ×94, OCR ×5, filings/manifest ×3, PDF backend ×1, google-genai ×1, gated live test ×1).
- [x] 0.5 Confirm no secrets/dbs/generated data tracked; `.env` absent; tree clean.

## Phase A — Independent code & architecture audit

- [x] A.1 Orchestration & dependency correctness (runner, DAG concurrency, Agent 6 gate).
- [x] A.2 Evidence integrity invariants.
- [x] A.3 Governance & auditability (run identity, immutability, status propagation).
- [x] A.4 Complexity & duplication.
- [x] A.D Deliverable: `BO_CODE_AUDIT_REPORT.md`.
- [x] A.F Fixed A-3 (handoff score with no conclusion) + A-1 (CLI `--roster`, roster recorded in summary). A-2 (v2/v3 governance asymmetry) escalated for approval + pinned by contract test. New: `tests/integration/test_bo_governance_contract.py` (6 tests). Full suite 1,245 passed / 128 skipped / 0 failed.

## Phase B — Analytical evaluation framework

- [ ] B.1 Evaluation cases (HCA FY2025; cross-sector; contradictory/incomplete).
- [ ] B.2 Analyst-reviewed answer-key schema (developer-authored, awaiting review).
- [x] B.3 Evaluation metrics & rubric.
- [x] B.D Deliverable: `BO_ANALYTICAL_EVALUATION_SPEC.md` (answer-key schema v1.1, fact-type taxonomy, metrics, sampling, reviewer instructions, provenance).

## Phase C — Prepare live end-to-end BO execution (NO paid calls)

- [x] C.1 Preflight COMPLETE (offline): HCA 10-K present (sha256 2043cd03…), all 8 primaries route, 9 packets, config/schema/limit checks done.
- [x] C.2 Capped cost-authorization proposal ($5.00 hard cap, Config A × 3; grounded in real M2 paid anchors).
- [x] C.3 Controlled live configurations (single-pass A primary; B/C experimental, caveated).
- [x] C.D Deliverable: `BO_LIVE_EVALUATION_PLAN.md`. STOPPED before paid calls.

## Phase D — Quality & efficiency evaluation

- [x] D.D Deliverable: `BO_LIVE_VALIDATION_REPORT.md` — status: **PLANNED / NOT EXECUTED** (no live evidence; no paid call made or authorized).

## Phase E — Release & integration readiness

- [ ] E.1 Downstream handoff interface + contract tests.
- [ ] E.2 Security & operational review.
- [ ] E.3 Merge-preparation package.
- [ ] E.D Deliverable: `BO_RELEASE_READINESS_REPORT.md`.

## Close-out

- [ ] Z.1 Full regression re-run; contract/security tests added.
- [ ] Z.2 Confirm no secrets/env/large datasets staged.
- [ ] Z.3 Push `feat/bo-validation-release`; report branch, SHA, tests, limits, approvals.
- [ ] Z.4 Outstanding-risk register + gates needing human/analyst/live evidence.
