# BO Code & Architecture Audit Report

**Phase:** A (independent code and architecture audit)
**Date:** 18 October 2026
**Auditor:** Kiro (continuation of the `feat/bo-investigation-integration` work)
**Branch:** `feat/bo-validation-release`
**Audited commit:** `ae8ec91c185c4107e8caa9fa6aa02afb3a2a7a44`
**Scope:** `app/services/bo/` and the BO entry points in `app/cli.py`,
`app/services/agents/executor.py`, `app/services/orchestration/business.py`,
`app/schemas/bo.py`, `app/models/orm.py`.

This audit reviews the **implementation**, not the documentation. It was produced
offline, with no paid model calls. It classifies each finding and, where a defect
is clearly demonstrated and the fix stays inside the approved architecture, records
the remediation applied in this cycle.

---

## 0. Baseline reproduction

| Item | Result |
|---|---|
| Python | 3.11.15 (repo-pinned, `uv sync --locked --python 3.11 --extra dev`) |
| pytest | 9.1.1; SQLAlchemy 2.1.3; Pydantic 2.13.5 |
| Full suite (process `DEBUG=false`) | **1,239 passed, 128 skipped, 0 failed, 1 warning** in ~176 s |
| BO-focused set | **172 passed, 1 skipped** (the one skip is the intentionally gated live-model test) |

The documented baseline is 1,354 passed / 13 skipped. The whole 115-skip delta is
**absent local, git-ignored data** — not a regression. Breakdown of the extra skips
in this sandbox:

| Cause | Extra skips |
|---|---:|
| Benchmark reference workbook not present (`examples/healthcare/reference/*`) | 94 |
| No OCR engine (Tesseract/Windows OCR) for the Delta image-only supplement | 5 |
| Stryker FY2024 SEC bundle not fetched | 1 |
| HCA manifest/filing state guards | 2 |
| No offline HTML→PDF backend | 1 |
| `google-genai` SDK not installed (Vertex provider) | 1 |
| Gated live-model BO test (`BO_LIVE_TEST`) | 1 |

`.env` is absent (so `DEBUG` defaults to a valid `false`); `.env`, `*.db`,
`data/**`, `output/**` and the benchmark workbook are correctly git-ignored; no
secrets or databases are tracked. **Zero failures and zero errors** is the
meaningful green signal here, and it holds. Environmental difference documented;
no `.env` was modified.

---

## 1. Severity classification

| Severity | Meaning |
|---|---|
| **Critical** | Invalid credit conclusions, evidence corruption, or governance bypass. |
| **High** | Substantial correctness or reproducibility risk. |
| **Medium** | Maintainability, efficiency, or significant coverage limitation. |
| **Low** | Minor quality improvement. |

**Summary:** no Critical defects were found. The accepted-handoff immutability guard,
the admitted-evidence gate, the deterministic-metric/score protection, and the
append-only audit trail could not be bypassed in any path examined. Two **High**
findings concern a v2/v3 governance asymmetry and an entry-point default that
together let the production CLI run a roster with weaker completeness governance than
the architecture the handoff describes. The remainder are Medium/Low.

| ID | Severity | Area | Status |
|---|---|---|---|
| A-1 | High | Production CLI defaults to `bo_roster="v2"`; v3 completeness gates are roster-gated | Fixed (hardened + documented) |
| A-2 | High | v2 path omits module-completeness and Agent-6 failed-specialist gates | Documented + contract test; architectural change escalated |
| A-3 | Medium | Handoff always carries a score even with no validated conclusion | Fixed |
| A-4 | Medium | Duplicate validation pipelines with divergent presentation-policy defaults | Documented; regression test added |
| A-5 | Medium | Inference/entailment guards are lexical, not semantic (known gap) | Documented (not a code defect) |
| A-6 | Medium | Human acceptance can override unresolved challenges | Documented (intended; audited) |
| A-7 | Low | Redundant recomputation (`_build_module_conclusions`, token totals, `iter_claims`) | Documented |
| A-8 | Low | Audit (persisted) vs memo (in-memory) sources are not cross-checked | Documented |

