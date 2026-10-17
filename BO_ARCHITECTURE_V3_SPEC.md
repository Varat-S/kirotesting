# Business Overview architecture v3 — specification

Authority: `Credit Memo Dump.docx` (15 Oct 2026) with the interpretations and review
decisions of 17 Oct 2026. Background and the code survey are in
[`BO_INTEGRATION_M0_ASSESSMENT.md`](BO_INTEGRATION_M0_ASSESSMENT.md). Where the two
differ, this document wins.

## 1. Structure

Business Overview (BO) has four internal modules. Financial Overview (FO) is outside BO.

| Module | Agents | Validators | State in this cycle |
|---|---|---|---|
| M1 Business Model & Operating Profile | five primary specialists + Agent 6 (integrator) | Validators 1–5; Agent 6 governance | Implemented (v3 roster) |
| M2 Industry | one agent | Validator 6 | Ownership contract declared; **not executed** |
| M3 Corporate Structure & Management | one agent | Validator 7 | Ownership contract declared; **not executed** |
| M4 Strategy & Capital Allocation / Financial Stewardship | one agent | Validator 8 | Ownership contract declared; **not executed** |
| Cross-module synthesis | one agent over M1–M4 conclusions | synthesis governance | M5 |

The eight primary agents (M1 ×5, M2, M3, M4) each have a **logically separate validator
and audit record**. Validation code may be shared; every validation record names its
validator id and version. Agent 6 is not one of the eight; its inputs must have passed
their validators and the step 12 gate, and its output is governed by the Agent 6 rules.

## 2. Step order

```
1–3  source, extract, validate & classify facts        (shared pipeline, deterministic)
4–5  indicators, context / benchmark                   (deterministic)
6–11 per primary agent: hypotheses → evidence enquiry & retrieval → testing
     → signals → impact → materiality + presentation class
12   evidence completeness gate                        (deterministic)
13   M1 Agent 6 integration                            (inputs: validated + gated only)
—    cross-module synthesis: re-applies 8–11 across M1–M4 conclusions
14   independent challenge & reconciliation (all four modules + synthesis)
15   deterministic module scoring (authoritative)       — any earlier score is "preliminary"
16   evidence-backed narrative (Business Orchestrator, repurposed)
```

## 3. Roster v3 (`default_registry("v3")`, `agent-registry-3.0.0`)

Parameter ids are unchanged. v1 and v2 remain buildable; stored records keep the roster
version and agent ids they were written with. Every move is recorded in
`BO_V2_TO_V3_PARAMETER_OWNER` (`app/services/bo/roster_v3.py`).

| Code | Agent id | Dimension | Owned parameters |
|---|---|---|---|
| M1S1 | `m1_revenue_quality` | Revenue Quality & Demand Defensiveness | `business_model_classification`, `revenue_recurrence_assessment`, `demand_defensiveness_assessment`, `cyclicality_seasonality_assessment` |
| M1S2 | `m1_customer_stickiness` | Customer Stickiness & Concentration | `customer_dependence_assessment`, `contract_termination_risk_assessment`, `switching_cost_assessment` |
| M1S3 | `m1_pricing_power` | Pricing Power & Revenue Realisation | `pricing_power_assessment`, `revenue_realisation_assessment` |
| M1S4 | `m1_operating_model` | Operating Model & Cost Resilience | `workforce_cost_resilience_assessment`, `supplier_dependence_assessment` |
| M1S5 | `m1_competitive_durability` | Competitive Durability | `competitive_position_assessment`, `competitive_pressure_assessment`, `barriers_to_entry_assessment` |
| M1A6 | `m1_resilience_integration` | Overall Business Resilience (Agent 6) | `compound_risk_assessment`, `mitigating_capability_assessment` |
| M2 | `m2_industry` | Industry | `regulatory_dependency_assessment`, `external_event_exposure_assessment` |
| M3 | `m3_corporate_management` | Corporate Structure & Management | `management_quality_assessment`, `key_person_dependency_assessment`, `governance_concern_assessment`, `litigation_risk_assessment`, `material_event_assessment` |
| M4 | `m4_strategy_capital` | Strategy & Capital Allocation | `acquisition_strategy_assessment`, `integration_risk_assessment`, `capex_strategy_assessment` |

