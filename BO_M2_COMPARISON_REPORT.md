# BO investigation integration — M1 and M2 report

Branch `feat/bo-investigation-integration`. Architecture: [`BO_ARCHITECTURE_V3_SPEC.md`](BO_ARCHITECTURE_V3_SPEC.md).
Background: [`BO_INTEGRATION_M0_ASSESSMENT.md`](BO_INTEGRATION_M0_ASSESSMENT.md).

**Status: M1 complete. M2 offline comparison complete on a synthetic case; the HCA
FY2025 arm has not been run because the filing is not yet in the workspace.
Not ready for M3** — see §5.

No paid model call was made. Every model response in this report is scripted.

## 1. What was implemented

**M1 — contracts, roster, validators, step 11, adapter**

| Item | Where |
|---|---|
| Architecture spec incorporating the 17 Oct decisions | `BO_ARCHITECTURE_V3_SPEC.md` |
| Selective import of Daniel's services (steps 6–12, RED) + their 255 tests; his module-wide orchestrator, NVIDIA demo/live paths and reports not imported | commit `422af03` |
| Roster v3: M1S1–M1S5 + Agent 6; M2/M3/M4 declared, never executed; `BO_V2_TO_V3_PARAMETER_OWNER`; `default_registry("v3")` (30 jobs) | `bo/roster_v3.py`, `agents/registry.py` |
| `ParameterStatus.NOT_ASSESSED` (no value, no run id, never usable for scoring) | `schemas/agentic.py` |
| Validator identity + version on every hypothesis record; per-agent `agent_output_validated` audit event | `bo/hypotheses.py`, `bo/validation_audit.py` |
| Deterministic, versioned Display / On Demand / Suppress policy (`presentation-policy-1`); opt-in for v3, v2 unchanged | `bo/presentation.py` |
| Adapter: sealed packet → search corpus with baseline evidence ids; status mapping; strictest-wins; `supported` needs deterministic corroboration; metric links and mitigant outcomes carried only when corroborated | `bo/investigation.py` |
| Dimension scope added to Daniel's generation and completeness services; his generation prompt aligned with the agreed ownership (supplier → Operating Model, geography → context only) | `analysis/hypothesis_generation.py`, `analysis/evidence_completeness.py`, `prompts/templates.py` |
| v3 specialist briefs and parameter-classification prompts | `prompts/bo_prompts.py` |

**M2 — per-specialist investigation and controlled comparison**

| Item | Where |
|---|---|
| `SpecialistInvestigation`: steps 6–12 + parameter classification for one specialist, over its sealed packet only | `bo/investigation.py` |
| Comparison harness: identical inputs for both arms, anchor-based scripts, call/payload recording, scoring against a pre-registered manifest | `bo/comparison.py` |
| Scenario set for M1S2 on the synthetic case | `examples/bo_m2/nfh_m1s2/` |
| Recorded results | `examples/bo_m2/nfh_m1s2/results.json` |

## 2. Tests

| Point | Passed | Failed | Skipped |
|---|---|---|---|
| Baseline `0ccb429`, local data absent | 893 | 0 | 128 |
| Baseline `0ccb429`, benchmark workbook + derived artefacts restored (local, git-ignored) | 1007 | 0 | 14 |
| After Daniel import (`422af03`) | 1262 | 0 | 14 |
| After M1 (`4c9e41a`) | 1294 | 0 | 14 |
| After M2 (this commit) | 1302 | 0 | 14 |

Remaining skips: no OCR engine (5), HCA 10-K not acquired (2), no PDF backend,
`google-genai` not installed, live tests gated (`BO_LIVE_TEST`), and others that
need local-only data. Python 3.13.7 (repo pins 3.11).

Two existing tests were edited, both for intended changes: the unknown-roster example in
`test_bo_registry.py` (`"v3"` now exists → `"v9"`), and the event-type inventory in
`test_audit_immutability.py` (new `agent_output_validated`).

## 3. M1 acceptance evidence

