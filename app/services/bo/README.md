# Business Overview (BO) module — working guide

Read this before changing anything under `app/services/bo/`. It describes what
exists, how a run flows, where each rule lives, and the traps that cost time.

Status and history: [`BO_QUALITY_REMEDIATION_REPORT.md`](../../../BO_QUALITY_REMEDIATION_REPORT.md)
(latest) and [`BUSINESS_OVERVIEW_V2_REPORT.md`](../../../BUSINESS_OVERVIEW_V2_REPORT.md)
(original build). Spec: `.kiro/specs/credit-memo-business-overview-v2/`.

## What it is

Six narrow LLM specialists analyse one borrower's business from admitted filing
evidence. Deterministic code validates everything they return. Two entry points:

| Command | Runs | Produces |
|---|---|---|
| `run-bo-specialists` | BO1–BO6 + validators | executive summary, six-specialist comparison (HTML/PDF), validation audit, benchmark, JSON |
| `run-bo-case` | BO1–BO6, Business Orchestrator, Business Challenger, one bounded rerun | analyst memo, audit report, handoff (pending human review) |

Neither runs Financial, Structuring, mitigant or cross-topic agents.

| Code | Agent id | Remit |
|---|---|---|
| BO1 | `bo_business_model_revenue` | Business model, revenue quality |
| BO2 | `bo_customers_contracts` | Customers, payers, contracts, revenue realisation |
| BO3 | `bo_competition_pricing` | Competition, pricing power |
| BO4 | `bo_operations_management` | Operations, management, execution |
| BO5 | `bo_regulation_external` | Regulation, litigation, external risks |
| BO6 | `bo_resilience_interactions` | Interactions across validated BO1–BO5 findings |

## Run flow

```
CreditMemoPipeline.prepare_case      ingest, reconcile, snapshot, metrics (no LLM)
  -> build_indicators                deterministic numbers, grouped per specialist
  -> EvidenceRouter + retrieval plan passages per specialist (bounded)
  -> sector checklist / case checks  guaranteed passages + questions
BO1..BO5 (concurrent)
  -> retrieval follow-up             once, only if an agent asked for a search
  -> coverage follow-up              once, only for unanswered coverage questions
BO6                                  receives usable BO1..BO5 hypotheses only
[run-bo-case only] orchestrator -> challenger -> at most one rerun
reports
```

Each phase is one `DagExecutor.run(only=...)` call over `default_registry("v2")`.

## Module map

| File | Responsibility |
|---|---|
| `plans.py` | Roster data: owned parameters, retrieval plans, indicator groups, permitted values (`PARAMETER_VALUE_SETS`), values needing company-specific evidence (`REQUIRES_COMPANY_EVIDENCE`) |
| `sectors.py` | Sector profiles: coverage checklist, sector indicators, detection; loader for case coverage checks |
| `retrieval.py` | Plan queries, checklist queries, agent-requested follow-up, budgets, instruction-like flagging |
| `indicators.py` | Deterministic indicators and their `CalculationTrace`; per-group `agent_view` |
| `recovery.py` | One bounded attempt to recover a quote that failed exact matching |
| `inference_rules.py` | Layer C: lexical rules for known unsupported inferences |
| `hypotheses.py` | `HypothesisValidator` (all per-hypothesis checks), `gate_parameters`, `HypothesisStore` |
| `reconcile.py` | Cross-agent duplicates and contradictions (lexical) |
| `synthesis.py` | Rubric value sets, orchestrator/challenger input views and output validation |
| `runner.py` | `BusinessOverviewRunner` (`run_case`, `run_prepared`, `run_specialists`) and `_BoRun` phase logic |
| `reporting.py` | Memo, audit report, handoff for `run-bo-case` |
| `handoff.py` | Human accept/reject of the handoff; immutability guard |
| `demo.py`, `demo_report.py` | `run-bo-specialists`: payload, the three reports, PDF |
| `benchmark.py` | Baseline replay and old-versus-new comparison |
| `transcript.py` | `RecordingBackend` (records exactly what was sent and returned) |
| `offline.py` | `ScriptedBoBackend` with anchors, for tests and offline demos |
| `evaluation.py` | Evaluation against a reviewer manifest |
| `workbench.py` | Read-only views for `/bo` |

