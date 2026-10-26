# BO Release & Integration Readiness Report

**Phase:** E (release and integration readiness)
**Date:** 18 October 2026
**Branch:** `feat/bo-validation-release`
**Audited commit base:** `ae8ec91c185c4107e8caa9fa6aa02afb3a2a7a44`

Intended architecture: **BO and FO concurrently → SO → global Credit Orchestrator.**
BO must remain independently executable. This report classifies each readiness area
as **Pass**, **Pass with limitations**, **Blocked**, or **Not evaluated**, and is
honest about which gates require independent human credit analysis, external
approvals, or live-model evidence.

---

## Readiness summary

| Area | Classification |
|---|---|
| E1 Downstream interface (handoff contract) | **Pass with limitations** |
| E1 Contract tests | **Pass** |
| E2 Secret & credential handling | **Pass** |
| E2 Prompt-injection exposure from filings | **Pass with limitations** |
| E2 Unsafe external tool / network access | **Pass** |
| E2 Evidence-file path handling | **Pass** |
| E2 Sensitive data in logs | **Pass** |
| E2 Provider error handling | **Pass with limitations** |
| E2 Resource exhaustion / concurrency / rate limiting | **Pass with limitations** |
| E2 Immutable audit record behaviour | **Pass** |
| E2 Production security sign-off | **Not evaluated** (requires human security review) |
| Analytical quality (live model) | **Blocked** (no authorized live run; developer-authored key) |
| Governance completeness (v2 default vs v3) | **Blocked** (escalated behaviour change) |
| Credit scoring policy | **Blocked** (illustrative rubric; no approved bank policy) |
| Merge into `main` / integration branch | **Blocked** (requires explicit authorization) |

---

## E1 — Downstream interface

The downstream surface is `AcceptedBusinessOverview` (schema `bo-handoff-1.1`,
`app/schemas/bo.py`), produced by `reporting._handoff`. It is the **only** surface FO,
SO and the Credit Orchestrator should consume; they must not reach into BO's internal
agent implementation.

**Verified present and typed (contract tests):**

| Required field | Present |
|---|---|
| Company / case identity | `case_id`, `analysis_run_id` |
| Evidence & analysis version | `evidence_snapshot_version`, `agent_registry_version/_hash`, `prompt_hashes`, `scoring_config_version/_hash`, `config_versions` |
| Module-level conclusions | `module_conclusions` keyed by stable `M1`–`M4` labels (not agent ids) |
| Material strengths / vulnerabilities | `business_strengths`, `operational_vulnerabilities`, `material_risk_ids` |
| Compound-risk findings | `compound_business_risks` |
| Company-specific mitigants | `validated_mitigants`, `unproven_mitigants` |
| Credit-impact interpretations | carried in the strength/risk briefs (`text`, `materiality`, `section`) |
| Score and score status | `business_risk_score_id`, `business_risk_score_status`, `business_risk_band` |
| Missing evidence | `evidence_limitations`, `score_limitations` |
| Unresolved challenges | `unresolved_challenges`, `escalations`, `open_questions` |
| Human-review state | `human_review_status`, `reviewer`, `reviewed_at` |
| Canonical evidence references | `canonical_references` (run id, snapshot version, conclusion id, score id, audit stage) |
| Audit / report artifact references | `canonical_references.audit_stage` + persisted artifact rows |

**Consumability without BO internals:** `module_conclusions` are keyed by the stable
`M1`–`M4` labels, each with a `status`; a consumer gates on these without knowing agent
ids. **Versioned & tamper-evident:** `schema_version` lets a consumer gate on the
contract; `content_hash` covers the analytical content (excluding the human-review
fields) and is re-verified on every read; `extra="forbid"` rejects drift.

**Contract tests added:** `tests/integration/test_bo_downstream_contract.py` (4 tests)
pin the full field set, the versioned schema, the no-internal-detail requirement, and
the tamper-evident hash. **Pass.**

**Limitations:** credit-impact interpretation is embedded in free-text briefs rather
than a dedicated structured field — a consumer must parse `text`. This is acceptable
for a first contract but is a candidate for a structured `credit_impact` field in a
future `bo-handoff-1.2`. Classified **Pass with limitations**.