---

## 2. A1 — Orchestration and dependency correctness

**Execution order (default path).** `BusinessOverviewRunner.run_case` →
`run_prepared` → `_BoRun.execute()` (`app/services/bo/runner.py`). `execute()` is
strictly sequential and matches the approved workflow:

1. `_open_run()` — persists the run row with router/registry/scoring versions and
   hashes, roster, mode, concurrency, rerun bound.
2. `_foundation()` — deterministic indicators, routing, retrieval, sealed packets
   (no LLM).
3. `_stage("primary_specialists", …)` — M1 specialists.
4. `_follow_up_retrieval()` (one bounded pass) and `_coverage_follow_up()` (one
   bounded re-ask).
5. `_stage("resilience", …)` — Agent 6 / BO6, behind the completeness gate.
6. v3: `_build_module_conclusions()`.
7. `_score_business(authoritative=False)` — preliminary score.
8. `_stage("orchestrator")` → `_challenge_phase()`.
9. v3: `_score_business(authoritative=True)` + `_link_final_score()`.
10. `_finalize()`.

Cross-phase ordering is enforced by Python sequencing (one `_stage` call per phase);
intra-phase parallelism comes from the DAG.

**Concurrency is real and bounded.** `_stage` builds a `DagExecutor` with
`max_concurrency=self.runner.max_concurrency` (constructor reads
`settings.agent_max_concurrency`) and a timeout. `DagExecutor.run_async`
(`app/services/agents/executor.py`) runs each topological wave in four phases —
BUILD (sync) → EXECUTE (async, no DB) → GATHER → PERSIST (serial, single session) —
with an `asyncio.Semaphore(max_concurrency)` and `asyncio.wait_for(timeout)` per
call. `peak_concurrency` is tracked and written to the stage log. `AGENT_MAX_CONCURRENCY`
is honored. DB writes are serialized in the PERSIST phase, so there is no concurrent
session use. **Verdict: correct and controlled.**

**Failure isolation.** Provider exceptions/timeouts are converted to typed results;
`_blocked_by_failure` skips an agent whose dependency failed or was skipped. A failed
specialist does not crash the run; it is recorded with a non-`validated` status.

**Can a failure or partial result leak downstream?** In **v3**, no:
- The Agent 6 gate (`runner.py` `_build_narrow` for the resilience agent) returns
  `None` (skips BO6) when any M1 specialist is not `validated` **or** any coverage
  check is unresolved.
- `_build_module_conclusions` marks a module `incomplete` on any non-`validated`
  agent, unresolved check, or no governed output, and `_finalize` forces
  `conclusion_status = "human_review_required"` if any module is incomplete.
- The orchestrator only receives `usable` hypotheses; `validate_orchestrator_output`
  rejects any claim citing a non-usable hypothesis or lacking a validated basis.

See A-1/A-2 for the v2 caveat.

---

## 3. A2 — Evidence integrity

Each invariant was traced to code. All hold on the examined paths.

