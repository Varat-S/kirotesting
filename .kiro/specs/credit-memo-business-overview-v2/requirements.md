# Business Overview v2 — Requirements

Scope: one complete, independently runnable **Business Overview (BO)** module on
top of the existing agentic infrastructure. Financial Overview (FO), Structuring
Overview (SO) and the Credit Orchestrator are out of scope and must not run.

Audited baseline: branch `spec/agentic-analysis` at
`e2e49cdf388aff090acc9b101c3e04e46dc5f476` (identical to the reviewed commit;
881 tests passed, 1 skipped before any change).

## Baseline audit (verified against code)

| Handoff observation | Verified | Where |
|---|---|---|
| Registry declares 27 logical jobs | yes | `app/services/agents/registry.py::default_registry` |
| Six narrow agents assigned to BO | yes | `_BUSINESS_NARROW` |
| Business Orchestrator and Challenger registered | yes | `_orchestrator`, `_challenger` |
| Agentic runner uses a fallback Business conclusion | yes | `agentic_runner.py` builds a fixed "assessment from validated parameters" claim with `orchestrator_run_id="orch"` |
| Business Orchestrator / Challenger never execute | yes | `build()` returns `None` for `orchestrate` / `challenge` / `mitigant` task types unless it is the Financial Orchestrator with a benchmark context |
| Challenge loop, router, validators, promotion, scoring exist but are not all wired | yes | `ChallengeLoop`, `RerunController` and `EvidenceRouter` are only called from tests / the sector path |
| Deterministic metrics persisted but excluded from official scoring | yes | comment and code in `agentic_runner.py` |

Additional findings that affect BO:

1. **BusinessRiskScore is always `unavailable` today.** `business_weights` needs
   `competitive_position_risk_signal` (critical), `management_governance_risk_signal`
   and `customer_hhi`. Nothing in the runner calls the `RubricEngine` that derives
   the two risk signals, so the critical dimension is always missing.
2. **BO narrative routing never matches SEC evidence.** v1 BO selectors use
   topics such as `corporate_structure` / `competitive_position`; SEC passages are
   tagged with a different vocabulary (`customer_concentration`, `regulatory_exposure`,
   …). v1 BO agents therefore receive all raw facts and no routed narrative.
3. **`FakeLLMBackend` is keyed by `(task_type, key)`** and cannot return a
   different answer on a rerun; a sequenced scripted backend is needed for tests.
4. **Sector benchmarking maps risk dimensions to v1 BO agent ids**
   (`config/healthcare/medical_devices.json`). Renaming BO agents in place would
   break the Stryker extension.
5. **No live-provider or SEC credentials are configured in this workspace**
   (`SEC_USER_AGENT`, Vertex settings absent). Live Vertex runs cannot be
   performed here; SEC acquisition needs a contact string from the user.

## Requirements

### R1 Six-agent roster (versioned)
1.1 Exactly six BO narrow agents: BO1 Business Model & Revenue Quality, BO2
Customers, Contracts & Revenue Realisation, BO3 Competition & Pricing Power, BO4
Operations, Management & Execution, BO5 Regulation, Litigation & External Risks,
BO6 Business Resilience & Risk Interactions.
1.2 The v2 roster is a separate registry version (`agent-registry-2.0.0`) with
27 valid jobs. The v1 roster (`agent-registry-1.1.0`) is unchanged so historical
`AgentRun` rows, the legacy agentic runner and sector benchmarking keep working.
1.3 Semantic parameter ids keep their meaning; ownership moves explicitly; new
ids exist only for new semantics; no parameter has two owners; no agent owns a
deterministic parameter.
1.4 BO6 depends on BO1–BO5 in the DAG, produces no score and no final narrative.
1.5 A v1→v2 agent compatibility map is published for old results and for the
sector-dimension mapping.

### R2 Hypothesis contract
2.1 Every specialist emits hypothesis records distinguishing `fact`,
`calculated_fact`, `management_claim`, `inference` and `assumption`.
2.2 Dispositions: `supported`, `qualified`, `rejected`, `unverified`.
Presentation: `display`, `on_demand`, `suppress`.
2.3 Validity and credit direction are independent fields; neither is derived
from the other, nor from confidence.
2.4 A rejected hypothesis is retained and may link to an accepted correction.
2.5 A mitigating initiative counts as an effective mitigant only when its
measured outcome is evidenced; existence alone is `unproven`.
2.6 A finding may carry both favourable and adverse implications (`mixed`).