| Criterion | Evidence |
|---|---|
| Existing BO regression tests remain valid | full suite green; v1/v2 registry hashes pinned to the untouched baseline (`test_earlier_rosters_are_unchanged`) |
| Ownership and source invariants enforced | v3 builds through the registry's uniqueness check; every v2 parameter has exactly one v3 owner; the 17 Oct mapping asserted parameter by parameter |
| Round trips do not discard evidence or status | status vocabularies map 1:1; adapter keeps contradicting evidence, quotes and metric bindings; tests in `test_bo_roster_v3.py` |
| Old records readable | a pre-v3 `HypothesisRecord` payload loads and round-trips; new fields are additive in the JSON payload, no migration |
| Modules 2–4 `not_assessed`, never neutral | `not_assessed` results carry no value, run id or signal; schema rejects one with a value or an invented run id |
| Score semantics not silently changed | weights and coverage rules unchanged; with M3 not assessed the management/governance signal is `unavailable` and the business score is never `final` (`provisional` at ≥ 60 % coverage, else `unavailable`) |
| Step 11 deterministic, model label not authoritative | policy table tests; model proposal recorded but ignored; suppressed record persisted and reloaded |

Note for the rubric owners: when the business score is `provisional`, the existing
engine computes its band over the weights that are present. That is pre-existing
behaviour; it has not been changed.

## 4. M2 comparison

### Design

* Case: the repository's synthetic fixture `NFH_BO` (fictional hospital operator).
  Specialist: **M1S2 Customer Stickiness & Concentration** under v3 ownership.
* Identical inputs: same sealed packet (6 passages), indicators, permitted values and
  validators; confirmed by an input fingerprint.
* **Equally wrong model.** Both arms' scripted responses make the same claims, cite the
  same quotes and assert `supported` for every claim. The comparison therefore
  measures what each pipeline's *deterministic* controls catch. It does not measure
  how well a model generates, investigates or reasons; that requires live runs.
* Scenarios and acceptable outcomes were written in `manifest.json` before the first
  run. Two scripting errors were then corrected (a missing required "offsetting"
  enquiry; a denominator spelt `consolidated revenue` instead of
  `consolidated_revenue`). The manifest was not changed.

### Results

| Scenario | Kind | Single pass | Investigation |
|---|---|---|---|
| S1 Government programmes 45 % of revenue (payer category) | control | supported, **usable** | unverified, not usable |
| S2 Genuine quote ("multi-year agreements set negotiated rates") → "guarantees volumes" | defect | unverified (rule `rate_agreement_to_volume_guarantee`) | unverified |
| S3 Correct bound number (Texas 30 %) → "relationships are sticky" | defect | supported, **usable** | supported, **usable** |
| S4 Non-disclosure of a >10 % payer → "concentration is low" | defect | unverified | unverified |
| S5 Multi-year agreements "demonstrably" mitigate termination risk | defect | supported, **usable**; mitigant accepted as *demonstrated* | unverified; mitigant *unproven* |
| S6 Termination risk low, contradiction ignored | defect | supported, **usable** | unverified |
| S7 No volume guarantee; terminable on notice | control | qualified, **usable** | unverified, not usable |

| Measure | Single pass | Investigation |
|---|---|---|
| Defects held back (of 5) | 2 | 4 |
| Correct findings kept usable (of 2) | 2 | 0 |
| Parameters accepted (of 3; 1 correct, 2 defective) | 3 (incl. both defective) | 0 |
| Contradictory-evidence searches that returned candidate passages | not applicable (plan-level retrieval only) | 6 of 7 hypotheses (passages in the trace) |
| Contradicting evidence recorded on the finding | 0 | 0 (the scripted model ignored it) |
| Off-dimension hypothesis | not applicable | recorded as out of scope, not investigated |
| Step 12 gate | not applicable | `blocked_pending_evidence`, 16 gaps |
| Model calls | 1 | 4 |
| Request / response characters | 10,489 / 4,981 | 46,863 / 23,520 (≈ 4.5× / 4.7×) |