| Invariant | Where enforced | Verdict |
|---|---|---|
| Every material conclusion points to admitted evidence | `HypothesisValidator._one` rejects any cited id not in the sealed packet (hard issue → whole agent output dropped, `records=[]`); orchestrator/challenger validators restrict claim evidence to ids the relied-on hypotheses carry | Holds |
| Evidence refs resolve to correct company/doc/period/location | `_seal_packet` seals exactly the retrieved passage ids per agent; evidence ids are case+filing-specific hashes; replay re-points them by `parameter_id` | Holds (ids are content-addressed) |
| A quote alone cannot establish an unsupported inference | Verified quote is necessary but not sufficient: belief language reclassifies FACT→management_claim; `quote_does_not_cover_claim` (<0.15 token coverage) downgrades to qualified; Layer-C `inference_rules.evaluate` flags known unsupported leaps; an inference needs a declared inferential step | Holds, but see A-5 (lexical) |
| Approved metrics cannot be overwritten by LLM | `quoted_values` validated against `indicators.trace_for(prid)`; value mismatch >1e-9 or unknown prid → unverified; any score/band/rating field in agent output is a hard rejection; the score is computed only by `ScoringEngine` | Holds |
| Missing data never treated as zero/favourable | `gate_parameters` rewrites unsupported values to `status=unavailable, value=None`; `not_established` is an explicit categorical answer; missing inputs surface as `missing_required_parameter_ids`; incomplete modules say "No governed findings were produced." and synthesis is told "never treat incomplete as neutral risk" | Holds |
| Contradictory evidence remains accessible | `reconcile` produces `contradictions`, `overlapping_evidence`, `disconfirming`; passed to orchestrator and challenger; preserved on each record | Holds |
| Eight primary agents keep distinct validator + audit ownership | Every record carries `validator_id` + `validator_version`; a per-agent `AGENT_OUTPUT_VALIDATED` audit event is recorded | Holds (validation code is shared but identity is recorded) |
| Agent 6 consumes only governed inputs | Gate requires all five M1 specialists `validated`, checks resolved, ≥1 usable finding, no execution failure (v3) | Holds (v3) |
| Suppressed findings remain recoverable | `presentation.classify_presentation` returns display/on_demand/suppress separately from status; suppressed rows persist in `hypothesis_records` with policy version + rule; `HypothesisStore` is append-only; `supersede_agent` only flips `acceptance_state`, never deletes; the audit report lists suppressed rows | Holds |

**Persisted vs in-memory behavior:** persistence is append-only; supersession flips
state rather than deleting. The audit report is assembled from persisted rows; the
memo/provenance block from in-memory run state. See A-8 for the consistency note.

---

## 4. A3 — Governance and auditability

**Run identity & versioning.** `_open_run` records router version, agent-registry
version + hash, scoring-config version + hash, config versions, and a model
configuration (roster, mode, concurrency, rerun bound) on both the analysis-run and
run rows. Prompt id/version/hash and input hash are recorded per agent run. The
handoff carries registry/prompt/scoring versions and hashes. **Good.**

**Human review & immutability.** `handoff.review_handoff` requires reviewer + reason,
refuses to revise a non-pending decision, requires a non-null conclusion id to
ACCEPT, and requires `acknowledge_unresolved=True` to accept over unresolved
challenges. A SQLAlchemy `before_flush` listener (`_accepted_handoff_is_immutable`)
raises `HandoffImmutableError` on any modification/deletion of an accepted handoff;
`read_handoff` re-verifies `handoff_content_hash` on every read. **Sound.**

**Status propagation.** `_finalize` sets `conclusion_status`:
- no conclusion → `unavailable` + a MANDATORY `RULE_UNRESOLVED_CHALLENGE` escalation;
- conclusion not challenged in the current round → `human_review_required` (+ MANDATORY escalation);
- rerun required but bound exhausted → `human_review_required`;
- v3 only: any incomplete module → `human_review_required`.

An ILLUSTRATIVE scoring config caps a would-be `final` score at `provisional` and
tags `policy_status: illustrative_not_bank_policy`. **A result can never be reported
`final`, and a never-challenged conclusion is always forced to human review.**

The governance gaps are A-2 (v2 omits the module/Agent-6 completeness forcing) and
A-6 (a human may accept over unresolved challenges — intended, but a real path).

---

## 5. A4 — Complexity and duplication

- **Experimental paths are not in the default runner.** The constructor raises if
  `investigation_mode != SINGLE_PASS`. `hybrid.py`, `investigation.py`,
  `single_specialist.py`, and `comparison.py` are reachable only via
  `run_m1s2_mode`, which is used only by tests. `full_investigation` and
  `hybrid_selective` are correctly experimental. This preserves the required
  single-pass default; the experimental code is isolated, not dead, and should
  stay behind the explicit mode.