Prompts and response schemas: `app/prompts/bo_prompts.py`.
Contracts: `app/schemas/bo.py`. Tables: `hypothesis_records`,
`retrieval_attempts`, `accepted_business_overviews` in `app/models/orm.py`.

## How validation decides

Two kinds of outcome. Know which one you are adding.

**Whole specialist output rejected** (nothing promoted) — keep this list short:
schema-invalid response, a parameter the agent does not own, an evidence id that
is not admitted, a score/band/rating field, a malformed or duplicate hypothesis id.

**One hypothesis or parameter held back** (everything else survives):

| Check | Effect |
|---|---|
| Quote not verbatim → recovery (`layout_gap`, `relocated`) | recovered: source text replaces the quote; else quote discarded |
| `fact` / `management_claim` with no verified quote | unverified |
| Number not in a quote and not bound to an indicator | unverified |
| Indicator binding wrong (id, value, unit, period, denominator) | unverified, flag `numeric_binding_invalid` |
| Only belief/intent quotes behind a `fact` | reclassified `management_claim` |
| `supported` with counter-evidence cited | qualified |
| Only risk-factor ("could/may") quotes | qualified, flag `generic_risk_disclosure_only` |
| Quote shares almost no wording with a `fact` | qualified, flag `quote_does_not_cover_claim` |
| Layer C rule hit | `audit_only` or `qualify` per rule |
| Mitigant "demonstrated" without outcome evidence | set to unproven |
| BO6: no mechanism / invalid component | unverified |
| BO6: weaker components | capped certainty and materiality |
| Parameter value not permitted, unsupported, or needing company-specific evidence | recorded `unavailable` with a reason |

`admissibility` is what downstream may use: `established`, `attributed`
(management claim), `qualified_narrative`, `audit_only`. `HypothesisRecord.usable`
is `admissibility != audit_only`.

Layer C/D rules are **heuristic pattern checks**. They are recorded as
`review_flags` with `heuristic: true`. Do not describe them as proof of
correctness anywhere.

## How to extend

| To add | Change |
|---|---|
| An unsupported-inference rule | Append an `InferenceRule` to `RULES` in `inference_rules.py`; add a flagged case and a negated case to `tests/unit/test_bo_remediation.py`. `exempt` and `triggers` must be tuples: a missing trailing comma turns a one-item tuple into a string and silently disables the rule. |
| A permitted value / stricter parameter | `PARAMETER_VALUE_SETS`, `REQUIRES_COMPANY_EVIDENCE` in `plans.py` |
| A sector | New `SectorProfile` in `sectors.py`, register in `SECTOR_PROFILES`; make `detection_pattern` specific (a medical-device maker must not match a hospital profile) |
| A coverage question for one case | A JSON file like `examples/healthcare/hca-2025/coverage_checks.json`, passed with `--coverage`. Never put a borrower name in generic code; a test enforces this. |
| An indicator | `SectorIndicator` in the sector profile, or `_CONTEXT_METRICS` in `indicators.py`; assign a `group` and add the group to the specialists that need it in `plans.py` |
| Prompt wording | `bo_prompts.py`. Specialist text goes in `_AGENT_BRIEFS`; `SHARED_RULES` must stay free of parameter names and agent codes (tested). A prompt change creates a new prompt version automatically. |

Changing owned parameters, retrieval plans or indicator groups changes the
agent definition hash and the registry hash. That is intended.

## Commands

```powershell
# tests (no network, no model)
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest tests/unit/test_bo_remediation.py tests/integration/test_bo_specialists_demo.py -q

# specialists demo, replaying recorded responses (no model cost)
.venv\Scripts\python -m app.cli --database data/bo_live.db --output output/bo_live `
    run-bo-specialists HCA_2025_LIVE --package examples/healthcare/hca-2025/package.json `
    --coverage examples/healthcare/hca-2025/coverage_checks.json `
    --replay output/bo_live/bo/HCA_2025_LIVE/<run>/specialists_run.json

# specialists demo, live (provider cost)
...same command with  --live  instead of --replay, optionally
    --baseline <previous run>/specialists_calls.json   --price-in X --price-out Y