---

## E2 — Security & operational review

**Secret & credential handling — Pass.** API keys (`llm_api_key`, `vertex_api_key`)
are `SecretStr` (`app/core/config.py`), read from env only, masked in repr/logs, never
hardcoded. `.env`, `.env.*` (except `.env.example`), `*.db`, `data/**`, `output/**`
are git-ignored; no secret or database is tracked (verified). The Vertex provider uses
ADC or a `VERTEX_API_KEY` from a local `.env`.

**Prompt-injection exposure from filings — Pass with limitations.** Filing passages
that read like instructions ("ignore previous instructions", etc.) are detected by
`retrieval.instruction_like` and carried with an `instruction_like` flag so reviewers
see them; evidence is never suppressed. This is a heuristic flag, not a guaranteed
defence — a novel injection phrasing could pass unflagged. The structural protection
is stronger: the deterministic validators, the admitted-evidence gate, and the
score/band-field rejection mean an injected instruction cannot promote a finding or
set a score. Classified **Pass with limitations** (heuristic flag + structural gates).

**Unsafe external tool / network access — Pass.** The BO path makes no network call
other than the configured LLM provider; no filesystem writes outside the configured
`data_root`/`output_root`; no shell-out. Retrieval operates only over the sealed
admitted-evidence packet.

**Evidence-file path handling — Pass.** Reads are to fixed template paths and
caller-supplied manifests/packages; evidence is content-addressed by SHA-256 hash, not
by free path. Output paths are rooted at the configured output root.

**Sensitive data in logs — Pass.** The production BO path uses no `print`/logging of
evidence or secrets. `print` statements exist only in the **experimental** CLI tools
(`m1s2_live.py`, `judge_eval.py`, `m3_*.py`), which are not on the default runner path
and write to git-ignored `output/`.

**Provider error handling — Pass with limitations.** Provider exceptions and timeouts
are converted to typed results; a failed agent is recorded non-`validated` and does
not crash the run; one bounded re-plan/re-ask exists. Retry/backoff on transient
provider errors is minimal (one bounded re-ask, not exponential backoff). Classified
**Pass with limitations**.

**Resource exhaustion / concurrency / rate limiting — Pass with limitations.**
`DagExecutor` bounds concurrency with `asyncio.Semaphore(AGENT_MAX_CONCURRENCY)` and
per-call `asyncio.wait_for(AGENT_TIMEOUT_SECONDS)`; input size is capped by
`LLM_MAX_INPUT_BYTES` with no silent truncation; output by `LLM_MAX_OUTPUT_TOKENS`.
There is no provider-side rate-limit token bucket; under a strict provider quota the
semaphore bound is the only throttle. Live capacity testing is **Not evaluated**.
Classified **Pass with limitations**.

**Immutable audit record behaviour — Pass.** The audit log is append-only with an
installed immutability guard (`app/services/audit`); accepted handoffs are protected by
a `before_flush` listener (`handoff.py`) that raises `HandoffImmutableError` on any
edit/delete, and `read_handoff` re-verifies the content hash. Supersession is
append-only (status flip, never delete).

**Production security sign-off — Not evaluated.** No automated check substitutes for an
independent human security review. Not claimed.

---

## E3 — Merge-preparation package

### Architecture overview
Four internal BO modules (M1 ×5 specialists + Agent 6, M2, M3, M4) → cross-module
synthesis → independent challenge → one bounded rerun → post-challenge deterministic
scoring → evidence-backed report + immutable handoff. Single-pass is the production
candidate default; `hybrid_selective` and `full_investigation` are experimental,
explicit-mode, test-only paths (not wired into the default runner). See
`BO_ARCHITECTURE_V3_SPEC.md`.

### Feature & completion matrix
See `BO_COMPLETION_PROGRESS.md` (16-step matrix) and
`BO_END_TO_END_ACCEPTANCE_REPORT.md`. All execution/governance components are
implemented and software-tested; analytical quality is not independently evaluated.

