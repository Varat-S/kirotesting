# BO investigation integration — M1 and M1S2 report

> Naming note: "M2" throughout this report refers to the **development workstream
> "M2"** (the second work package: per-specialist investigation + controlled
> comparison), which was exercised on **Module 1 Specialist 2 (M1S2, Customer
> Stickiness)**. It is NOT the architecture's Module 2 (Industry Information,
> `m2_industry`). The example fixtures live under `examples/bo_m1s2/`.

Branch `feat/bo-investigation-integration`. Architecture: [`BO_ARCHITECTURE_V3_SPEC.md`](BO_ARCHITECTURE_V3_SPEC.md).
Background: [`BO_INTEGRATION_M0_ASSESSMENT.md`](BO_INTEGRATION_M0_ASSESSMENT.md).

**Status: M1 complete. M2 complete: scripted comparisons on the synthetic case and HCA
FY2025 (§4–§5a), and live runs on HCA with `gemini-3.1-flash-lite` (§5b). Ready for
review** (§6).

Sections 4–5a use scripted model responses. Sections 5b–5c report real, paid model calls
(about $2.50 in total, see §5c).

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
| Scenario set for M1S2 on the synthetic case | `examples/bo_m1s2/nfh_m1s2/` |
| Recorded results | `examples/bo_m1s2/nfh_m1s2/results.json` |

## 2. Tests

| Point | Passed | Failed | Skipped |
|---|---|---|---|
| Baseline `0ccb429`, local data absent | 893 | 0 | 128 |
| Baseline `0ccb429`, benchmark workbook + derived artefacts restored (local, git-ignored) | 1007 | 0 | 14 |
| After Daniel import (`422af03`) | 1262 | 0 | 14 |
| After M1 (`4c9e41a`) | 1294 | 0 | 14 |
| After M2 (`ceeb3bb`) | 1302 | 0 | 14 |
| After fixes R1–R4 (`c4edbeb`) | 1309 | 0 | 14 |
| After HCA run (`846b800`; HCA filing present) | 1311 | 0 | 14 |
| After live-run fixes (`19ec617`; `google-genai` installed) | 1323 | 0 | 13 |

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

The tables above are the first recorded run (commit `ceeb3bb`). §5 shows the same
scenarios after the approved fixes.

## 5. Fixes R1–R4 (approved 17 Oct) and re-run

Same manifest, same scripts, same inputs. Nothing in the scenarios was changed.

| Fix | Implementation | Where |
|---|---|---|
| R1 Verbatim reported-fact channel | A premise of a hypothesis classified `reported_fact` (not an outcome premise) is corroborated when its quote is verbatim in a cited passage and covers every content word, number and negation of the premise. A reported fact that is only partly corroborated becomes `unverified` (all or nothing). Inferences never use this channel. | `analysis/grounding.py` (`verbatim_fact_support`), `analysis/hypothesis_testing.py` |
| R2 Fail-closed metric links | A metric link without a `claimed_concept` corroborates nothing. An inference whose material premises are all corroborated facts (none adjudicated) has not stated its inferential step and becomes `unverified`. | `analysis/hypothesis_testing.py` |
| R3 Unassessed contradiction | Passages returned by a contradictory-evidence search that the testing model never labelled contradicting, irrelevant or contextual: flagged, listed in missing information, review required, `supported` capped at `qualified`. Investigation path only; the single-pass path has no per-hypothesis contradictory search to check against. | `bo/investigation.py` |
| R4 Mitigant outcome evidence | "Demonstrated" needs a cited passage that reports a measured change (a figure plus a change word). Lexical heuristic, flagged `heuristic: true`. Applies to v2 and v3 single-pass validation. | `bo/hypotheses.py` |

| Scenario | Kind | Single pass | Investigation, before | Investigation, after |
|---|---|---|---|---|
| S1 payer-category share | control | supported, usable | unverified | **supported, usable** |
| S2 genuine quote → wrong inference | defect | unverified | unverified | unverified |
| S3 correct number → irrelevant claim | defect | supported, **usable** | supported, **usable** | **unverified** |
| S4 absence as safety | defect | unverified | unverified | unverified |
| S5 mitigant without outcome | defect | supported, **usable**; mitigant now *unproven* (R4) | unverified | unverified; mitigant *unproven* |
| S6 ignored contradiction | defect | supported, **usable** | unverified | unverified |
| S7 no volume guarantee, terminable | control | qualified, usable | unverified | **qualified, usable** (R3 flag) |

