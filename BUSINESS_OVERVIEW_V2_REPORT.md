# Business Overview v2 — Implementation Report

Hybrid six-agent Business Overview (BO) module, runnable on its own.
Specs: `.kiro/specs/credit-memo-business-overview-v2/`.

## Status in one paragraph

The BO-only pipeline is implemented and tested offline, and it has been run end
to end on a **real SEC filing (Stryker FY2024 10-K)** with scripted responses.
It has **not** been run on HCA and **not** with a live model:

| Stage | Status |
|---|---|
| BO-only infrastructure (six agents, orchestrator, challenger, rerun, memo, audit, handoff, workbench) | Done, automated tests pass |
| Real-filing run, secondary borrower (Stryker FY2024) | Done — **offline simulation** (scripted responses, no model) |
| Real HCA FY2025 filing ingested | **Not done.** The download from SEC EDGAR was declined in this session; nothing HCA-specific has been processed or verified |
| HCA acceptance set (section H) | **Not passed** — tests exist and are skipped until the filing is present |
| Live-model (Vertex) BO run | **Not performed.** No provider credentials are configured in this workspace |
| Analytical quality of a model | **Unverified.** Scripted responses prove pipeline behaviour only |

By the handoff's own definition of done (items 9 and 15), the BO module is
therefore **not complete**: items 1–8, 10–14 and 16 are met; item 9 (real HCA
filing) is open.

## 1. Branch and commit

Branch `spec/agentic-analysis`. Audited base `e2e49cdf388aff090acc9b101c3e04e46dc5f476`
(identical to the reviewed commit). The BO v2 commit is the one that adds this
file; see `git log -1`.

## 2. Architecture before / after

| | Before | After |
|---|---|---|
| BO roster | 6 narrow agents, v1 (`agent-registry-1.1.0`) | v1 unchanged **plus** v2 roster (`agent-registry-2.0.0`, `default_registry("v2")`); both have 27 jobs |
| BO6 | n/a | depends on BO1–BO5, consumes only their validated hypotheses |
| Business Orchestrator | registered, never executed; fixed fallback sentence with `orchestrator_run_id="orch"` | executed through `DagExecutor`; conclusion references its real `AgentRun` |
| Business Challenger | registered, never executed | executed; findings drive `ChallengeLoop` |
| Rerun | services existed, unwired | one bounded targeted round, append-only supersession |
| BusinessRiskScore | always `unavailable` (rubric never called) | rubric signals derived by the existing `RubricEngine`; status per existing coverage rules |
| BO evidence routing | topic labels that never matched SEC passages | declared retrieval plan over admitted passages, with recorded attempts and gaps |
| Hypotheses | none | typed, validated, persisted `HypothesisRecord`s |
| Outputs | combined draft memo | BO memo, audit report, handoff, workbench view |

Shared infrastructure reused: `CreditMemoPipeline.prepare_case`, `EvidenceRouter`,
`DagExecutor`, `AgentRuntime`, `LLMClient`, prompt registry, cache identity,
`DeterministicValidator`, `ParameterPromoter`, `MetricEngine`, `ParameterEngine`,
`RubricEngine`, `ScoringEngine`, `ConclusionStore`, `ChallengeLoop`,
`RerunController`, `EscalationEngine`, `HumanReviewWorkflow`, `StageRecorder`.
The legacy full agentic runner still uses the v1 roster and is untouched.

New code: `app/services/bo/` (plans, retrieval, indicators, hypotheses,
reconcile, synthesis, runner, reporting, handoff, offline, evaluation,
workbench), `app/schemas/bo.py`, `app/prompts/bo_prompts.py`, two report
templates, one workbench template. Three additive tables: `hypothesis_records`,
`retrieval_attempts`, `accepted_business_overviews`.

## 3. Six agents and owned parameters

| Code | Agent id | Owned semantic parameters |
|---|---|---|
| BO1 | `bo_business_model_revenue` | `business_model_classification`, `revenue_recurrence_assessment`, `demand_defensiveness_assessment`*, `cyclicality_seasonality_assessment`* |
| BO2 | `bo_customers_contracts` | `customer_dependence_assessment`, `supplier_dependence_assessment`, `contract_termination_risk_assessment`, `revenue_realisation_assessment`* |
| BO3 | `bo_competition_pricing` | `competitive_position_assessment`, `pricing_power_assessment`, `switching_cost_assessment`, `competitive_pressure_assessment`*, `barriers_to_entry_assessment`* |
| BO4 | `bo_operations_management` | `management_quality_assessment`, `key_person_dependency_assessment`, `governance_concern_assessment`, `acquisition_strategy_assessment`, `integration_risk_assessment`, `capex_strategy_assessment`, `workforce_cost_resilience_assessment`* |
| BO5 | `bo_regulation_external` | `regulatory_dependency_assessment`, `litigation_risk_assessment`, `material_event_assessment`, `external_event_exposure_assessment`* |
| BO6 | `bo_resilience_interactions` | `compound_risk_assessment`*, `mitigating_capability_assessment`* |

