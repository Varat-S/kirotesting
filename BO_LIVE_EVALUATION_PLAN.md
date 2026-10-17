# BO Live Evaluation Plan & Preflight

**Phase:** C (prepare live end-to-end BO execution)
**Date:** 18 October 2026
**Branch:** `feat/bo-validation-release`
**Status:** Preflight COMPLETE (offline). **No paid model call has been made or is
authorized.** This plan stops at the budget proposal and awaits explicit approval.

---

## 1. Preflight — HCA Healthcare FY2025 (offline, no model)

A full v3 pipeline run was executed with an empty-but-valid scripted backend so the
exact request payloads a live run would send are produced and measured, without any
paid call. Harness: `output/bo_preflight/preflight.py` (git-ignored scratch);
measurements below are reproducible.

### 1.1 Filing and indicators available

| Check | Result |
|---|---|
| Primary 10-K present | **Yes** — `examples/healthcare/hca-2025/filing/0001193125-26-044769/hca-20251231.htm` (5.5 MB) + 15 companion files |
| Primary document SHA-256 | `2043cd0346a1ca36748348b6bef4bdf02385ee772eb241794fcffc98c8c6ba8c` |
| Accession | `0001193125-26-044769`; form 10-K; report date 2025-12-31 |
| Package | `examples/healthcare/hca-2025/package.json`; as-of 2025-12-31; cutoff 2026-03-31 |
| Deterministic indicators | Computed by the shared pipeline during `run_case`; routed to specialists |
| Evidence packets sealed | **9** (one per executable v3 agent) |

### 1.2 Routing and agent execution (offline)

All eight primary agents received their sealed packets and **validated** on the
empty-response run; the governance gates behaved exactly as the audit predicts:

| Agent | Offline status | Note |
|---|---|---|
| M1S1–M1S5 (5 specialists) | validated | each routed its packet |
| M2, M3, M4 | validated | four-module roster active (`agent-registry-3.0.0`) |
| M1 Agent 6 (`m1_resilience_integration`) | **skipped** | completeness gate blocked it (no usable findings from empty responses) — correct |
| Business Orchestrator | failed | empty answer → no validated conclusion |
| Business Challenger | skipped | no conclusion to challenge |
| `conclusion_status` | `human_review_required` | no-conclusion path, as designed |

This confirms (a) the filing ingests, (b) evidence routes to all eight primaries,
(c) the v3 completeness governance fires. With *real* model content, Agent 6, the
orchestrator and the challenger would execute rather than skip.

### 1.3 Measured request sizes (the core budget input)

Characters of the JSON-serialised request inputs, with an approximate
input-token estimate at ~4 chars/token. The empty-response run triggered the one
bounded coverage/retrieval follow-up for most specialists (hence 2 calls each); a
live run may fire fewer or more follow-ups.

| Method | Calls (offline) | Total req chars | ≈ input tokens |
|---|---:|---:|---:|
| m1_customer_stickiness | 2 | 102,926 | 25,731 |
| m1_pricing_power | 2 | 94,304 | 23,576 |
| m2_industry | 2 | 90,818 | 22,704 |
| m1_revenue_quality | 2 | 88,018 | 22,004 |
| m1_operating_model | 2 | 85,246 | 21,311 |
| m1_competitive_durability | 2 | 75,914 | 18,978 |
| m3_corporate_management | 2 | 73,861 | 18,465 |
| m4_strategy_capital | 1 | 34,206 | 8,551 |
| orchestrate | 1 | 15,771 | 3,942 |
| **Total** | **16** | **661,064** | **≈ 165,266** |

(The challenger did not run offline because there was no conclusion; a live run adds
one challenger call and, if a material finding is raised, one bounded rerun round.)

### 1.4 Configuration, schema and control checks

| Preflight item | Result |
|---|---|
| Model/provider configuration | Vertex (`LLM_PROVIDER=vertex`) via `_live_backend`; credentials from ADC or `VERTEX_API_KEY` in a git-ignored `.env`. **Not configured in this sandbox** — a live run requires the operator to set it locally. |
| Prompt versions & output schemas | Versioned prompts (`bo_prompts.py`); JSON schemas `BO_NARROW_JSON_SCHEMA`, `BO_ORCHESTRATOR_JSON_SCHEMA`, `BO_CHALLENGER_JSON_SCHEMA` enforced by the client. Recorded per run. |
| Context / output-token limits | `LLM_MAX_INPUT_BYTES` (default 1.5 MB) and `LLM_MAX_OUTPUT_TOKENS` (default 12,000) enforced; largest single request ≈ 52 KB (well under the input cap). Reasoning-model output cap is raised to 32 K in the M2 live tool. |
| Rate-limit / timeout / retry / concurrency | `AGENT_TIMEOUT_SECONDS` (120), `AGENT_MAX_CONCURRENCY` (6), one bounded re-plan/re-ask; `asyncio.Semaphore` + `wait_for` enforce both (audited in `executor.py`). |
| Run artifacts persisted | `business_overview.json`, `audit_report.json`, `handoff.json`, HTML reports written per run; append-only DB rows. |
| Partial / failed runs reviewable | Yes — a failed orchestrator still persists specialist outputs, packets, audit and a `human_review_required`/`unavailable` status (demonstrated by the preflight). |