| Measure | Single pass | Investigation, before | Investigation, after |
|---|---|---|---|
| Defects held back (of 5) | 2 | 4 | **5** |
| Correct findings usable (of 2) | 2 | 0 | **2** |
| Parameters: correct accepted / defective held back | 1 / 0 of 2 | 0 / 2 of 2 | **1 / 2 of 2** |
| Model calls; request / response chars | 1; 10,489 / 4,981 | 4; 46,863 / 23,520 | 4; 46,862 / 23,520 |

Adversarial variants (tests in `tests/integration/test_bo_m1s2_comparison.py`):

* S3 with the metric's concept copied correctly → still `unverified` (R2, undeclared inferential step).
* S2 relabelled as a `reported_fact` → `unverified` (R1 all-or-nothing).
* S7 with "do not" dropped from the premise → `unverified` (R1 negation check).
* `verbatim_fact_support` unit cases: changed figure, non-verbatim quote, dropped negation, added word → all rejected.

Reading this honestly:

* On these seven scripted scenarios, the investigation path now does what the manifest
  asked of it, and the single-pass path still lets three defects through. That shows the
  investigation path's **deterministic controls** are stronger on these error types. It
  does not show that it produces better analysis with a real model.
* I wrote both the scenarios and the fixes. The fixes were designed after seeing the
  first run, so the "after" column is not an independent test. The HCA arm, with
  scenarios written before any further code changes, is the next real check.
* The cost is about 4× the calls and 4.5× the request volume per specialist (scripted
  sizes; real token counts need a live run).
* R4 is lexical. A passage containing an unrelated figure and the word "increased" would
  pass it. It is recorded as a heuristic flag, consistent with the other Layer C/D rules.
* R3 caps conservatively: a passage the model labelled *supporting* still counts as
  unweighed if a contradictory search returned it. This may cost some correct
  `supported` findings (S7 became `qualified`). The intent is for the step 14 challenger
  or a reviewer to resolve it.

## 5a. HCA FY2025 (real 10-K)

Case `HCA_2025_M2`, accession `0001193125-26-044769`; ingestion 180 narrative passages,
1,547 facts; M1S2 sealed packet 17 passages plus 12 payer-category indicators (2025 and
2024). Scenarios and both scripts were committed **before** the first run (`389b265`).
No code changed between that commit and the recorded run.

One correction after registration: the investigation script's enquiries had an empty
`permitted_document_ids`, which allows searching no document (all 18 searches came back
unresolved and testing was rejected). It was set to the 10-K's document id. No scenario,
claim, quote or status was changed.

| Scenario | Kind | Single pass | Investigation |
|---|---|---|---|
| H1 private payers 48.9 % of revenue (category) | control | supported, usable | qualified, usable (R3 cap) |
| H2 predetermined-rate agreements → "lock in volumes" | defect | unverified (Layer C rule) | unverified |
| H3 approved payer-share indicator → "patients are loyal" (concept copied correctly) | defect | supported, **usable** | unverified (R2: inferential step not declared) |
| H4 no >10 % payer disclosed → "concentration is low" | defect | unverified | unverified |
| H5 market position "demonstrably" protects contracts; outcome = ER-volume table | defect | supported, **usable**; mitigant **demonstrated** | unverified; mitigant unproven |
| H6 contracts "secure" despite disclosed renewal risk | defect | supported, **usable** | unverified |
| H7 competitors may negotiate exclusivity (premise says "HCA" for "us") | control | qualified, usable | **unverified** |

| Measure | Single pass | Investigation |
|---|---|---|
| Defects held back (of 5) | 2 | 5 |
| Correct findings usable (of 2) | 2 | 1 |
| Parameters: correct accepted / defective held back | 1 / 0 of 2 | 1 / 2 of 2 |
| Off-dimension hypothesis | n/a | recorded out of scope |
| Step 12 gate | n/a | `blocked_pending_evidence`, 16 gaps |
| Model calls; request / response chars | 1; 53,212 / 6,588 | 4; 272,570 / 28,139 (≈ 5.1× / 4.3×) |

What HCA adds to the synthetic result:

1. **The pattern holds on real text.** The investigation path held back every planted
   defect; the single-pass path let the same three through (H3, H5, H6) and accepted both
   defective parameter values.
