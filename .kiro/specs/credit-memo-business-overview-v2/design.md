# Business Overview v2 — Design

## 1. Design conflicts identified before implementation

| # | Conflict | Decision |
|---|---|---|
| C1 | "Replace the BO allocation" vs. "preserve historical agent definitions" and "Stryker benchmarking must remain intact" (its config names v1 agent ids). | Versioned roster. `default_registry()` stays `agent-registry-1.1.0`. `default_registry(bo_roster="v2")` builds `agent-registry-2.0.0` with the six new BO agents and the same 21 other jobs. The BO-only runner uses v2. Moving the full agentic runner to v2 is an FO/SO integration task. |
| C2 | The registered orchestrator/challenger must execute, but their v1 prompts know nothing of hypotheses. A prompt name is append-only versioned, so two templates under one name would flip versions between runs. | Agent ids stay `business_orchestrator` / `business_challenge`. In v2 they point to prompt names `bo_v2_business_orchestrator` / `bo_v2_business_challenge`; their definition hashes differ from v1. |
| C3 | BusinessRiskScore needs rubric-derived signals nobody computes. | The BO runner calls the existing `RubricEngine` on accepted BO3/BO4 categorical parameters. Config, weights and thresholds are untouched. `customer_hhi` is computed only when customer-level revenue facts exist; otherwise it is reported missing and the score is `provisional` or `unavailable` per the existing coverage rules. |
| C4 | Agents "retrieve evidence" but must not receive the full 10-K nor loop. | Deterministic retrieval over admitted narrative passages, driven by a declared per-agent plan, plus one bounded agent-requested follow-up. |
| C5 | A single failed specialist would skip every dependent in one `DagExecutor.run`. | The runner calls `DagExecutor.run(only=…)` once per phase. A failed specialist is recorded and passed to the orchestrator as a coverage limitation; the orchestrator still executes. |
| C6 | `ConclusionClaim` cannot reference hypotheses or memo sections. | Additive optional fields `hypothesis_ids`, `section`, `claim_type`. Existing payloads validate unchanged. |
| C7 | Text-disclosed operating figures (state revenue share, equivalent admissions) are not XBRL facts. | They are *disclosed figures*: admissible only with a verbatim quote containing the number. Nothing is calculated from them. |

## 2. Roster and parameter ownership

| Code | v2 agent id | Owned semantic parameters |
|---|---|---|
| BO1 | `bo_business_model_revenue` | `business_model_classification`, `revenue_recurrence_assessment`, `demand_defensiveness_assessment`*, `cyclicality_seasonality_assessment`* |
| BO2 | `bo_customers_contracts` | `customer_dependence_assessment`, `supplier_dependence_assessment`, `contract_termination_risk_assessment`, `revenue_realisation_assessment`* |
| BO3 | `bo_competition_pricing` | `competitive_position_assessment`, `pricing_power_assessment`, `switching_cost_assessment`, `competitive_pressure_assessment`*, `barriers_to_entry_assessment`* |
| BO4 | `bo_operations_management` | `management_quality_assessment`, `key_person_dependency_assessment`, `governance_concern_assessment`, `acquisition_strategy_assessment`, `integration_risk_assessment`, `capex_strategy_assessment`, `workforce_cost_resilience_assessment`* |
| BO5 | `bo_regulation_external` | `regulatory_dependency_assessment`, `litigation_risk_assessment`, `material_event_assessment`, `external_event_exposure_assessment`* |
| BO6 | `bo_resilience_interactions` | `compound_risk_assessment`*, `mitigating_capability_assessment`* |

\* new id (new semantics). All other ids keep their v1 definition.

### Old → new ownership