Review notes carried with the roster:

* `litigation_risk_assessment` → M3 is subject to a future review of legal-risk ownership.
* `material_event_assessment` → M3 covers company-specific events; their wider
  interactions belong to cross-module synthesis.
* Geographic concentration evidence may be **consumed** by M1S2 as context. It is owned
  (as `external_event_exposure_assessment`) by M2. M1S2 must not treat geographic
  exposure as customer stickiness.
* Ownership is exclusive; evidence is not. Several agents may receive the same admitted
  passage.

**M2–M4 until implemented:** their agent definitions exist so ownership is declared and
unique, but they are never executed. Their parameters are reported with status
`not_assessed`. No neutral value, no artificial finding, and their absence is never
reported as completed analysis.

## 4. Statuses

| Concept | Values | Notes |
|---|---|---|
| Verification (baseline `Disposition`) | `supported`, `qualified`, `rejected`, `unverified` | `qualified` ≡ docx/Daniel `partially_supported` |
| Admissibility | `established`, `attributed`, `qualified_narrative`, `audit_only` | derived deterministically |
| Direction | `positive`, `negative`, `mixed`, `neutral` | independent of verification and presentation |
| Materiality | `low`, `medium`, `high` | deterministic rubric where available; basis recorded |
| Presentation (step 11) | `display`, `on_demand`, `suppress` | deterministic policy (§5) |
| Parameter status | existing values + `not_assessed` | `not_assessed` = owner not executed; never usable for scoring |

Combining verification results: the strictest wins —
`rejected` > `unverified` > `qualified` > `supported`. A lexical baseline check can only
lower a status. A genuine quotation alone never establishes entailment: in the
investigation path, `supported` additionally requires the deterministic premise
corroboration of the testing stage.

## 5. Step 11 presentation policy (`presentation-policy-1`)

Deterministic and versioned (`app/services/bo/presentation.py`). Inputs: verification,
acceptance state, admissibility, materiality. Direction, confidence, citation count and
any model-proposed label are **not** inputs; a model's proposed label is kept in the
record for audit only.

| Condition (first match wins) | Class |
|---|---|
| superseded or rejected acceptance state | `suppress` |
| verification `rejected` | `suppress` |
| verification `unverified` or admissibility `audit_only`, materiality `high`/`medium` | `on_demand` + open-question flag |
| verification `unverified` or admissibility `audit_only`, materiality `low` | `suppress` |
| usable, materiality `high`/`medium` | `display` |
| usable, materiality `low` | `on_demand` |

Suppressed records are never deleted: they stay in `hypothesis_records` with the reason
and policy version, and the audit report lists them.

## 6. Scoring

No new rubric, weights or thresholds are introduced. The existing deterministic scorer
and `config/poc/scoring.json` (labelled ILLUSTRATIVE – NOT BANK POLICY) are preserved.
Its business score combines `customer_hhi`, `competitive_position_risk_signal` (M1
parameters) and `management_governance_risk_signal` (now M3 parameters). Under v3, while
M3 is `not_assessed`, the management/governance signal is unavailable; the score is
therefore reported `provisional` or `unavailable` per the existing coverage rules, never
silently re-weighted over the remaining inputs. The authoritative step 15 score is
computed after challenge and reconciliation (M4); any score produced earlier is labelled
preliminary.

## 7. Investigation path (M2 onwards)

Per primary agent, under the existing `BusinessOverviewRunner` boundary:

* Evidence: the agent's **sealed packet** (admitted passages, baseline evidence ids) is
  the only corpus. Daniel's PDF corpus builder is not used on the BO path.
* Steps 6–12 use the imported services, scoped to the agent's one dimension.
* Outcomes are written as `HypothesisRecord`s (the single authoritative store), with the
  full investigation trace attached as a versioned audit payload.
* The baseline lexical validator still runs on every record and can only lower status.

## 8. Live testing

Offline implementation and scripted comparisons only. No paid model calls without
separate budget authorisation.