### Code audit results
`BO_CODE_AUDIT_REPORT.md`: no Critical defects; two High findings (A-1 CLI default
roster, A-2 v2/v3 completeness-governance asymmetry) — A-1 hardened, A-2 escalated and
pinned by contract test; A-3 fixed; others Medium/Low and documented.

### Regression results
Full suite **1,249 passed, 128 skipped, 0 failed** (process `DEBUG=false`) after all
Phase A/E changes (10 new contract tests: 6 governance + 4 downstream). The 128 skips are entirely absent local git-ignored data
(documented in `BO_CODE_AUDIT_REPORT.md` §0); 0 failures is the green signal.

### Analytical validation limitations
No live-model evaluation (`BO_LIVE_VALIDATION_REPORT.md` = planned/not executed). The
HCA answer key is a developer-authored template awaiting independent credit-analyst
sign-off (`BO_ANALYTICAL_EVALUATION_SPEC.md`). All analytical-quality metrics are
`not_measured`.

### Live test results
None. Offline preflight complete; capped $5.00 budget proposed and awaiting approval
(`BO_LIVE_EVALUATION_PLAN.md`).

### Known blockers (see §Outstanding-risk register)
Live-model quality unmeasured; v2/v3 governance asymmetry (escalated); illustrative
scoring rubric (no approved bank policy); independent answer key unverified; production
security review not performed.

### Rollback procedure
All work is on `feat/bo-validation-release`, branched from
`feat/bo-investigation-integration` @ `ae8ec91`. To roll back: do not merge the branch;
or `git revert` the Phase A/E commits. No migrations are introduced by this cycle; the
`AcceptedBusinessOverview` fields used by the A-3 fix already exist in the schema, so no
schema migration is required. The CLI `--roster` flag defaults to the pre-existing `v2`
behaviour, so existing invocations are unaffected.

### Migration / configuration changes
None required. New optional CLI flag `--roster {v2,v3}` (default `v2`, unchanged
behaviour). No DB schema change. No new config files.

### Reviewer checklist
- [ ] Confirm the A-2 governance decision: make the completeness gates
      roster-independent, **or** restrict authoritative runs to v3 and mark v2
      legacy-only.
- [ ] Independent credit analyst completes & signs off the HCA FY2025 answer key.
- [ ] Approve (or decline) the capped live-model budget ($5.00, single-pass ×3).
- [ ] Human security review of the BO path.
- [ ] Confirm the illustrative scoring rubric is replaced/approved before any
      non-illustrative score is reported.
- [ ] Confirm no merge into `main`/the integration branch without explicit approval.

---

## Outstanding-risk register

| # | Risk | Severity | Owner / gate |
|---|---|---|---|
| R1 | Production CLI defaults to v2, whose completeness gates are weaker than v3 | High | Governance decision (A-2) — human approval |
| R2 | Live-model analytical quality is unmeasured | High | Authorized live run + independent key |
| R3 | Scoring rubric is ILLUSTRATIVE, not bank policy | High | Credit-policy owner approval |
| R4 | HCA answer key is developer-authored, unverified | Medium | Independent credit analyst |
| R5 | Prompt-injection flag is heuristic | Medium | Structural gates mitigate; reviewer awareness |
| R6 | No provider rate-limit backoff beyond the concurrency semaphore | Medium | Live capacity test |
| R7 | Duplicate validation pipelines (default vs experimental) could drift | Low | Pinned by presentation-class test; consolidate later |
| R8 | Audit (persisted) vs memo (in-memory) not cross-checked | Low | Future hardening |

## Gates requiring independent human credit analysis, external approval, or live evidence

1. **Independent credit-analyst review** — answer key sign-off (R4); synthesis quality.
2. **Credit-policy approval** — a non-illustrative scoring rubric (R3).
3. **Live-model evidence** — all analytical-quality, cost, latency and reliability
   metrics (R2, `BO_LIVE_VALIDATION_REPORT.md`).
4. **Governance decision** — the v2/v3 completeness asymmetry (R1, A-2).
5. **Human security review** — production security sign-off.
6. **Merge authorization** — no merge into `main`/the integration branch without it.