2. **R4 is weak on real filings, as anticipated.** The ER-volume table contains figures
   and "increased", so the lexical check accepted it as outcome evidence for an unrelated
   mitigant in the single-pass path. The investigation path did not, because it requires
   a corroborated outcome premise.
3. **R1 is strict.** A correct finding whose premise paraphrased "us" as "HCA" was not
   verbatim support and became `unverified` (H7). One of two controls lost.
4. **R3 fires on everything.** On a 17-passage packet, every contradictory search
   returned its maximum of 5 passages, so every hypothesis was flagged and nothing could be
   `supported` (H1 became `qualified`). As implemented, R3 is too blunt to be informative.
5. **Cost.** About 5× the request volume of the single pass, mostly the passages sent to
   the testing stage.

## 5b. Live runs (HCA FY2025, M1S2, `gemini-3.1-flash-lite` on Vertex)

Tool: `python -m app.services.bo.m1s2_live` (temporary). Per run it writes an HTML and a
PDF transcript of every model call (prompt, inputs, raw output, tokens) and of what the
validators did with each finding, under `output/m1s2_live*/` (git-ignored).

**The first live runs exposed five integration defects that the scripted tests could not**
(fixed in `771ed3d`, `2cf0e2f`, `19ec617`; scripted results unchanged after each fix):

| Live failure | Cause | Fix |
|---|---|---|
| Planning rejected; whole investigation lost | Model omitted mandatory perspectives; planner fails closed | One bounded re-plan naming the omissions (as the single-pass path has) |
| Testing rejected; whole investigation lost | One non-verbatim quote (page number inside a sentence, table rows, spliced text) rejects the stage | Baseline layout-gap recovery per quote; unrecoverable quotes dropped and recorded, not fatal |
| Metric premises never corroborated | Testing payload never listed the approved metrics; model invented ids; claimed entity/scale had no metadata to match | Approved metrics (with entity and unit) sent to the model; prompt explains the grounding fields |
| Correct facts never corroborated | Model (correctly) labelled the hypotheses inferences; verbatim check applied to reported facts only | Verbatim check applies to any factual, non-outcome premise; an inference can then reach at most `partially_supported` |
| Whole output rejected at the end | Parameter values returned without evidence ids | Evidence derived from the cited governed hypotheses |

Two rules were added while doing so, both checked against the unchanged scripted scenarios
(still 5/5 defects held back, 2/2 controls usable on both cases): an interpretive
statement is an inference, not a reported fact (prompt); and a **positive** hypothesis
whose outcome premise is not corroborated is `unverified` (a strength must be
demonstrated; a risk may stand qualified). On HCA, H2 (rates → volumes) is now held back
by the baseline's lexical rule rather than by the investigation governance itself, which
reaches `partially_supported`.

**Final live comparison (3 runs, after the fixes):**

| | Single pass | Investigation |
|---|---|---|
| Hypotheses per run | 3, 3, 2 | 2, 2, 2 |
| Usable per run | 2, 3, 1 | 1, 1, 1 |
| What was found | payer-category concentration (usable in 1 of 3 runs; quote failed in 2); contract renewal / termination risk (every run, as management claim or generic risk); "switching costs not established" (2 of 3 runs, recorded as a *supported* hypothesis) | payer reliance (48.9 %) → bargaining power and margin pressure: `qualified`, every run; Florida/Texas 51 % concentration: `unverified`, every run |
| `customer_dependence` | concentrated_payer_categories in 1 of 3 runs | concentrated_payer_categories in 3 of 3 |
| `contract_termination_risk` | moderate in 3 of 3 (on management-claim / risk-factor findings) | unavailable in 3 of 3 (no finding carries it) |
| Step 12 gate | n/a | `blocked_pending_evidence` every run |
| Average cost / time per run | $0.0071 / 6.6 s | $0.0238 / 27.5 s (≈ 3.4× / 4.2×) |

Observations:

1. **The investigation path is more consistent.** The same two hypotheses, the same
   dispositions and the same parameter values in all three runs; the single pass varied
   in what survived and in its parameters.
2. **It finds less.** Two hypotheses per run, and it never surfaced contract renewal risk,
   which the single pass reported every time (as a disclosed risk, appropriately
   qualified there). Neither arm surfaced competitor exclusivity, price transparency or
   narrow networks, which are in the packet.
