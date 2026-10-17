# BO investigation integration — M0 assessment and revised plan

Branch `feat/bo-investigation-integration` (from `feat/bo-quality-remediation` at `0ccb429`).
Status: **M0 only. No application code has been changed.** This document is for review
before M1 begins.

## 1. Sources and authority

| Source | Status |
|---|---|
| `Credit Memo Dump.docx` (16-step workflow, BO architecture diagram, hypothesis framework, governance controls) | Read. **Architectural authority**, with the 15 October interpretations below. Not committed to the repo. |
| `Credit Memo Dump.txt` | A different document: the Module 3 (Corporate Structure & Management) design handoff. Used only for Module 3 / Validator 7 scope. |
| Original Claude Code handoff | Superseded where it conflicts with the docx (see §2). |

Binding interpretations agreed on 17 October:

1. Business Overview (BO) = four internal modules: M1 Business Model & Operating Profile; M2 Industry; M3 Corporate Structure & Management; M4 Strategy & Capital Allocation. Financial Overview (FO) is outside BO and out of scope.
2. M1 = five primary specialists + Agent 6 (integrator). Specialist 4 = Operating Model & Cost Resilience; specialist 5 = Competitive Durability. No ownership overlap with M2–M4. Parameter IDs preserved through versioned remapping.
3. Eight primary agents (M1 ×5, M2, M3, M4), each with a logically separate deterministic validator and audit record. Agent 6 is not one of the eight; its inputs must have passed validation and the step 12 gate, and its output is still governed.
4. Step 11 classifies Display / On Demand / Suppress, separate from verification status and direction. Suppressed findings remain recoverable.
5. Cross-module synthesis re-applies steps 8–11 across M1–M4 conclusions.
6. Order: 12 completeness → 13 Agent 6 → cross-module synthesis → 14 challenge → 15 deterministic scoring → 16 narrative.
7. The docx's duplicate "dimension 6": Overall Business Resilience = Agent 6; Financial Stewardship & Capital Commitments = M4.

## 2. Test baselines (recorded 17 Oct, offline, no model calls)

| Branch | Result | Notes |
|---|---|---|
| `feat/bo-quality-remediation` `0ccb429` | **893 passed, 0 failed, 128 skipped** | Skips are local-data gaps: benchmark workbook absent (~100), HCA FY2025 10-K not acquired (2), no OCR engine (5), no PDF backend, `google-genai` not installed, live test gated by `BO_LIVE_TEST`. The remediation report's "1,017 pass" was in an environment with that data. |
| `Daniel's-Branch` `6662c17` | **833 passed, 1 failed, 1 skipped** | Failure: `test_default_delta_case_and_source_viewer`, Delta fact count 345 ≠ 358. Not Module 1 code; probably OCR-dependent, **cause not verified**. |

Environment: Python 3.13.7 (repo pins 3.11 in `.python-version`). The HCA filing is not present in any local checkout; it must be fetched with `sec-fetch` (public SEC, no provider cost) before the HCA evaluation in M2.

## 3. What each branch actually is

### Baseline: `feat/bo-quality-remediation`

Six narrow LLM specialists (BO1–BO6) inside a 27-job registry (`default_registry("v2")`; the other 21 jobs are FO, structuring, mitigant, orchestrator, challenger and credit agents). Execution (`app/services/bo/runner.py:379`):

```
prepare_case (ingest, reconcile, snapshot, metrics — deterministic)
 → indicators + routed, sealed evidence packets per specialist
 → BO1..BO5 concurrently (single LLM call each: hypotheses + status + parameters)
 → ≤1 retrieval follow-up, ≤1 coverage follow-up
 → BO6 (usable BO1–BO5 hypotheses only)
 → BusinessRiskScore → business_orchestrator → business_challenge → ≤1 rerun
 → memo, audit report, handoff (pending human review)
```

Evidence status: implemented; exercised by offline tests; demonstrated live for the six specialists (HCA FY2025, `gemini-3.1-flash-lite`, one model, several runs, 14–24 usable hypotheses per run). Orchestrator, challenger, score and memo were **not** re-run live after remediation (stated in its report).