### R3 Evidence and retrieval
3.1 Retrieval runs only over material already admitted to the
`CanonicalEvidenceSnapshot`; nothing new enters the snapshot.
3.2 Each specialist has a declared, bounded retrieval plan covering support,
contradiction, mitigant and mitigant-challenge purposes; one bounded
agent-requested follow-up round is allowed.
3.3 Every attempt is recorded (terms, purpose, results, document/section/location,
evidence id). An empty result is a recorded retrieval gap.
3.4 Evidence budgets (passages, characters) are configurable.

### R4 Validation
4.1 Unknown evidence ids, unowned parameters, score-like fields and
schema-invalid output reject the whole agent output (no partial promotion).
4.2 `fact` and `management_claim` hypotheses must carry a verbatim quote that
exists in the cited admitted passage.
4.3 Numbers in a claim must be bound to an accepted deterministic result or
appear in a cited verbatim quote. Quoted calculations must match value, unit,
period and denominator.
4.4 A `fact` whose only support is management belief/intent language is
reclassified `management_claim`. Unsupported, unverified and rejected
hypotheses never become established findings.
4.5 Source text is data; instruction-like passages are flagged and never change
control flow.

### R5 Deterministic calculations
5.1 Indicators come only from `MetricEngine` / `ParameterEngine`. Each carries
numerator, denominator, units, period, source fact ids, formula id, definition
version and calculation state.
5.2 Operating cash flow less capital expenditure is labelled as such and never
as cash available for debt service. BO issues no debt-capacity conclusion.

### R6 Business Orchestrator
6.1 The registered `business_orchestrator` executes through the runtime with a
structured input of accepted parameters, validated hypotheses, contradictions,
duplicate groups, BO6 findings, deterministic context, limitations and the
BusinessRiskScore with status. No raw documents.
6.2 Output is a schema-valid `TopicConclusion` with claim-level references. The
orchestrator assigns no score.
6.3 An invalid orchestrator answer produces no accepted conclusion.

### R7 Business Challenger and rerun
7.1 The registered `business_challenge` executes after the orchestrator; a
no-finding result is a recorded, validated execution.
7.2 Outcomes: accepted, accepted subject to qualification, targeted rerun
required, mandatory human review.
7.3 At most one automatic remediation round; rerun scope is the named agents
plus DAG descendants; superseded artifacts are preserved.
7.4 Unresolved material findings stay visible and block an unqualified
conclusion.

### R8 Scoring
8.1 The existing `ScoringEngine` and rubric configuration are used unchanged.
Status semantics `final` / `provisional` / `unavailable` / `not_applicable` are
preserved; no number is fabricated.

### R9 BO-only execution
9.1 A dedicated entry point runs BO without FO, SO, mitigant or cross-topic
agents and without constructing empty FO/SO results.
9.2 It reuses `DagExecutor`, `AgentRuntime`, `LLMClient`, prompt registry, cache
identity, audit and persistence.

### R10 Outputs
10.1 Analyst memo (JSON source of truth, HTML rendering, optional PDF).
10.2 Explainability/audit report from recorded events only; unexecuted steps
read `not_executed` / `not_performed`; fake-backend runs are labelled offline
simulation.
10.3 `AcceptedBusinessOverview` handoff, immutable once accepted, acceptance
only by an explicit human action.
10.4 A BO workbench view over the same artifacts.

### R11 HCA demonstration and evaluation
11.1 The real HCA FY2025 Form 10-K is ingested through the existing SEC path.
11.2 A reviewer-controlled manifest separates source-confirmed facts,
recomputed values, acceptable inferences, known false hypotheses, unresolved
questions and expected coverage. Tests check concepts and source grounding, not
prose similarity with the groupmate's document.
11.3 Offline scripted runs and live-model runs are reported separately.

### R12 Regression
12.1 All existing tests keep passing. The private benchmark workbook is not a
requirement for BO tests.