\* new id. All 17 v1 parameter ids are preserved with unchanged meaning; the
old→new ownership table is in the design spec. No agent owns a deterministic
parameter; no parameter has two owners.

## 4. BO-only execution graph

```
prepare_case (no LLM): ingest -> reconcile -> snapshot -> metrics
indicators (MetricEngine + ParameterEngine) -> routed, budgeted packets
wave 0   BO1 BO2 BO3 BO4 BO5            concurrent
         [optional: one follow-up retrieval pass for agents that asked]
wave 1   BO6                             validated primary hypotheses only
         rubric signals -> BusinessRiskScore -> reconciliation (deterministic)
wave 2   business_orchestrator -> output validation -> TopicConclusion
wave 3   business_challenge -> ChallengeLoop
         [at most one targeted rerun: named agents + DAG descendants]
memo + audit + handoff (human review pending)
```

## 5. Number of AI calls

Eight per clean run (6 specialists + orchestrator + challenger). The recorded
Stryker demo made **12**: 8, plus BO3, BO6, orchestrator and challenger again
in the one remediation round. All 12 were scripted (offline simulation); none
reached a model.

## 6. Output artifacts

Demo outputs (git-ignored): `output/bo_demo/bo/SYK_FY2024_BO/<analysis_run_id>/`

* `business_overview.html` / `.json` — analyst memo
* `audit_report.html` / `.json` — explainability and audit report
* `handoff.json` — `AcceptedBusinessOverview`, `human_review_status: pending`
* `evaluation.json` — only when `--manifest` is supplied

Demo database: `data/bo_demo.db`.

## 7. Source documents and hashes

**HCA: none.** No HCA document has been acquired, so there is no accession,
filing date or hash to report.

Stryker demo (real filing, fetched by the user earlier into `data/sec/SYK/`):

| Field | Value |
|---|---|
| Company / CIK | Stryker Corporation / 310764 |
| Accession | 0000310764-25-000023 (Form 10-K, filed 2025-02-12, period 2024-12-31) |
| Primary document | `syk-20241231.htm`, SHA-256 `2c9ddc9a72f5ea55adbb9d75e2eb73372754e4503feb223e1bc85a719ab4ab24` |
| Source URL | https://www.sec.gov/Archives/edgar/data/310764/000031076425000023/syk-20241231.htm |
| Evidence cut-off | 2025-03-01T00:00:00Z; 20 files admitted, 0 rejected |
| Snapshot | v1, 1,731 facts, 123 narrative passages, content hash `3fa9b82a…be684` |

Per-file hashes for all 20 files are in section 1 of the audit report.

## 8. Ground-truth manifest

* `examples/healthcare/hca-2025/review_manifest.json` — **template,
  `unverified_pending_filing`**. It names the concepts and the six interpretive
  traps to check and contains no HCA figures. It is not ground truth until a
  reviewer verifies it against the processed filing.
* The synthetic manifest in `tests/bo_helpers.py` is verified against the
  fictional fixture and drives the evaluation tests.
* No manifest exists for Stryker; none was needed for a pipeline demonstration.

## 9. Finding statistics (Stryker demo, current versions)

| Agent | Supported | Qualified | Rejected | Unverified |
|---|---|---|---|---|
| BO1 | 5 | 0 | 1 | 0 |
| BO2 | 3 | 0 | 0 | 1 |
| BO3 | 3 | 1 | 1 | 0 |
| BO4 | 4 | 0 | 0 | 0 |
| BO5 | 3 | 0 | 1 | 0 |
| BO6 | 0 | 2 | 0 | 0 |
| **All** | **18** | **3** | **3** | **1** |

32 hypothesis records are stored including superseded versions. These counts
describe the script, not a model.

## 10. Challenger results (Stryker demo)

| Round | Agent run | Outcome | Findings |
|---|---|---|---|
| 0 | executed, validated | `rerun_required` | 1 material — `overstated_pricing_or_resilience` on the patent claim |
| 1 | executed, validated | `accepted_with_qualification` | 1 medium — management signal rests on one rubric dimension |

Rerun scope executed: `bo_competition_pricing`, `bo_resilience_interactions`,
`business_orchestrator`, `business_challenge`. Eight non-BO descendants were
invalidated by the DAG and recorded as deferred. Evidence that both agents ran:
`agent_runs` rows for `business_orchestrator` (2) and `business_challenge` (2)
in `data/bo_demo.db`, shown in audit sections 5, 15 and 16; the accepted
`topic_conclusions` row carries the orchestrator's real run id and supersedes
the first conclusion.