### Daniel: `Daniel's-Branch`

Branched from `main`, independent of the BO work (5 commits, ~17k lines, mostly `app/services/analysis/*`, `app/schemas/llm.py`, `app/schemas/evidence.py`, `app/prompts/templates.py`). One **module-wide** pipeline (`module1_orchestrator.py`), stages A–H:

```
A evidence preparation (own page-level corpus built from PDF bytes)
 → B hypothesis generation (one LLM call across all dimensions)
 → C enquiry planning (LLM) → D lexical retrieval (deterministic)
 → E hypothesis testing (LLM + deterministic premise corroboration / grounding)
 → F signals, impact, materiality (deterministic rubric; LLM optional)
 → G evidence completeness gate (deterministic)
 → H RED challenge (LLM; escalation to human, no rerun)
```

Evidence status: implemented; exercised by scripted offline tests and a scripted demo on **NVIDIA FY2026**, not HCA. The live path (`module1_live.py`) covers only stages B–D; testing, signals, completeness and RED have never run live. No recorded live output is in the repository.

There are no agents, no per-specialist ownership, no parameters, and no Agent 6. `CreditDimension` includes `FINANCIAL_STEWARDSHIP_CAPITAL_COMMITMENTS`, and both the generation prompt ("six credit dimensions") and the completeness gate (`_ALL_DIMENSIONS = tuple(CreditDimension)`) treat it as a Module 1 dimension. That repeats the numbering slip (interpretation 7): as written, Module 1 would always look incomplete or would analyse M4's remit.

Merge mechanics: a dry-run merge into the baseline gives only two textual conflicts (`.gitignore`, `app/services/pipeline/default_case.py`). The real conflicts are semantic (§7).

## 4. 16-step workflow against the code