| v1 agent | v1 parameter | v2 owner |
|---|---|---|
| `business_model` | `business_model_classification` | BO1 |
| `business_model` | `revenue_recurrence_assessment` | BO1 |
| `competition_pricing` | `competitive_position_assessment` | BO3 |
| `competition_pricing` | `pricing_power_assessment` | BO3 |
| `competition_pricing` | `switching_cost_assessment` | BO3 |
| `customer_supplier_contract` | `customer_dependence_assessment` | BO2 |
| `customer_supplier_contract` | `supplier_dependence_assessment` | BO2 |
| `customer_supplier_contract` | `contract_termination_risk_assessment` | BO2 |
| `management_governance` | `management_quality_assessment` | BO4 |
| `management_governance` | `key_person_dependency_assessment` | BO4 |
| `management_governance` | `governance_concern_assessment` | BO4 |
| `ma_capex_execution` | `acquisition_strategy_assessment` | BO4 |
| `ma_capex_execution` | `integration_risk_assessment` | BO4 |
| `ma_capex_execution` | `capex_strategy_assessment` | BO4 |
| `regulatory_material_events` | `regulatory_dependency_assessment` | BO5 |
| `regulatory_material_events` | `litigation_risk_assessment` | BO5 |
| `regulatory_material_events` | `material_event_assessment` | BO5 |

v1 prompt id = response schema ref = agent id; v1 routing selectors are
`narrative:<topic>` labels. No v1 parameter feeds scoring directly; scoring
consumes `customer_hhi` and two rubric signals. `BO_V1_TO_V2_AGENT` publishes the
agent-level mapping. BO4 is broad by design: its prompt is split into task
sections (management & governance, workforce & cost, procurement, capital
deployment / M&A / capex) and its retrieval plan is selective per section.

## 3. DAG (BO slice of the v2 registry)

```
wave 0  BO1  BO2  BO3  BO4  BO5        (concurrent, bounded)
wave 1  BO6  (depends on BO1..BO5)
wave 2  business_orchestrator (depends on BO1..BO6)
wave 3  business_challenge    (depends on business_orchestrator)
```

Mitigant, structuring, financial and cross-topic jobs remain registered and are
never selected by the BO runner (`only=` is always a subset of the eight BO jobs).

## 4. Contracts (`app/schemas/bo.py`)

* `HypothesisRecord` — id, run, agent, agent run, claim, `claim_type`,
  `direction`, supporting / contradicting evidence ids, `evidence_quotes`,
  `parameter_result_ids`, `quoted_values`, `disposition`, `admissibility`
  (derived), `confidence`, `materiality`, `credit_mechanism`,
  `missing_information`, `display_priority`, `review_required`,
  `corrects_hypothesis_id`, optional `mitigant` block
  (`capability`, `existence`, `effectiveness`, `outcome_evidence_ids`),
  `validation_notes`, lineage (`supersedes_id`, `acceptance_state`).
* `RetrievalAttempt` — query id, purpose, terms, origin (`plan` | `agent_request`),
  status (`performed` | `no_results`), result references.
* `CalculationTrace` — the indicator metadata of R5.1 bound to a
  `parameter_result_id`.
* `AcceptedBusinessOverview` — the downstream handoff of R10.3.

Existing fields are reused rather than duplicated: `Materiality`,
`AcceptanceState`, `Topic`, `ParameterResult`, `ConclusionClaim`,
`ChallengeFinding`, `RiskScore`.

Admissibility is derived deterministically:

| claim_type | disposition | admissibility |
|---|---|---|
| fact / calculated_fact | supported | `established` |
| management_claim | supported | `attributed` (must be presented as a management claim) |
| inference | supported / qualified | `qualified_narrative` |
| any | qualified | `qualified_narrative` |
| assumption | any | `audit_only` |
| any | unverified / rejected | `audit_only` |

## 5. Persistence (additive tables)

`hypothesis_records`, `retrieval_attempts`, `accepted_business_overviews`.
Calculation traces, reconciliation output, memo JSON and audit JSON are recorded
through the existing hash-chained `StageRecorder`. `AgentRun`, `ParameterResult`,
`RiskScore`, `TopicConclusion`, `ChallengeFinding`, `EvidencePacket` tables are
reused as they are. `app/migrate.py` reports the new tables.

## 6. Runner (`app/services/bo/runner.py`)

Phase 1 `CreditMemoPipeline.prepare_case` (no LLM) → snapshot + metrics.
Phase 2 BO1–BO5, then optional follow-up retrieval pass.
Phase 3 BO6 over validated BO1–BO5 output.
Phase 4 rubric signals → `ScoringEngine.score_business` → orchestrator → validation.
Phase 5 challenger → `ChallengeLoop` → at most one targeted rerun → challenger again.
Phase 6 memo, audit, handoff (pending human acceptance).