## 11. Evidence accuracy and coverage

* Stryker demo: every quote behind an accepted finding is re-verified verbatim
  against the admitted passage (asserted by
  `test_stryker_real_filing_runs_the_full_bo_path_offline`); every number in the
  memo is either inside such a quote or bound to a deterministic calculation;
  31 retrieval attempts recorded, 2 gaps (one plan query, one agent-requested
  search for customer concentration).
* BusinessRiskScore: `provisional`, band 3, coverage 0.7; `customer_hhi` missing
  and stated as such.
* HCA: no result.
* No precision/recall or accuracy figure is claimed for any model.

## 12–13. Tests and regression

Baseline before changes: 881 passed, 1 skipped.
After: **969 passed, 3 skipped, 0 failed** (90 new BO tests: 88 passed, 2 skipped).

New BO tests by acceptance area: A registry (10), C/D/E validation, hypothesis
logic, reconciliation, retrieval, indicators (29), B/C/F/G/I pipeline (36),
CLI/workbench/migration (5), evaluation (6), real filing (4: Stryker runs when
its bundle is present; two HCA tests skipped).

Regression suites all pass unchanged: Delta, synthetic case, NVIDIA SEC,
Stryker/medical-device benchmarking, parameter engine, scoring, legacy analysis
mode, healthcare workbook importer, final-snapshot compatibility, leakage and
immutability. No BO test needs the private benchmark workbook.

Skips: one pre-existing (no PDF backend), two HCA (filing absent). In CI the
Stryker real-filing test also skips because `data/` is git-ignored.

## 14. Token use and runtime

No live-provider run was performed, so there is no token or cost figure.
Offline demo runtime: about 5 s for the analysis phases. The audit report
records per-agent usage when a provider reports it.

## 15. Limitations and unverified behaviour

1. **No HCA result of any kind.** Primary demonstration not performed.
2. **No live-model run.** Prompts and schemas have never been exercised against
   a model in this module; schema strictness under Vertex is untested for the
   three new schemas.
3. Scripted responses were written by reading the filing; they show what the
   pipeline does with good and bad answers, not what a model would answer.
4. Retrieval is deterministic keyword matching over 2,000-character passages
   produced by the existing SEC narrative extractor. It has no semantic ranking,
   can split a sentence across passages, and only sees passages that extractor
   kept.
5. `customer_hhi` is never computed in the BO path (no customer-level revenue
   facts), so the score is at best `provisional`.
6. A rubric signal is produced from whichever dimensions are available; in the
   demo the management signal rests on one dimension. This is existing rubric
   behaviour and is surfaced by the challenger script, not fixed.
7. Duplicate and contradiction detection is lexical (token overlap, shared
   evidence).
8. Text-disclosed operating figures are admissible only as verbatim quotes;
   nothing is calculated from them.
9. The belief-language attribution rule is a fixed English pattern list.
10. The full agentic runner (FO/SO/credit) still uses the v1 BO roster.
11. No PDF backend is installed here; PDF export is untested in this workspace.

## 16. Remaining FO / SO / Credit Orchestrator integration

* Switch the full agentic runner to the v2 roster and map sector risk
  dimensions through `BO_V1_TO_V2_AGENT`.
* SO: consume `AcceptedBusinessOverview.operational_vulnerabilities`,
  `material_risk_ids` and `unproven_mitigants` when proposing mitigants.
* Credit Orchestrator: consume the accepted handoff only
  (`human_review_status == "accepted"`), and re-run the deferred descendants a
  BO rerun invalidates.
* FO: take the shared deterministic indicators from the common calculation
  layer; BO already treats them as context.

## 17. Reproduce

```powershell
# tests
.venv\Scripts\python -m pytest -q

# real-filing demo (offline simulation); needs the Stryker bundle under data/sec
.venv\Scripts\python -m app.cli --database data/bo_demo.db --output output/bo_demo `
    run-bo-case SYK_FY2024_BO `
    --bundle data/sec/SYK/0000310764-25-000023/bundle.json `
    --cutoff 2025-03-01T00:00:00Z --entity-id SYK --entity-name "Stryker Corporation" `
    --responses examples/healthcare/stryker-bo/offline_responses.json

# inspect in the browser
$env:DATABASE_URL = "sqlite:///./data/bo_demo.db"
.venv\Scripts\python -m uvicorn app.main:app
#   http://127.0.0.1:8000/bo

# record a reviewer decision (explicit; never automatic)
.venv\Scripts\python -m app.cli --database data/bo_demo.db bo-review <handoff_id> `
    --reviewer "<name>" --reason "<reason>" --acknowledge-unresolved
```

HCA: follow `examples/healthcare/hca-2025/README.md` (fetch, verify the
manifest, then run with or without `--live`).