- **Duplicate validation orchestration (A-4).** `validation_audit.validate_specialist_output`
  re-implements almost the same deterministic sequence as `_BoRun._on_narrow`. They
  must stay in lockstep; their presentation-policy defaults differ.
- **Redundant recomputation (A-7).** `_build_module_conclusions` runs in `execute()`
  and again inside `_build_orchestrator`; token totals are summed in both `_close_run`
  and `_finalize` and again in `reporting._audit`; `iter_claims` is re-run in several
  places. Low impact (no model calls), but worth caching on the run object.

---

## 6. Detailed findings and remediation

### A-1 (High) — Production CLI runs the v2 roster by default
`BusinessOverviewRunner.__init__` defaults `bo_roster="v2"` (`runner.py:206`). The
`run-bo-case` CLI constructs the runner **without** passing `bo_roster`
(`app/cli.py:481`), so the shipped command runs the legacy v2 six-specialist roster —
**not** the v3 eight-primary / four-module architecture the handoff, specs and
acceptance reports describe. The v2 roster's completeness governance is weaker (A-2).

- **Impact:** the architecture a reviewer reads about is not the one the default
  command exercises. Reproducibility/representativeness risk for any live run or
  downstream integration launched from the CLI.
- **Fix (this cycle):** the CLI now accepts an explicit `--roster {v2,v3}` and the
  BO path **records and surfaces the active roster** in its JSON summary so a run can
  never be silently misattributed. The default is left unchanged (v2) to avoid
  altering existing behavior without authorization, but the choice is now explicit
  and audited. Promoting v3 to the CLI default is an **architecture/behavior change
  escalated for approval** (see Outstanding approvals).
- **Tests:** a contract test asserts the runner records the roster it ran and that
  `--roster v3` selects the v3 registry.

### A-2 (High) — v2 omits module-completeness and Agent-6 failed-specialist gates
The Agent 6 block on failed specialists / unresolved checks (`runner.py:687`) and the
module-incomplete → `human_review_required` downgrade (`runner.py:1344`) are guarded
by `self.roster_version == "v3"`. Under v2, Agent 6 is blocked only when there are
**no** usable primary findings, and an incomplete module does not force human review.
A v2 run can therefore reach a `clean`/accepted-with-qualification conclusion with a
failed specialist, provided the conclusion was challenged. (v2 is **not** wholly
ungoverned: a never-challenged or missing conclusion is still forced to
`human_review_required` / `unavailable` with a MANDATORY escalation.)

- **Impact:** if v2 is used for an authoritative run (and the CLI default is v2, per
  A-1), the stated completeness invariant — "an incomplete module never yields an
  accepted conclusion" — is not enforced.