---

## 2. Proposed cost authorization (CAPPED — awaiting approval)

Grounded in the **real paid anchors** recorded in `BO_M2_COMPARISON_REPORT.md` §5b–5c
(same borrower, same filing, HCA M1S2 on Vertex):

- single-pass specialist, `gemini-3.1-flash-lite`: ≈ **$0.007** / specialist, ≈ 6 s;
- a full Module-1 (five specialists) flash-lite run was estimated at ≈ **$2**;
- a stronger testing tier (`gemini-3.1-pro-preview`) cost ≈ $0.36 / specialist.

### 2.1 Budget per complete single-pass v3 run (flash-lite, all tiers)

| Stage | Model calls | Basis |
|---|---:|---|
| 5 × M1 specialists | 5–10 | one call each + ≤1 bounded follow-up |
| M1 Agent 6 | 1 | runs when M1 complete |
| M2, M3, M4 | 3–6 | one call each + ≤1 follow-up |
| Orchestrator | 1 | cross-module synthesis |
| Challenger | 1 | independent challenge |
| Bounded rerun (contingent) | 0 or +≈11 | at most one round if a material finding is raised |
| **Per run** | **≈ 11–20 (≤ ~31 with a rerun)** | |

| Line | Value |
|---|---|
| Model | `gemini-3.1-flash-lite` (narrow, orchestrator, challenge tiers) |
| Est. input tokens / run | ≈ 165 K (measured) + challenger ≈ 10 K ≈ **175 K** |
| Est. output tokens / run | ≈ 30–50 K (M2 live observed 6.5–28 K per specialist-stage) |
| **Est. cost / complete run** | **≈ $0.05–$0.12** (flash-lite), extrapolated from the $0.007/specialist anchor |
| Proposed runs | **3 per configuration** (output varies; median reported) |
| Configurations | A (single-pass, production candidate) — primary. B/C experimental only if separately justified. |
| **Total maximum expenditure (hard cap)** | **$5.00** for Configuration A × 3 runs, including rerun contingency and a safety margin |

### 2.2 Optional stronger-tier sensitivity (separate, explicit opt-in)

A single additional run with the testing stage on `gemini-3.1-pro-preview` to probe
whether a stronger model preserves material findings: ≈ **$2** for one full run.
**Not included** in the $5 cap; request as a distinct line if desired.

### 2.3 Stop conditions and overrun safeguards

- Hard stop at the **$5.00** cap; abort before any call that would exceed it.
- Stop on repeated provider errors (≥3 consecutive) or any input exceeding the byte cap.
- Stop after 3 runs of Configuration A regardless of outcome.
- Each run writes its cost/token transcript before the next begins; the running
  total is checked against the cap between runs.
- No configuration beyond A is executed without a separate approved line item.

**No paid call proceeds until the user approves this budget.** Prior authorization
from earlier development sessions (Claude Code / Codex) is NOT carried over.

---

## 3. Controlled live configurations

| Config | Scope | Status |
|---|---|---|
| **A — Single-pass BO** | All four modules, five M1 specialists + Agent 6, cross-module synthesis, challenge, scoring, reporting | **Production candidate — primary.** Fully supported by the runner (v3). |
| B — Single-pass + targeted investigation | Only if the implemented runner supports it end-to-end | Experimental; the three-mode executor is **not wired into the default runner** (audit §A4). Do **not** run as if production. |
| C — Experimental hybrid | M1S2-only `run_m1s2_mode` | Experimental, test-only entry point; single-specialist. Do **not** expand hybrid to all five specialists. |

Same evidence packet, cutoff and approved configuration across any compared arms;
input fingerprint recorded.

### Per-run capture (required for Phase D)

Every live run must record: each model invocation and response, input/output (and
reasoning, if available) tokens, provider cost, stage latency, validation results,
completeness states, challenge interventions, the final structured BO record, the
HTML report, audit references, and any failure/retry history. Where a datum is
unavailable, report `not_measured` — never an invented estimate.

---

## 4. Reproduction (offline preflight)

```bash
DEBUG=false .venv/bin/python output/bo_preflight/preflight.py   # measurements above
# Equivalent CLI shape for a LIVE run (requires local Vertex .env; DO NOT run unapproved):
# DEBUG=false .venv/bin/python -m app.cli --database data/bo_live.db --output output/bo_live \
#   run-bo-case HCA_2025_LIVE --package examples/healthcare/hca-2025/package.json \
#   --roster v3 --live
```

---

## 5. Outstanding before any live run

1. **User approval of the §2 budget** (hard cap $5.00 for Config A × 3).
2. Operator-configured Vertex credentials in a local git-ignored `.env`
   (absent in this sandbox).
3. The frozen HCA answer key (Phase B) completed and signed off, so live output can
   be scored rather than merely produced.