| Step | Baseline | Daniel | Gap / decision |
|---|---|---|---|
| 1–3 Source, extract, validate & classify facts | Shared pipeline: admission, snapshot, reconciliation; `claim_type` on hypotheses | Same pipeline (from `main`) plus its own PDF corpus builder | Keep baseline. Do not import Daniel's corpus builder as an evidence source. |
| 4 Quantitative indicators | `bo/indicators.py` with `CalculationTrace` (formula id, version, sources) | `metric_resolver.py` | Keep baseline; route Daniel's grounding through `CalculationTrace` ids. Missing: agent-requested deterministic calculations (handoff §3). |
| 5 Context / benchmark | Benchmarking service + one sector profile (healthcare provider) | Passes benchmarks through | Keep baseline. Single sector. |
| 6 Generate hypotheses | Combined with testing in one specialist call | Separate generation service with allow-lists | **Import (adapt):** needs a per-specialist dimension scope. |
| 7 Required evidence & retrieval | Fixed retrieval plans with purposes (support / contradict / mitigant / mitigant_challenge) + one agent follow-up + checklist coverage | Per-hypothesis enquiry plan (supporting, contradictory, offsetting, effectiveness) + lexical search with coverage enforcement | **Import (adapt)** the enquiry planner; run searches over baseline-admitted passages only. Keep baseline plans as the guaranteed floor. |
| 8 Analyse & test | Model self-assigns status; validator checks quotes lexically. Known gap: **no entailment check** | Separate testing stage: mechanism premises, corroboration channels, entailment state, adjudication, "no promotion by the model" | **Import (adapt).** The main value Daniel's branch adds; directly addresses the baseline's known gap #1. Baseline lexical layers stay in front of it. |
| 9 Positive / negative signals | `direction` on the hypothesis | Signal stage keeps the original direction and flags mismatches | Import with adapter. |
| 10 Credit impact | Free-text `credit_mechanism` | Impact channel, quantification only against approved metric ids | Import with adapter. |
| 11 Materiality & Display/On Demand/Suppress | `materiality` and `display_priority` are **model-chosen**; demoted only if audit-only | Deterministic, versioned rubric, but **never Suppress**, and magnitude/breadth come from caller-supplied fixtures | **Missing in both.** Build a deterministic step 11 classifier: materiality rubric (adapt Daniel's) + presentation class separate from status and direction; Suppress kept in audit. |
| 12 Evidence completeness | Sector checklist coverage questions (partial) | Full gate: source-package vs investigation vs sufficiency kept separate; BLOCKED / NOT_ASSESSABLE never overridden | **Import (adapt).** Dimension set must be per module, not the global enum. |
| 13 Agent 6 | BO6: requires mechanism, capped by its components' certainty/materiality, rejected/unverified components cannot be premises | — | Keep baseline. Add step 12 gate in front of it (currently gated only on `usable`). |
| Cross-module synthesis (re-applies 8–11) | — (`reconcile.py` does lexical cross-agent duplicate/contradiction detection within BO) | — | **Missing.** New agent + validator. `reconcile.py` is a reusable deterministic pre-pass. |
| 14 Challenge & reconciliation | `business_challenge` + one bounded rerun + escalation | RED: targets must resolve, evidence-backing decided by the service, unresolved by default, never auto-resolved, escalation | Keep baseline loop (rerun); **import Daniel's reconciliation rules**. Scope must become all four modules + synthesis. |
| 15 Module score | `BusinessRiskScore` via existing `RubricEngine`; `unavailable` state exists. **Runs before orchestrator and challenger** | — | **Reorder** to after challenge. Restructure per module (§6). |
| 16 Narrative | `business_orchestrator` (validated LLM narrative) + deterministic memo/audit rendering | `module1_report.py` | Keep baseline. Orchestrator moves after scoring and becomes the narrative writer (decision D4). |

## 5. Eight primary agents and their validators

| # | Target agent | Existing implementation | Validator today | Needed |
|---|---|---|---|---|
| 1 | Revenue Quality & Demand Defensiveness | BO1 `bo_business_model_revenue` | `DeterministicValidator.for_agent` + `HypothesisValidator(spec)`, instantiated per agent with that agent's sealed packet | Ownership unchanged. |
| 2 | Customer Stickiness & Concentration | BO2 `bo_customers_contracts` | same | Remap (§6). |
| 3 | Pricing Power & Revenue Realisation | part of BO3 `bo_competition_pricing` + `revenue_realisation` from BO2 | same | **New split agent.** |
| 4 | Operating Model & Cost Resilience | part of BO4 `bo_operations_management` | same | Narrowed; M3/M4 parameters removed. |
| 5 | Competitive Durability | part of BO3 | same | **New split agent.** |
| 6 | M2 Industry Information | none (BO5 holds some industry/regulatory parameters) | — | Not implemented. |
| 7 | M3 Corporate Structure & Senior Management | none (BO4 holds management/governance parameters) | — | Not implemented; scope in the Module 3 handoff. |
| 8 | M4 Strategy & Capital Allocation / Financial Stewardship | none (BO4 holds acquisition/capex parameters) | — | Not implemented. |
| — | Agent 6 | BO6 `bo_resilience_interactions` | BO6 branch of `HypothesisValidator` | Add step 12 gate on its inputs. |

Validators are logically per agent already: one instance per agent, bound to that agent's spec, packet and owned parameters. What is missing is an explicit **validator identity and version** (Validator 1…8) on each validation audit record. That is a small M1 change, not a rewrite.

## 6. Module 1 roster changes (proposed v3 roster)

Introduce `default_registry("v3")`. v1 and v2 remain buildable so stored records and replays stay readable. `BO_V2_TO_V3_PARAMETER_OWNER` records every move. Parameter IDs do not change; ownership, agent definition hash and registry hash do (intended, per the BO README).

| Parameter (unchanged id) | v2 owner | Proposed v3 owner |
|---|---|---|
| `business_model_classification`, `revenue_recurrence_assessment`, `demand_defensiveness_assessment`, `cyclicality_seasonality_assessment` | BO1 | M1-1 Revenue Quality |
| `customer_dependence_assessment`, `contract_termination_risk_assessment` | BO2 | M1-2 Customer Stickiness |
| `switching_cost_assessment` | BO3 | M1-2 Customer Stickiness (docx places switching costs here) |
| `supplier_dependence_assessment` | BO2 | **D1**: M1-2 or M1-4 (the docx lists suppliers under both) |
| `revenue_realisation_assessment` | BO2 | M1-3 Pricing Power & Revenue Realisation |
| `pricing_power_assessment` | BO3 | M1-3 |
| `competitive_position_assessment`, `competitive_pressure_assessment`, `barriers_to_entry_assessment` | BO3 | M1-5 Competitive Durability |
| `workforce_cost_resilience_assessment` | BO4 | M1-4 Operating Model |
| `management_quality_assessment`, `key_person_dependency_assessment`, `governance_concern_assessment` | BO4 | M3 |
| `acquisition_strategy_assessment`, `integration_risk_assessment`, `capex_strategy_assessment` | BO4 | M4 |
| `regulatory_dependency_assessment` | BO5 | **D2**: M2 (company-specific reimbursement exposure analysed by M1-3) |
| `litigation_risk_assessment`, `material_event_assessment` | BO5 | **D2**: M3 (enforcement/misconduct evidence) or M2 |
| `external_event_exposure_assessment` | BO5 | **D2**: M1-2 (geographic concentration) or M2 |
| `compound_risk_assessment`, `mitigating_capability_assessment` | BO6 | Agent 6 |

Consequences:

* BO5 leaves Module 1 entirely; BO3 splits into two agents; BO4 narrows to operating model and cost.
* Module 1 still has five primaries (BO3 split adds one, BO5's removal takes one away), so the specialist call count is unchanged; each prompt is narrower.
* Parameters rehomed to M2–M4 need an owner that exists (ownership invariant). Proposed (**D3**): declare M2/M3/M4 agent definitions in v3 that own those parameters but are **not executed** in this cycle. Their parameters are recorded `not_assessed`, never neutral or favourable. This is a contract stub, not speculative module logic.

## 7. Conflicts to resolve in the adapter

| Conflict | Resolution |
|---|---|
| **Two evidence-id schemes.** Daniel builds passages from PDF bytes with his own hashes; baseline evidence ids are admitted `narrative_evidence` in `CanonicalEvidenceSnapshot`, sealed per agent packet. | Adapter builds Daniel's `EvidenceCorpus` **from the agent's sealed packet**. Baseline ids are authoritative; Daniel's corpus builder is not used on the BO path. Retrieval can never widen beyond admitted evidence. |
| **Two hypothesis models.** `HypothesisRecord` (persisted, agent-owned, `Disposition`, `Admissibility`) vs `CreditHypothesis` / governed test results (in-memory, dimension-based). | `HypothesisRecord` stays the authoritative store. Daniel's objects are working objects inside one specialist's investigation; the adapter maps their outcome onto `HypothesisRecord` and keeps the full investigation trace as a linked, versioned audit record. No second authoritative store. |
| **Status vocabularies.** Baseline `supported / qualified / rejected / unverified`; Daniel `supported / partially_supported / rejected / unverified`. | `partially_supported ↔ qualified`, explicit mapping table with round-trip tests. Baseline lexical holds (quote not verbatim, unbound numbers, generic risk only…) can only lower a status, never raise one. |
| **Dimension set.** Daniel's enum and gate include M4's stewardship dimension. | Dimension scope is passed per specialist (one dimension per M1 primary); the gate checks the module's declared dimensions only. |
| **Materiality.** Baseline: model-chosen. Daniel: deterministic, but fixture inputs and no Suppress. | Deterministic step 11 (§4). Model proposals recorded in `model_stated`, never authoritative. |
| **Score inputs.** `BusinessRiskScore` combines a competitive-position signal (M1) and a management/governance signal (now M3). No approved rubric covers revenue quality, customers or operating model. | **D5**: per-module scoring needs an approved rubric. Until then the M1 score is `provisional` (competitive position only) or `unavailable`; I will not invent rubric weights. |
| **Ordering.** Baseline scores before orchestrator and challenger. | Reorder to steps 13 → synthesis → 14 → 15 → 16 in M4. |
| **Persistence.** Daniel adds no ORM tables (audit log only). Baseline tables: `hypothesis_records`, `retrieval_attempts`, `accepted_business_overviews`. | New fields (validator id/version, presentation class, investigation trace ref) are additive with defaults, so old rows stay readable. A migration test is part of M1. |
| **Prompts.** Daniel's prompts live in `app/prompts/templates.py` (generic, six dimensions). Baseline uses `bo_prompts.py` (specialist block + shared rules, tested to contain no parameter names). | Port the needed stage prompts into the BO prompt structure with specialist scoping; keep the prompt-version registry. |
| **Cost.** Daniel's loop is 3–4 LLM calls per specialist (generate, plan, test, optional signals) vs 1–2 today. | Measured in M2; reported with tokens and runtime. |

## 8. Reuse matrix (Daniel → baseline)

| Daniel component | Decision | Why reuse instead of extending the BO equivalent |
|---|---|---|
| `hypothesis_generation.py` | Adapt | Separating generation from testing is what step 6 requires; the baseline has no equivalent stage. Needs dimension scope + baseline allow-lists. |
| `enquiry_planning.py` | Adapt | Per-hypothesis enquiries (incl. offset effectiveness) go beyond the fixed baseline plans. Baseline plans stay as a floor. |
| `evidence_retrieval.py` — `EvidenceSearchService` | Adapt (search only) | Deterministic, versioned ranking. Corpus builder **not** imported. Overlaps `bo/retrieval.py`: one must own retrieval for BO. Proposal: BO retriever stays the owner; Daniel's ranker runs inside it for enquiry queries. |
| `hypothesis_testing.py` | Adapt | Premise-level corroboration and entailment states address the baseline's documented gap. Largest and most valuable import. |
| `credit_signal_assessment.py` | Adapt | Steps 9–10 have no baseline equivalent. Materiality rubric reused but derived from data, plus Suppress. |
| `evidence_completeness.py` | Adapt | Step 12 gate has no baseline equivalent beyond checklist coverage. |
| `red_challenge.py` | Partial | Import the reconciliation rules into `business_challenge` validation; keep the baseline challenge loop and bounded rerun. |
| `metric_resolver.py`, `grounding.py` change | Adapt minimal | Grounding must resolve against baseline `CalculationTrace`. |
| `module1_orchestrator.py` | **Do not import** | Module-wide, no agents, would replace `BusinessOverviewRunner`. Its stage sequencing is the model for the per-specialist investigation driver. |
| `module1_demo.py`, `module1_live*.py`, `module1_report.py`, `app/demo.py` | Do not import | NVIDIA-specific demo/report paths; BO has its own reporting. |
| `.kiro/steering/credit-memo-principles.md` | Import | Documentation only. |
| `default_case.py` change, `providers.py` | Review in M1 | Textual conflict / provider wiring; import only if needed. |

## 9. Is `feat/bo-quality-remediation` still the right baseline?

Yes. Against the corrected architecture it already has the agent registry and ownership invariants, per-agent sealed evidence packets, deterministic validators per specialist, Agent 6 with premise rules, a challenge loop with bounded rerun, deterministic scoring with an `unavailable` state, human review and immutable handoff, and live HCA evidence for the specialists. Daniel's branch has none of the agent, ownership, persistence or human-review structure. What it has is the per-hypothesis investigation loop, which fits beneath the BO boundary.

Required departures from the baseline: the v3 roster (§6), the reordering of steps 14–16, a deterministic step 11, a step 12 gate, cross-module synthesis, and per-module scoring.

## 10. Revised milestones

**M0 — Assessment (this document).** Done, pending review.

**M1 — Contracts, roster v3, adapter (no new LLM behaviour).**
* `default_registry("v3")` with the §6 ownership and `BO_V2_TO_V3_PARAMETER_OWNER`; v2 stays the default until M3.
* M2/M3/M4 agent definitions declared, not executed; their parameters `not_assessed` (if D3 is accepted).
* Validator identity/version (1–8, plus Agent 6 and synthesis governance) on validation audit records.
* Shared investigation contract: dimension, specialist owner, direction, mechanism, supporting/contradicting evidence, metric refs, claim classification, verification status, entailment state, confidence (fact vs interpretation), impact, materiality, presentation class, missing information, provenance, versions.
* Adapter: sealed packet → Daniel corpus; Daniel outcomes → `HypothesisRecord` + linked investigation trace; status mapping.
* Step 11 deterministic classifier (materiality + Display/On Demand/Suppress; Suppress retained).
* Tests: v2 regression unchanged; round trips lose no evidence/status; old rows readable; ownership uniqueness across v3 including stubs; status can never be raised by a lexical layer; adversarial cases from the handoff (genuine quote / wrong inference, right number / irrelevant claim, conflicting periods, intention as success, missing evidence ≠ safety).

**M2 — Investigation loop on M1-2 (Customer Stickiness & Concentration), evaluated on HCA FY2025.**
* Steps 6–12 for one specialist under v3 ownership, with the step 12 gate in front of Agent 6 for that specialist's output.
* Controlled comparison: **existing single-pass specialist prompt under the same v3 ownership and the same sealed packet** vs the enhanced loop. The baseline arm is therefore not today's BO2, whose ownership differs.
* Measures as in the handoff (material coverage, supported vs unsupported, contradiction handling, mitigant investigation, citation validity, entailment, gaps, tokens/runtime/cost). Offline scripted first; live only with your authorisation (D6).
* **Stop for review.**

**M3 — All five M1 specialists + Agent 6 under v3.** Concurrency, separate validator records, step 12 before Agent 6, bounded targeted re-investigation, failure of one specialist marks M1 incomplete rather than silently complete. v3 becomes the default.

**M4 — M1 conclusion, challenge, scoring, narrative.** Reorder to 13 → (synthesis slot) → 14 → 15 → 16. Import RED reconciliation rules into the challenger. Per-module scoring under the approved rubric (D5), otherwise provisional/unavailable. `business_orchestrator` becomes the step 16 narrative writer (D4). Reproducible rendering from frozen inputs.

**M5 — Module conclusion interface and cross-module synthesis.** Versioned `ModuleConclusion` produced by M1 (and by the M2–M4 stubs as `not_assessed`). Cross-module synthesis agent + its validator re-applying steps 8–11 across modules; deterministic contradiction pre-pass reusing `reconcile.py`; challenge scope widened to all four modules + synthesis. Acceptance tests with constructed module conclusions showing compound risks and contradictions are detected and preserved. M2–M4 agent logic remains out of scope.

## 11. Decisions needed before M1

| # | Decision | Recommendation |
|---|---|---|
| D1 | Owner of `supplier_dependence_assessment` | M1-4 Operating Model (procurement is already in BO4's retrieval plan; supplier concentration is an operating dependency) |
| D2 | Owners of BO5's parameters | `regulatory_dependency` → M2; `litigation_risk`, `material_event` → M3; `external_event_exposure` → M1-2 as geographic concentration. Needs credit-team confirmation. |
| D3 | Parameters that move to M2–M4 before those modules exist | Declare non-executed owners; record `not_assessed`; M1/BO outputs say so explicitly |
| D4 | Role of `business_orchestrator` | Step 16 narrative writer after scoring; cross-module synthesis is a new, separate agent |
| D5 | Module 1 scoring rubric | Needs an approved rubric from the team. Until then: M1 score provisional/unavailable. I will not invent weights. |
| D6 | Paid model calls in M2 | Offline scripted comparison first; a small number of live HCA runs (current provider `gemini-3.1-flash-lite`) only with explicit approval and a stated budget. Fetching the HCA 10-K from SEC has no provider cost. |

## 12. Architecture decision records

* **ADR-1 Authority.** `Credit Memo Dump.docx` + the 17 Oct interpretations supersede the original handoff where they conflict (module structure, specialist 4/5 scope, validators, step order, Display/On Demand/Suppress).
* **ADR-2 Evidence ids.** Baseline admitted evidence ids are authoritative on the BO path; Daniel's PDF corpus builder is not used there.
* **ADR-3 One authoritative hypothesis store.** `HypothesisRecord`; investigation objects are linked audit traces.
* **ADR-4 Roster versioning.** v3 roster added alongside v1/v2; parameter ids unchanged; ownership moves recorded in an explicit map.
* **ADR-5 No module-wide orchestrator import.** The investigation loop runs per specialist under `BusinessOverviewRunner`.
