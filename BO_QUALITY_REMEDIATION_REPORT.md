# BO six-agent quality remediation — report

Branch `feat/bo-quality-remediation` (from `spec/agentic-analysis` at `c8a20c1`).
Scope: the six Business Overview specialists and their validators. The
orchestrator, challenger, score and memo are untouched and were not run.

## Status

All six specialists run live on the HCA FY2025 10-K and pass validation; the
three reports and the structured JSON are generated; 1,017 tests pass.

Not established by this work:

* **Model quality in general.** One model (`gemini-3.1-flash-lite`), one filing,
  one run per configuration. Four live runs on the same day gave 14, 24, 22 and
  23 usable hypotheses, so differences between two runs are not a result.
* **Logical correctness of accepted findings.** The new checks are lexical and
  structural. They hold back known over-readings; they do not prove that a claim
  that passes follows from its quote.
* **The HCA review manifest** is still an unverified template; no reviewer has
  checked the findings against the filing.

## What the reconnaissance found

| Reported problem | Actual cause |
|---|---|
| Common instructions told every agent to emit BO1's parameters | Display bug in the temporary PDF script (it printed BO1's prompt as "common"). Runtime prompts were correct. Prompts are now built as *specialist block + shared rules*, and the shared rules contain no parameter name. |
| Florida/Texas quote unverifiable | The quote was genuine. The filing's text has a page number inside the sentence ("…for the year ended 49 December 31, 2025"). |
| Galen, revenue per equivalent admission, payer mix not analysed | Retrieval: the passages existed in the admitted evidence but never reached the specialists (Galen College reached BO4 and was ignored). |
| Indicators unused | All six agents got the same four numbers, none specific to their remit. |

## Changes

| Area | Change | Module |
|---|---|---|
| Prompts | Specialist block (role, own parameters, id prefix) + identical shared rules; specialist guidance rewritten for the listed traps | `app/prompts/bo_prompts.py` |
| Categorical values | Permitted value set for every parameter; `not_established` always available; sentences rejected | `bo/plans.py`, `gate_parameters` |
| Layer A/B | Quote verification with one bounded recovery attempt; a mis-bound number invalidates that hypothesis | `bo/recovery.py`, `bo/hypotheses.py` |
| Layer C | Nine negation-aware rules for unsupported inferential steps, each recorded as a heuristic review flag | `bo/inference_rules.py` |
| Layer D | Evidence basis re-derived from verified quotes (company-specific, management statement, generic risk, general statement); generic-risk-only and quote-does-not-cover-claim findings are qualified; company-specific parameter values need an established company-specific finding | `bo/hypotheses.py` |
| Retrieval | Sector checklist (healthcare provider) and case coverage tests guarantee passages reach the assigned specialist; one bounded coverage follow-up | `bo/sectors.py`, `bo/retrieval.py`, `bo/runner.py` |
| Indicators | Curated per specialist; payer-category revenue shares, salaries and supplies to revenue, change in implicit price concessions, current and prior year | `bo/indicators.py` |
| BO6 | Needs a transmission mechanism; cannot exceed the certainty or materiality of its components; rejected or unverified components cannot be premises; debt-capacity claims are held back and become FO handoffs | `bo/hypotheses.py` |
| Demonstration | `run-bo-specialists` command; executive summary, six-specialist comparison (HTML + PDF), validation audit, benchmark, JSON | `bo/demo.py`, `bo/demo_report.py`, `bo/benchmark.py` |

No borrower name appears in generic code (tested). Galen is a case coverage test
in `examples/healthcare/hca-2025/coverage_checks.json`.

## Demonstration

`output/bo_live/bo/HCA_2025_LIVE/ar_aaf78c0cf54943da/` (git-ignored):

* `executive_summary.html`
* `specialists_comparison.html`, `specialists_comparison.pdf`
* `validation_audit.html`
* `benchmark.html`
* `specialists_run.json`, `baseline_revalidated.json`

The executive summary is assembled from validated hypotheses quoted as written;
no model wrote it and it contains no rating or debt-capacity view.

## Old versus new (HCA FY2025, same model)

| Measure | Baseline `ar_ea76a14fa23c4fa5` as recorded | Baseline responses under the new validators | New run `ar_aaf78c0cf54943da` |
|---|---|---|---|
| Specialists validated | 6 | 6 | 6 |
| Hypotheses generated | 23 | 23 | 23 |
| Usable after validation | 21 | 19 | 23 |
| Established facts | 8 | 3 | 10 |
| Attributed to management | 5 | 4 | 2 |
| Qualified | 8 | 12 | 11 |
| Not usable | 2 | 4 | 0 |
| Unsupported-inference flags | not checked | 4 | 0 |
| Evidence-sufficiency flags | not checked | 10 | 7 |
| BO/FO boundary violations | not checked | 1 | 0 |
| Citation failures / recovered / unresolved | 2 / 0 / 2 | 2 / 2 / 0 | 5 / 5 / 0 |
| Usable hypotheses bound to an indicator | 0 | 0 | 4 |
| Coverage questions with a validated answer | not checked | 0 of 10 | 10 of 10 |
| Parameter values accepted / held back | n/a | 5 / 20 | 20 / 0 |
| FO handoffs | 0 | 1 | 1 |
| Model calls | 6 | 0 (replay) | 7 |
| Tokens in / out | 71,151 / 17,221 | same responses | 120,468 / 22,067 |
| Run time | not recorded | 3.7 s | 29.9 s |

Cost is not determinable here: no price was supplied, so token counts are
reported. Pass `--price-in` / `--price-out` to compute it.

Reading the table:

* The middle column isolates the validator. The same 23 baseline responses now
  yield 3 established facts instead of 8, one debt-capacity claim is held back,
  both failed quotes are recovered, and 20 free-text or unsupported parameter
  values are held back.