3. **Ownership leakage persists.** In every investigation run the model put Florida/Texas
   geographic concentration under Customer Stickiness, which belongs to Module 2, despite
   the prompt saying it is context only. It was held back, but because its premises were
   not verbatim, not because of ownership. No deterministic check enforces dimension
   ownership of the *content* of a hypothesis.
4. **Two instructions were not followed by the live model:** assess every
   contradictory-search passage (the R3 flag fired on every finding) and stay within the
   dimension's remit.
5. **The step 12 gate** reported "blocked" in every run: under the agreed order this is
   what keeps Agent 6 from consuming these findings unqualified.

Caveats: one model, one case, three runs per configuration, no reviewer-verified answer
key (the HCA review manifest is still an unverified template). These are observations,
not a measured quality difference.

## 5c. Breadth increment (option B) — live

Approved 17 Oct. Changes (`33bc1c2`, `42fef15`):

* **Seeded generation:** the generation call receives the specialist's brief (remit and
  boundaries) and an agenda: its "Assess:" topics plus its coverage questions.
* **Deterministic plan completion:** after the one re-plan, a rejected model plan is
  completed mechanically (search intent reset to the canonical intent of its
  requirement kind; omitted mandatory perspectives filled with the labelled template
  enquiries) and must still pass the planner's full gate.
* **Optional testing model:** `m1s2_live --testing-model` runs only the hypothesis-testing
  call on another model (output cap raised to 32k for reasoning models; thinking tokens
  now recorded and priced).

| Configuration (3 live runs each, HCA M1S2) | Hypotheses | Usable | Cost / run | Time / run |
|---|---|---|---|---|
| Single pass (flash-lite) | 3, 4, 2 | 3, 3, 2 | $0.007 | 6 s |
| Investigation, unseeded, flash-lite (§5b) | 2, 2, 2 | 1, 1, 1 | $0.024 | 28 s |
| Investigation, seeded, flash-lite | 4, 3, 1 | 0, 0, 0 | $0.025 | 24 s |
| Investigation, seeded, **testing on gemini-3.1-pro-preview** | 4, 4, 4 | 1, 2, 1 | $0.36 | 145 s |

What changed:

1. **Seeding widened the analysis.** The seeded runs raised what the earlier runs missed:
   rates fixed without volume guarantees, payer consolidation and bargaining power,
   contract renewal pressure from competitors' exclusivity, and narrow / tiered networks.
2. **With flash-lite doing the testing, none of it survived.** The model paraphrases its
   premises and labels interpretations ("high concentration") as facts. The deterministic
   corroboration accepts only verbatim restatement or a correctly linked metric, so
   correct reasoning ends `unverified`.
3. **With a stronger testing model, the material negatives survived as `qualified`
   findings:** network exclusion risk (narrow / tiered networks), payer consolidation and
   bargaining power, competitor exclusivity threatening renewals. Claimed strengths
   ("payer diversification mitigates…") were held back as undemonstrated, as intended.
4. **Still lost in every configuration:** the true reported fact "agreements fix rates but
   do not guarantee volumes", because the model paraphrases it and lists open evidence
   requirements. No parameter value was accepted in the seeded configurations.
5. **Cost.** The stronger testing model costs about 15× the flash-lite investigation and
   50× the single pass per specialist, mostly reasoning tokens; about $0.36 per
   specialist run. A full Module 1 run (five specialists) would be roughly $2.

Spend on live runs: about $2.50 in total (flash-lite runs ≈ $0.60; Pro testing runs
≈ $1.09 recorded plus an earlier batch of three truncated Pro runs whose reasoning tokens
were not yet recorded, estimated at under $1).

## 6. Recommendation and decisions for review

**Current recommendation (after §5c).** The remaining blocker is semantic, not
structural: the deterministic checks cannot recognise a correct paraphrase. The options:

* **R5. A separate entailment judge** for narrative premises: a second, small model call
  that answers only "does this passage state this premise?" for each premise, recorded as
  a judged (not deterministic) corroboration. Daniel's grounding evaluator already has a
  slot for it. It must be tested with positive, negative and adversarial cases (genuine
  quote / wrong inference, dropped negation, changed figure) before it is trusted.
* **Accept the current behaviour** and treat `unverified` material negatives as open
  questions for the reviewer (they are already shown On Demand with the open-question
  flag).
* **Stronger model for testing** (works; ≈ $0.36 per specialist run).