Characters are measured on the scripted exchange. Real token counts and cost are not
available offline.

### What the results show

1. **Neither arm is defensibly better yet.** The investigation path held back more
   defects (4 vs 2) but also held back both correct findings and every parameter. Under
   its current governance, rolling it out would leave Module 1 largely `unverified`.
2. **Why the controls failed in the investigation arm.** Daniel's testing stage can
   corroborate a premise only through an approved metric or a human adjudication. With
   no LLM judge configured, every narrative premise is `not_verifiable`. So a correct
   fact quoted verbatim from the filing cannot become `supported` or even
   `partially_supported`. This is strong protection against S2-type errors, paid for
   with recall.
3. **A gap in both arms (S3).** A correct number attached to an unrelated claim passes
   both pipelines. In the investigation arm, a premise linked to any approved metric is
   graded against the metric's own statement. The premise's text is never compared,
   and a missing `claimed_concept` is skipped rather than failing.
4. **Baseline gaps confirmed.** The baseline accepts a "demonstrated" mitigant whenever
   the cited outcome passage is admitted (S5), and it does not notice an ignored
   contradiction (S6). Two defective parameter values were accepted.
5. **Contradiction handling still depends on the model in both arms.** The investigation
   path retrieves contradicting passages per hypothesis, so they are visible in the
   trace. If the testing model ignores them, nothing deterministic flags it.

## 5. Recommendation and decisions for review

Do not start M3 with the investigation path as it is. Proposed next step (small,
deterministic, re-measurable on the same pre-registered scenarios plus HCA):

| # | Change | Addresses |
|---|---|---|
| R1 | A **verbatim reported-fact channel**: a premise classified as a reported fact is corroborated when its own text is covered by a verified verbatim quote (the BO Layer A quote check plus the quote-coverage check). Inference and outcome premises still need a metric or a human adjudication. | Recall loss (S1, S7) without reopening S2 |
| R2 | **Fail-closed metric links**: `claimed_concept` required and matched; and an *inference* cannot reach `supported` on metric-grounded factual premises alone. | S3 in the investigation path |
| R3 | **Unassessed contradiction flag**: if a contradictory enquiry returned passages that the testing model neither marked contradicting nor irrelevant, flag for review and cap the status at `qualified`. | S6 in both paths |
| R4 | **Baseline mitigant check**: "demonstrated" needs an outcome-type passage, not merely an admitted one (apply to the single-pass validator too). | S5 baseline |
| R5 | Optional, needs budget: an LLM entailment judge (supported by Daniel's grounding evaluator) evaluated live against these scenarios. | Narrative entailment generally |

Decisions requested:

1. Approve R1–R4 as the next increment (still M2), or choose differently.
2. HCA: once the filing is in `examples/healthcare/hca-2025/filing/`, I will write the
   HCA M1S2 scenario scripts (anchored on real passages) and re-run both arms.
3. Live evaluation: a small budgeted run would test what this offline comparison cannot,
   namely whether the investigation loop makes the model find more material issues.

## 6. Remaining risks and assumptions

* Synthetic case only; seven scenarios; I wrote both arms' scripts. The "equally wrong
  model" design limits bias, but it is not an independent evaluation.
* The investigation arm's governance is Daniel's; its scripted tests passed before
  integration, but its testing, signal and completeness stages have never run live.
* Materiality in the investigation path uses Daniel's provisional rubric. Magnitude and
  breadth inputs are not derived from data, so most findings fall to `medium` and are
  flagged for human review.
* The baseline arm ran without its bounded retrieval and coverage follow-ups; neither
  would have been triggered by this script.
* Agent 6, challenge, scoring and narrative were not exercised (M3/M4).

## 7. Commits

| Commit | Content |
|---|---|
| `799b227` | M0 assessment |
| `422af03` | Selective import of Daniel's services and tests |
| `4c9e41a` | M1 |
| (this commit) | M2 harness, scenarios, results, report |