* The right column adds the prompt, retrieval and indicator changes. All ten
  coverage questions have a validated answer, including Galen, rates versus
  volumes, and revenue per equivalent admission versus mix.
* More usable hypotheses in the new run is not counted as an improvement.

Baseline problems:

| Baseline problem | Now |
|---|---|
| BO5-H003 geographic concentration unverified | Recovered (page number inside the sentence); established, with denominator and period in the quote |
| BO2-H003 treated as fact before reclassification | Still handled by reclassification; the belief patterns now also cover "we strive", "committed to" |
| BO1-H003, BO3-H001, BO3-H005 needed qualification | Still qualified when they recur |
| BO6-H001 concluded on debt capacity | Held back by rule when replayed; not asserted in the new run; BO6 raised an FO handoff instead |
| Galen missing | Addressed (BO4-H006): a programme to address nursing shortages, effectiveness unproven |
| Rates versus volumes not addressed | Addressed (BO1-H002, BO2-H002) |
| Revenue per equivalent admission versus pricing power | Addressed (BO3-H002): price not separable from mix |
| Indicators not used | 4 usable hypotheses bound to an indicator |
| Verbose free-text parameter values | None accepted; all values are from the permitted sets |

## Live runs performed

| Run | Result | What it exposed |
|---|---|---|
| `ar_ea76a14fa23c4fa5` (11 Oct) | baseline | — |
| `ar_3d4ca2d0d0f24f4a` | BO3 and BO6 rejected whole | One mis-bound number and one answer to an unasked checklist id discarded entire outputs. Changed to per-finding handling. |
| `ar_443610d3a9014642` | 6 of 6 | A genuine quote attached to a claim it did not state was accepted as fact. Added the quote-coverage check. |
| `ar_831823910c854d84` | BO6 rejected | Validator defect: BO6's parameters were rejected for citing BO1–BO5 hypothesis ids. Fixed. |
| `ar_aaf78c0cf54943da` | 6 of 6 | Final demonstration, produced by the committed code. |

The handoff asked for one final live run. There were four after the baseline,
because each of the first three exposed a defect that offline fixtures had not.
All are recorded in `data/bo_live.db`.

## Tests

1,017 passed, 4 skipped, 0 failed (was 969 / 3 before this branch).

* `tests/unit/test_bo_remediation.py` — 39 tests: prompt/schema, BO1–BO6 traps,
  recovery, indicators, coverage.
* `tests/integration/test_bo_specialists_demo.py` — 10 tests, one of them the
  live integration test (skipped unless `BO_LIVE_TEST=1`).
* Existing BO tests updated where the contract tightened.
* `tests/conftest.py` now stops tests reading a developer's `.env`. Two existing
  Vertex tests failed once a real key was present locally, and printed it.

Skips: no PDF backend for the main memo, the unverified HCA manifest, a template
check that no longer applies, and the live test.

## Limitations

1. **Entailment is not checked.** A fact is accepted when its quote is verbatim
   and shares wording with the claim. In the final run BO2-H002 ("contracts fix
   rates but do not guarantee volumes") is established on that basis; whether
   the filing states the absence of a guarantee needs a reviewer or the
   challenger.
2. **Indicator relevance is not checked.** BO3-H002 binds overall revenue growth
   to a statement about price versus mix. The number is correct; its relevance
   is the model's judgement.
3. **Layer C rules were not triggered in the final run**, because the model
   avoided those claims. They are exercised by tests and by the baseline replay.
4. The inference and sufficiency rules are English pattern lists and can be
   evaded by unusual wording; flags are marked heuristic.
5. Revenue per equivalent admission, admissions and state shares are text
   figures, admissible only as quotes. Nothing is calculated from them.
6. The healthcare checklist is the only sector profile. Stryker correctly gets
   none.
7. Retrieval is keyword-based over 2,000-character passages.
8. Citation failures rose from 2 to 5 in the new run (all recovered): the model
   often quotes a sentence from a neighbouring passage or across a page break.

## Before connecting to the wider pipeline

* The full `run-bo-case` path uses the same specialists and validators, but the
  orchestrator and challenger prompts do not yet know about `evidence_basis`,
  review flags or FO handoffs. Pass them through and add them to the memo.
* Rubric-scored parameters are now often `not_established` (competitive
  position, key-person dependency), so BusinessRiskScore will more often be
  `unavailable`. That is the intended honest outcome; decide whether the
  scoring coverage rules should change.
* FO handoffs are recorded on hypotheses but nothing consumes them yet.
* Have a reviewer verify `examples/healthcare/hca-2025/review_manifest.json`
  against the filing, then run the evaluation with it.

## Reproduce

```powershell
.venv\Scripts\python -m pytest -q

# offline replay of the final live responses (no model cost)
.venv\Scripts\python -m app.cli --database data/bo_live.db --output output/bo_live `
    run-bo-specialists HCA_2025_LIVE --package examples/healthcare/hca-2025/package.json `
    --coverage examples/healthcare/hca-2025/coverage_checks.json `
    --replay output/bo_live/bo/HCA_2025_LIVE/ar_aaf78c0cf54943da/specialists_run.json

# live (provider cost); needs .env with LLM_PROVIDER, key and model names
.venv\Scripts\python -m app.cli --database data/bo_live.db --output output/bo_live `
    run-bo-specialists HCA_2025_LIVE --package examples/healthcare/hca-2025/package.json `
    --coverage examples/healthcare/hca-2025/coverage_checks.json --live `
    --baseline output/bo_live/bo/HCA_2025_LIVE/ar_ea76a14fa23c4fa5/specialists_calls.json
```

Replaying needs the same case id and database as the recorded run, because
evidence ids are case-specific.