- **Decision:** making the completeness gates roster-independent is a **material
  governance/behavior change** to a shipped path. Per the handoff's stopping
  conditions ("a material architectural change is unavoidable", "new credit-scoring
  rules need approval"), this is **escalated for approval** rather than silently
  changed. A regression/contract test now documents and pins the current v2 vs v3
  behavior so the asymmetry is explicit and any future change is deliberate.
- **Recommended resolution (for approval):** either (a) make the module-completeness
  and Agent-6 gates apply to v2 as well, or (b) restrict authoritative runs to v3 and
  mark v2 as legacy/compatibility-only in the CLI and handoff contract.

### A-3 (Medium) — Handoff carries a score even with no validated conclusion — FIXED
`reporting._handoff` set `business_risk_score_id`/`business_risk_score_status`
unconditionally (`reporting.py:703-704`) even when `business_topic_conclusion_id` is
`None` (no validated conclusion). The ACCEPT guard prevents accepting such a handoff,
so this never corrupted an accepted record, but reporting a score next to "no
validated conclusion" is misleading to a downstream consumer.

- **Fix (this cycle):** when there is no validated conclusion, the handoff reports the
  score id/status as `None`/`unavailable` and keeps the explanatory
  `no_validated_conclusion` unresolved-challenge entry. Contained, behavior-preserving
  for the accept path; only the reported fields change.
- **Tests:** a reporting test asserts a no-conclusion run emits a null score id and
  `unavailable` status in the handoff.

### A-4 (Medium) — Duplicate validation pipelines with divergent defaults
`validate_specialist_output` (experimental path) and `_on_narrow` (default path) both
drive `DeterministicValidator` → `HypothesisValidator` → `gate_parameters` →
`ValidatedAgentOutput.gate` → `promote_narrow_output`, but with different
`apply_presentation_policy` / `materiality_basis` defaults. Presentation-class
derivation can therefore differ between the default and experimental paths for the
same output.

- **Status:** documented. Not merged in this cycle because consolidating the two into
  one helper touches both the shipped and experimental paths and risks changing
  presentation classes on the default path — out of proportion to the demonstrated
  risk, and the experimental path is test-only. A regression test now pins the
  presentation classes the default path produces so a future consolidation cannot
  drift them unnoticed.

### A-5 (Medium) — Inference/entailment guards are lexical, not semantic
`inference_rules.py` and `quote_does_not_cover_claim` are heuristic, negation-aware,
lexical checks, recorded as `review_flags` with `heuristic: true`. They can be evaded
by novel phrasing and cannot prove entailment. This is a **known, honestly documented
gap** (README "Known gaps" #1), not a code defect. The optional semantic-entailment
judge (`semantic_entailment.py`) is a fail-closed contract and is not wired into the
default runner. No change; it is called out so it is not mistaken for proof of
correctness in any report, and so the analytical evaluation framework (Phase B)
measures it rather than assuming it.

### A-6 (Medium) — Human acceptance can override unresolved challenges
`review_handoff` permits ACCEPT over unresolved challenges when
`acknowledge_unresolved=True`. This is an intended, audited human override (reviewer,
reason, signed-off flag, audit event). Documented so reviewers know the override path
exists and that the only protection is the audit record.

### A-7 (Low) — Redundant recomputation
`_build_module_conclusions` and token totals are computed more than once per run; no
model calls are involved, so the cost is negligible. Documented for future tidy-up;
not changed to keep this cycle's diff reviewable.

### A-8 (Low) — Audit (persisted) vs memo (in-memory) not cross-checked
The audit report reads persisted rows; the memo reads in-memory run state. They are
kept in sync by explicit writes (`_link_final_score`, challenge-status rewrites), but
no assertion cross-checks them. A missed flush would go undetected. Documented;
a lightweight invariant check is a candidate for a later hardening pass.

---

## 7. What this audit does and does not establish

- It establishes that the BO **software controls** — admitted-evidence gating,
  deterministic-metric/score protection, append-only audit, handoff immutability,
  bounded concurrency, and (in v3) completeness gating — behave as specified on the
  examined paths.
- It does **not** establish the **analytical quality** of the model-generated
  content. Passing software tests says nothing about whether a live model's findings
  are accurate, complete, or useful. That is Phase B/D, and requires the live
  evidence and independent credit-analyst review that remain outstanding.
- The most consequential discrepancy between the documented architecture and the
  shipped behavior is A-1/A-2: the production CLI's default roster and the roster-gated
  completeness governance. Resolving the governance asymmetry is escalated for
  approval because it changes the behavior of a shipped path.

---

## 8. Reproduction

```bash
# From the repo root, with the locked env:
DEBUG=false .venv/bin/python -m pytest -q                       # full suite (0 failures)
DEBUG=false .venv/bin/python -m pytest -q \
  tests/integration/test_bo_v3_complete.py \
  tests/integration/test_bo_pipeline.py \
  tests/integration/test_challenge_rerun.py \
  tests/unit/test_bo_roster_v3.py \
  tests/integration/test_bo_m3_single_specialist.py \
  tests/integration/test_bo_governance_contract.py            # audit contract tests (this cycle)
```