I recommend **R5 with flash-lite as the judge, evaluated offline first on the existing
scenario sets plus new adversarial cases**, then live; and keeping the Pro testing tier
as a configuration option rather than the default.

### Earlier recommendation (after §5b), kept for the record


After the live runs: the investigation path's deterministic controls are stronger and
its output more consistent, but with this model it covers less ground, costs about 3.4×
more per specialist, and does not keep to its dimension on its own. Options:

* **A. Proceed to M3** with the investigation path, adding during M3 a coverage floor
  (the single-pass findings for the same specialist become hypotheses the investigation
  must test) and an ownership check in Agent 6 / the challenger.
* **B. One more M2 increment first** on generation breadth (e.g. seed generation with the
  specialist's retrieval-plan purposes and checklist questions) and a stronger model tier
  for testing only, then re-run live.
* **C. Hybrid:** single pass for breadth, investigation loop only for findings that are
  material or positive.

My recommendation is **B**: breadth is the main live weakness, and it is cheap to measure
with the harness now in place.

R1a and R3a, proposed after the scripted HCA run, were implemented in `44270ad`:

| # | Refinement | Fixes |
|---|---|---|
| R1a | Treat the borrower's names and aliases from the entity record (e.g. "HCA", "HCA Healthcare") as equivalent to "we / us / our" in the verbatim check. Still no paraphrase of content words, figures or negations. | H7 |
| R3a | Require the testing stage to assess every passage returned by a contradictory search (schema rule), and flag only true omissions; a passage the model explicitly weighed is not re-flagged. Supported findings that rest on a passage the model also labelled supporting remain for the step 14 challenger. | R3 firing on every finding |

The original R1–R5 proposal is kept below for reference; R1–R4 are implemented (§5) and
R5 is still open.

| # | Change | Addresses |
|---|---|---|
| R1 | A **verbatim reported-fact channel**: a premise classified as a reported fact is corroborated when its own text is covered by a verified verbatim quote (the BO Layer A quote check plus the quote-coverage check). Inference and outcome premises still need a metric or a human adjudication. | Recall loss (S1, S7) without reopening S2 |
| R2 | **Fail-closed metric links**: `claimed_concept` required and matched; and an *inference* cannot reach `supported` on metric-grounded factual premises alone. | S3 in the investigation path |
| R3 | **Unassessed contradiction flag**: if a contradictory enquiry returned passages that the testing model neither marked contradicting nor irrelevant, flag for review and cap the status at `qualified`. | S6 in both paths |
| R4 | **Baseline mitigant check**: "demonstrated" needs an outcome-type passage, not merely an admitted one (apply to the single-pass validator too). | S5 baseline |
| R5 | Optional, needs budget: an LLM entailment judge (supported by Daniel's grounding evaluator) evaluated live against these scenarios. | Narrative entailment generally |

## 7. Remaining risks and assumptions

* Two cases (synthetic, HCA), seven scenarios each; I wrote both arms' scripts. The "equally wrong
  model" design limits bias, but it is not an independent evaluation.
* The investigation arm's governance is Daniel's; its scripted tests passed before
  integration, but its testing, signal and completeness stages have never run live.
* Materiality in the investigation path uses Daniel's provisional rubric. Magnitude and
  breadth inputs are not derived from data, so most findings fall to `medium` and are
  flagged for human review.
* The baseline arm ran without its bounded retrieval and coverage follow-ups; neither
  would have been triggered by this script.
* Agent 6, challenge, scoring and narrative were not exercised (M3/M4).

## 8. Commits

| Commit | Content |
|---|---|
| `799b227` | M0 assessment |
| `422af03` | Selective import of Daniel's services and tests |
| `4c9e41a` | M1 |
| `ceeb3bb` | M2 harness, scenarios, first results, report |
| `c4edbeb` | Fixes R1–R4, re-run, adversarial tests |
| `389b265` | HCA scenarios and scripts, committed before the first HCA run |
| `846b800` | HCA results, HCA test, report |
| `44270ad` | R1a / R3a |
| `771ed3d`, `2cf0e2f`, `19ec617` | Live harness and transcripts; fixes found by live runs |
| `7fa9544`, `353af39` | Live results in this report |
| `33bc1c2`, `42fef15` | Breadth increment: seeded generation, testing tier, plan completion |
| (this commit) | Thinking-token cost tracking; §5c |