# full BO path (orchestrator, challenger, memo)
.venv\Scripts\python -m app.cli --database data/bo_demo.db --output output/bo_demo `
    run-bo-case <CASE> --package <package.json> [--responses script.json | --live]

# complete an existing governed specialists-only artifact without replaying specialists
.venv/bin/python -m scripts.complete_bo_recorded --source <specialists_run.json> \
    --mode offline --output-dir <output-directory>
.venv/bin/python -m scripts.complete_bo_recorded --source <specialists_run.json> \
    --mode replay --replay <prior-bo_completion_packet.json>
.venv/bin/python -m scripts.complete_bo_recorded --source <specialists_run.json> \
    --mode live --output-dir <output-directory>  # provider cost: synthesis + challenge
```

Live runs need a git-ignored `.env` in the repo root:

```
LLM_PROVIDER=vertex
VERTEX_API_KEY=...
VERTEX_MODEL_NARROW=gemini-3.1-flash-lite
VERTEX_MODEL_ORCHESTRATOR=gemini-3.1-flash-lite
VERTEX_MODEL_CHALLENGE=gemini-3.1-flash-lite
```

## Things that will trip you up

* **Evidence ids are case-specific hashes.** A recorded response only replays
  under the same case id and filing. `ScriptedBoBackend` scripts therefore refer
  to evidence by anchor phrase (`{"$evidence": "phrase"}`); `--replay` needs the
  same case id and database as the recorded run.
* **Indicator ids change every run.** `ReplayBackend` re-points them by
  `parameter_id`. Hand-written scripts use `{"$indicator": "<parameter_id>"}`.
* **Filing text contains layout artefacts** (page numbers and running headers
  inside sentences). That is why quote recovery exists; do not "fix" it by
  loosening exact matching.
* **Live output varies.** Four same-day HCA runs gave 14–24 usable hypotheses.
  Never conclude anything from one run; use `--baseline` and read the middle
  ("re-validated") column to separate validator effects from model effects.
* **Whole-output rejection is expensive.** Three of the first four live runs
  lost a specialist to an all-or-nothing rule. Prefer holding back one finding.
* **Tests must not read `.env`.** `tests/conftest.py` disables it. If a test
  needs settings, construct `Settings(...)` explicitly.
* **`data/`, `output/`, `.env` are git-ignored.** Demo databases
  (`data/bo_live.db`, `data/bo_demo.db`) and reports are local only.
* **The HCA filing is not committed** (`examples/healthcare/hca-2025/filing/`,
  about 10 MB). Fetch it with `sec-fetch`; see that folder's README. Tests that
  need it skip when it is absent.
* **`tmp_bo_specialists_table.py`** in the repo root is an untracked throwaway,
  superseded by `demo_report.write_comparison_pdf`.

## Known gaps (open work)

1. Entailment is not checked: a verbatim quote that shares wording with a claim
   is accepted as support. Needs the challenger, an LLM judge or a reviewer.
2. Indicator relevance is not checked (a correct number can be bound to a claim
   it does not bear on).
3. The orchestrator and challenger prompts predate `evidence_basis`,
   `review_flags` and `fo_handoffs`; these are not yet passed to them or shown
   in the `run-bo-case` memo.
4. FO handoffs are recorded on hypotheses; nothing consumes them.
5. One sector profile (healthcare provider). Retrieval is keyword-based.
6. `examples/healthcare/hca-2025/review_manifest.json` is an unverified template.
8. The full agentic runner (FO/SO/credit) still uses the v1 BO roster.

## M3 readiness services (offline)

The readiness cycle adds three versioned services without changing the default
runner mode:

* `semantic_entailment.py` is a fail-closed contract for an optional structured
  entailment judge. It receives only approved cited evidence. Failed numeric,
  entity, period, denominator or source checks stop evaluation before the judge.
* `hybrid.py` defines `single_pass`, `full_investigation` and
  `hybrid_selective` execution modes. The hybrid policy selects material adverse
  risks, asserted mitigants, inferential mechanisms, conflicts, weak provenance,
  coverage gaps and low-confidence material claims. Failed validation is a
  reason to investigate, not a reason to skip a claim.
* `ownership.py` screens the claim text against clear v3 remit boundaries.
  Out-of-scope claims remain in the investigation trace with the policy version,
  reason and suggested destination. The service never silently forwards them.

The three-mode executor is not yet wired into `BusinessOverviewRunner`; see
`BO_M3_READINESS_REPORT.md` for the gate decision and integration plan.