Cache identity: packet hash, prompt hash, definition hash, router version and
model id already compose the key; BO prompts and schemas are new prompt names,
so no v1 cache entry can be reused by a v2 agent.

## 7. Validation layers

1. Runtime schema validation (`LLMClient`).
2. `DeterministicValidator.for_agent` — ownership, evidence existence, citations.
3. `HypothesisValidator` — quotes, number binding, calculation metadata,
   attribution, mitigant effectiveness, score-key ban, admissibility.
4. Cross-agent reconciliation — duplicate groups, contradictions.
5. Orchestrator output validation — schema, evidence, quote-not-calculate,
   hypothesis admissibility, claim-specific references, no double counting,
   no score, provenance.
6. Challenger output validation — target claims exist, evidence admitted,
   affected agents are BO agents.

## 8. Outputs

`output/bo/<case>/<analysis_run_id>/` holds `business_overview.json|html[|pdf]`,
`audit_report.json|html`, `handoff.json`. HTML is rendered only from the JSON.

## 9. Security and provenance

Evidence is passed under a data key with an explicit data-not-instructions rule;
instruction-like passages are flagged. Scores are deterministic. Reruns are
append-only. The handoff is immutable after acceptance (ORM guard). Nothing is
fetched during analysis.

## 10. Quality remediation addendum

Changes to the specialist layer made after the first live HCA run. The
architecture in sections 1-9 is unchanged.

### 10.1 Prompt structure

`prompt = specialist_block(spec) + SHARED_RULES`. The specialist block holds the
role, its analytical discipline, its own parameters and its id prefix.
`SHARED_RULES` is byte-identical for all six and names no parameter.

### 10.2 Contract additions (all additive)

`HypothesisRecord`: `evidence_basis`, `model_stated`, `review_flags`,
`transmission_mechanism`, `fo_handoffs`, `recovery`. `CalculationTrace`: `group`.
Agent response: `evidence_basis`, `fo_handoff`, `transmission_mechanism`,
`checklist_responses`.

### 10.3 Validation layers

| Layer | What | Where |
|---|---|---|
| A source integrity | verbatim quote; one recovery attempt (`layout_gap`, `relocated`, `unresolved`) | `recovery.py` |
| B schema and numbers | ownership, permitted values, indicator binding (id, value, unit, period, denominator) | `hypotheses.py`, `plans.py` |
| C inferential validity | nine lexical, negation-aware rules; effect `audit_only` or `qualify` | `inference_rules.py` |
| D sufficiency | evidence basis from verified quotes; generic-risk-only and quote-does-not-cover-claim are qualified; company-specific parameter values need an established company-specific finding | `hypotheses.py` |

Rejection granularity: schema, ownership, unadmitted evidence ids, score fields
and bad hypothesis ids reject the specialist's whole output. Everything else
holds back one hypothesis or one parameter. Layers C and D are heuristics and
are recorded as such.

### 10.4 Coverage

A `SectorProfile` supplies a checklist (question + retrieval terms + patterns a
validated answer must match) and sector indicators. Case coverage checks use the
same mechanism from a per-case JSON file. A question counts as addressed only
when a hypothesis the agent cited for it survived validation and matches the
patterns. One bounded follow-up re-asks a specialist about unanswered questions
that have passages.

### 10.5 Indicators

Grouped (`growth`, `margin`, `capital`, `cash`, `segment`, `geography`, `payer`,
`workforce`, `realisation`); each specialist declares the groups it receives.
Sector indicators are computed from reported concepts by the existing
`ParameterEngine` formulas, current and prior year. Conflicting facts are left
out, never estimated.

### 10.6 BO6

Requires `transmission_mechanism`; a component that is not a usable primary
hypothesis makes the interaction unverified; an interaction cannot be more
certain or more material than its components; management attribution carries
through; debt-service, liquidity, covenant or rating conclusions are held back
and converted to an FO handoff.

### 10.7 Specialists-only run

`BusinessOverviewRunner.run_specialists` executes phases 1-3 only. `demo.py`
builds one payload from it and renders every report from that payload.
`benchmark.py` replays a baseline's recorded responses through the current
validators to separate validator effects from prompt and retrieval effects.
