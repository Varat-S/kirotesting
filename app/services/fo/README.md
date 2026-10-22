# Financial Overview (FO) module — working guide

A deterministic credit scorecard built from one 10-K's Inline XBRL tags. It collects facts with
concept fallbacks, computes seven measures, scores each 1 (lowest risk) to 10 (highest), and
combines them into a composite risk score with a letter rating (AAA … D).

The methodology is an **exact port** of the "10-K with FO" project (`credit_memo_app.py`). On the
FY2025 10-Ks of EHC, HCA, THC and UHS, every ratio, score and composite matched that project. Keep it that
way: change a constant only on purpose, in both places. The exceptions are deliberate and listed under "Measures
and composite" (DSCR's bands, year-1 principal and interest incurred; ICR's bands; the going-concern fail).

It is organised like the Business Overview (BO) module. It has two agents. The **Pre-check Agent** runs
before the measures (going concern, covenants, acquisitions, prepaid model). The **Trigger Agent** runs after
them, when a measure trips its trigger (today DSCR T1). Every number is computed by code. The agents only
classify and quote the filing's passages; code verifies the quotes and re-derives the outcome.

## Run it

| Where | How |
|---|---|
| Browser | `python -m app.preview`, then `/` → "Financial Overview runs" (`/fo`): pick a folder, then Run (needs a live provider: the Run button is disabled without one) |
| CLI | `python -m app.cli run-fo-case [CASE_ID] --filing "HCA 10-K FY2025" --live` (or `--responses agent.json` offline, with `{"going_concern": ..., "covenants": ..., "acquisitions": ..., "prepaid": ..., "dscr_t1": ...}`; `dscr_t1` is needed only when DSCR scores 9 or more); one of the two is required |
| Analyst decision | the run's page (browser), or `python -m app.cli fo-review RUN_ID --decision continue\|stop --reviewer "…" --reason "…"` (going-concern fail), or `fo-override RUN_ID --measure liquidity\|dso\|leverage\|dscr --decision accept\|override [--score N] …` (an escalated measure) |
| Tests | `.venv\Scripts\python -m pytest tests/unit/test_fo_precheck.py tests/unit/test_fo_scoring.py tests/unit/test_fo_measures.py tests/unit/test_fo_covenants.py tests/unit/test_fo_acquisitions.py tests/unit/test_fo_prepaid.py tests/unit/test_fo_disagreements.py tests/unit/test_fo_dscr_trigger.py tests/integration/test_fo_runner_api.py -q` |

**Picking the model.** The run form has a "Pre-check Agent model" dropdown, and the CLI has `--model` (with
`--live`). `models.py` names the candidate Vertex models and offers only the text-analysis ones: no TTS,
transcribe, live, translate or image models, because the pre-checks need structured JSON back. Each is checked with a
free `count_tokens` call, cached for 6 hours, because Vertex can't list models with an API key. Models that return 404
for your key and region are hidden. The default is `VERTEX_MODEL_NARROW`. The pick applies to that run only, and is
recorded as `payload.agent_model` and shown in the runs table.

**Stopping a run.** After Run, the progress panel has a "Stop the analysis" button. It posts the form's run token to
`/fo/run/cancel` (`cancel.py`). The runner checks for it before each pre-check, before scoring and before recording,
then ends with `FoRunCancelled`: no run is recorded, and the page says "Run stopped". A model call already in flight
finishes first, and its `model_runs` row stays logged. The stop registry is in memory, so this works in a
single-process server. On the CLI, Ctrl+C stops a run.

**The Pre-check Agent always interprets the pre-checks.** The keyword rules run alongside it as a cross-check
(a disagreement is noted on the page) and never decide. Without an agent, or when its output is rejected, the run is
refused (`PrecheckAgentError`; the browser shows the error) and nothing is recorded. The model is skipped only
when there is nothing to interpret: no auditor's report and no going-concern language, no covenant search hit, or
no deal note or deal mention.

## Going-concern pre-check

| Result | When (basis in the 10-K) | Effect |
|---|---|---|
| PASS | No statement of substantial doubt, in the auditor's report or management's text. Standard "prepared on a going-concern basis" wording, or risk-factor language with no doubt expressed, is a pass | None: the analysis proceeds |
| WATCH | Management says substantial doubt was raised but alleviated by its plans (a note), with no going-concern paragraph in the auditor's report | The liquidity-and-maturity score is floored at 7 before weighting |
| FAIL | The auditor's report (Item 8 or the F-pages) has a going-concern paragraph, or management concludes substantial doubt exists and is not alleviated (the going-concern or liquidity note) | The pipeline pauses (`awaiting_review`) for an analyst to choose. **Continue**: overall score 10; the measures are shown as "superseded by going-concern fail" and are not counted; agent triggers are skipped (`payload.gate.agent_triggers = "skipped"`). **Stop**: "Not scored: going-concern fail" |

How it decides (`precheck.py`, prompt in `app/prompts/fo_prompts.py`):

1. **Passages** (code):
   - **The auditor's report is always read in full**, every paragraph, from "Report of Independent Registered
     Public Accounting Firm" to "We have served as … auditor since".
   - Every sentence elsewhere (Items 1, 1A, 7, 8, F-pages) containing "substantial doubt" or "going concern" is
     collected and labelled with its section: going-concern or liquidity note, basis of presentation, MD&A
     liquidity, subsequent events, risk factors, or other text. Sections are recognised by their headings, and
     the page lists which ones were located.
   - Statements that cash covers 12 months are collected too.
   - Ids: A01 (auditor), M01 (mentions), F01 (funding).
2. **Agent** (`--live`, or "Pre-check Agent" in the browser): it reads the whole auditor's report and
   classifies the passages, quoting each finding verbatim. It is called whenever there is an auditor's report
   to read. Inputs are about 12–16 KB for a large hospital filer.
3. **Validation**: a finding is held back if:
   - its quote isn't verbatim in the cited passage;
   - an auditor-paragraph finding doesn't come from the auditor's report;
   - a management conclusion comes from the risk factors (Item 1A);
   - a WATCH or FAIL quote doesn't contain "substantial doubt" (so going-concern-basis wording can't carry
     one);
   - a FAIL quote says no doubt exists.

   The status is **re-derived** from the findings that survive, not taken from the agent's stated status.
4. **Rules** (the FO project's keyword classification) always run too, as a cross-check only. When they
   disagree with the agent, the page says so. They never decide.

The model run is logged in `model_runs` (prompt id and hash, raw response). The run folder also gets
`precheck_transcript.json`: the system prompt, the inputs, the raw response, and what validation kept.
The analyst's decision goes in `financial_overview_reviews`: one decision per run, bound to the run's content
hash, never rewritten. The recorded payload never changes. After a decision, `review.json` is added and
`financial_overview.html` is re-rendered.

## Covenant pre-check

Lenders can block revolver draws or demand repayment after a breach. `covenants.py` searches the debt note,
MD&A liquidity, the auditor's report and subsequent events for "waiver", "forbearance", "covenant", "breach",
"not in compliance", "event of default", "cross-default", "amendment" and "relief" (plus "waived" and
"amended"). Outside the debt note, "waiver", "amendment", "relief" and "breach" count only next to a debt word,
so a Medicaid waiver or a data breach doesn't count. Only paragraphs with a hit are read, and no hit means in
compliance (no model call).

| Outcome (first match) | When | Effect |
|---|---|---|
| (a) breach | A breach not waived, or MD&A says a breach is reasonably likely within 12 months | Revolver availability = 0 in every measure (coverage and refinancing); liquidity-and-maturity score = 10. **Escalated**: the run waits at `awaiting_review` while an analyst accepts that score or overrides it |
| (b) relief | A breach waived for more than 12 months after the year-end, or a relief amendment | +1 on the liquidity-and-maturity score after calculation (capped at 10) |
| (c) routine | A routine amendment only (repricing, extension, benchmark-rate change) | No change (resolved) |
| (d) compliant | In compliance, or no hit | No change |
| (e) unclear | A waiver or breach is mentioned, but its status or duration isn't clear, or a waiver lasts 12 months or less | No adjustment, no escalation; an information flag |

The agent (prompt `fo_precheck_covenants`) classifies each paragraph. It quotes verbatim, gives a waiver's end
date or length, and lists any disclosed covenant headroom. Code then checks every finding:
- a breach quote must state an actual breach, not a hypothetical one, and must not say it was waived;
- a likely breach must be in Item 7;
- a waiver's date or length must appear in its quote, otherwise the waiver is read as (e);
- headroom levels must appear in their quote.

Code then re-derives the outcome. Every search hit goes to the agent. The keyword rules only cross-check it (and
don't extract headroom).

With a going-concern WATCH as well, the floor of 7 applies to the covenant-adjusted score. On a going-concern
FAIL the measures are superseded and only the continue/stop decision is escalated.

## Acquisitions and divestitures pre-check

Year-end receivables include the whole acquired business, but revenue covers only part of the year: DSO is
overstated after an acquisition and understated after a divestiture. `acquisitions.py` reads the acquisitions or
dispositions note, the discontinued-operations and held-for-sale notes in full (tables as rows, cells joined with
" | ", with the "in millions" scale in force), subsequent events that mention a deal, and strong deal mentions
elsewhere in the notes ("pro forma", "discontinued operations", "held for sale", "divest…", "business
combination"). Nothing found: no deal, no model call. The cash flow statement's "Acquisition of…" line opens nothing:
a section opens only inside the notes.

The agent (prompt `fo_precheck_acquisitions`) lists each deal once, with verbatim evidence per fact: the deal, its
date, discontinued operations / held for sale, materiality, price or proceeds, pro forma revenue, divested revenue,
debt repaid from the proceeds, and any EBITDAR parts the 10-K discloses. Code checks every fact (verbatim quote; the
amount and the date must be in the quote; pro forma revenue must be at least the reported revenue), derives the
timing from the verified date, and re-derives each deal's outcome:

| Per deal, first match | Adjustment |
|---|---|
| A prior-year deal, or an acquisition after the year-end | None (an after-year-end acquisition is an information flag) |
| Acquisition with pro forma revenue disclosed | DSO on revenue + (pro forma − reported); an aggregate pro forma figure counts once |
| Divestiture presented as discontinued operations | None: revenue and receivables already exclude it |
| Divestiture not in discontinued operations, revenue disclosed | DSO on revenue − divested revenue (completed in the year, or held for sale at the year-end) |
| Not material (no pro forma required) | None |
| Material, revenue not disclosed | **Unresolved**: DSO left as reported and escalated (`measure_override`) |

A divestiture after the year-end adjusts debt (proceeds repaid) and EBITDAR (divested EBITDAR) together, never one
side. If the divested EBITDAR can't be built from the 10-K (EBITDAR; EBITDA + rent; or EBIT + D&A + rent, rent not
disclosed taken as 0) or the debt repaid isn't disclosed, neither is adjusted and Debt/EBITDAR is unresolved and
escalated. ASC 805 pro forma gives revenue and earnings, not EBITDAR: an acquisition's missing months of EBITDAR are
added only if the 10-K discloses them.

Materiality: as the filing states it; presenting pro forma information counts as material. When the filing says
nothing, a deal whose price or proceeds are under 5% of total assets (`MATERIALITY_SHARE`) is not material; with no
price it is treated as material. Only the latest year is adjusted (the prior year stays as reported). Each deal is
recorded once in `payload.precheck.acquisitions.deals`, with `shared_with` naming Debt/EBITDAR, DSO and the 25%
key-input trigger (not built yet). The DSO and Debt/EBITDAR columns keep `revenue_reported` / `ebitdar_reported` /
`adj_debt_reported`.

## Prepaid-model pre-check

When customers pay in advance, receivables are small and DSO says little about credit quality. `prepaid.py`
applies when the company has a prepaid or mixed business model, as the agent reads it from Item 1 (there is no
list), or its contract liabilities are at least its receivables (tagged `ContractWithCustomerLiability` /
`DeferredRevenue`, else the revenue note's figure).

The agent (prompt `fo_precheck_prepaid`) reads every filing's business description (Item 1, B01…) and revenue note
(R01…, tables as rows), and nothing else: the business model (prepaid / mixed / billed), revenue by payer (each line
billed or prepaid, current and prior year) and contract liabilities. Code checks every quote is verbatim from the
right place:
- a prepaid model needs an Item 1 quote saying customers pay in advance ("in advance", "prepaid", "entrance fee",
  "monthly fee", "deposit", "subscription"…);
- a mixed model also needs one saying services are billed (billed, reimbursed, Medicare, Medicaid, insurers,
  payers…);
- every amount must be a whole number in its quote, and each payer line must name its payer.

Code then re-derives the outcome. The only run without a model call is a filing with neither Item 1 nor a revenue
note. HCA and NVIDIA read as billed: not applicable.

| If a prepaid or mixed model, or contract liabilities ≥ receivables (first match) | DSO |
|---|---|
| Revenue split by payer disclosed | Recomputed on billed revenue for both years; factor 1 (normal weight). Without the prior year's billed revenue the change isn't scored |
| Not disclosed, contract liabilities ≥ receivables | Factor 0 |
| Not disclosed, mixed model | Factor 0.5 |
| Otherwise | **Unresolved**: DSO as calculated, escalated (`prepaid:dso`; merged with an acquisitions DSO escalation) |

The composite sets DSO weight = 7.5% × factor and scales every other weight by (100 − DSO weight) ÷ (100 − 7.5).
The factor replaces the old half-weight rule for DSO under 15 days, which still applies when the check doesn't.

## Trigger Agent: DSCR T1

A trigger agent runs after the measures are scored, when its measure trips the trigger. It runs only when
`payload.gate.agent_triggers` is "enabled": after a going-concern FAIL it is skipped (`payload.triggers.dscr_t1.status
= "skipped"`). `dscr_triggers.py`, prompt `fo_trigger_dscr_t1`.

| Trigger | Why | What the agent checks |
|---|---|---|
| DSCR headline score ≥ 9 (the forward column first: DSCR below 0.5x) | Cash flow can't cover debt service | T1a: is year-1 principal commercial paper, revolver borrowings, or a 364-day or securitisation facility, that is rolled over? T1b: were year-1 maturities refinanced after the year-end? |

**The inputs T1 works on** (the DSCR measure, `measures.py`):
- Year-1 principal = scheduled amortisation in year 1 of the debt maturity table + the current finance-lease
  principal (`FinanceLeaseLiabilityCurrent`), unless the table's concept family already includes leases. 364-day and
  securitisation facilities maturing within 12 months stay in principal unless a committed term runs beyond 12 months.
  A 10-K tags only its own year-end's maturity table, so the historical columns, and a filing with no year-1 tag, fall
  back to the current portion of debt (`principal_basis = "current_portion"`).
- Interest = interest incurred (expensed + capitalised): the tagged `InterestCostsIncurred`, else the income-statement
  expense + `InterestCostsCapitalized` (none tagged = none). ICR uses the same figure.

The agent reads the debt note and MD&A liquidity (paragraphs and tables that mention commercial paper, the revolver,
364-day or securitisation facilities, short-term borrowings, maturities, backstop capacity, classification as
long-term or refinancing) and every subsequent-events paragraph (D01…). It is given the DSCR column and the year-1
principal the engine used, with its source. Code checks every fact (verbatim quote; the amount and the date must be in
the quote) and re-derives the two adjustments, which are independent of each other:

| Finding (verified) | Adjustment |
|---|---|
| T1a: rollover debt in year-1 principal, with undrawn committed backstop capacity ≥ the amount removed that matures more than 12 months out | Input correction: removed from year-1 principal, DSCR recomputed (ratio and band score) |
| T1a: otherwise (no backstop, too small, or maturing within 12 months) | No adjustment |
| T1b: the debt was already classified as long-term at the year-end (ASC 470-10-45-14) | No adjustment (checked first) |
| T1b: year-1 maturities refinanced after the year-end (executed) | −2 on the DSCR score (not below 1), after any recompute |
| T1b: only a stated plan to refinance | No adjustment, because a plan isn't executed; the reason cites the passage |

What T1a needs:
- A quote showing the balance is rolled over (reissued, issued from time to time, repaid and reborrowed, renewed…).
- One backstop quote giving both the undrawn committed capacity and the date it matures, more than 12 months after the
  year-end. A 364-day or securitisation facility maturing within 12 months is not its own backstop.
- Capacity ≥ the amount: each item comes out whole or not at all (no partial removal). Items on the same backstop
  (commercial paper and revolver drawings) must fit within it together, up to the largest backstop shown.
- Commercial paper is in year-1 principal only when principal includes short-term borrowings (Debt, Current, on the
  current-portion fallback). The maturity table's scheduled amortisation doesn't include it.
- Nothing is removed beyond year-1 principal.

What T1b needs:
- A reclassification quote ("classified as long-term", "intent and ability to refinance"…) makes it "reclassified":
  no adjustment. A reclassification claim whose quote doesn't say so is held back, and the item is checked as a
  refinancing.
- Executed: a quote dated after the year-end that says it was done (issued, redeemed, extended…) and has no plan word
  ("intend", "plan", "expect", "may", "will"…). A claimed execution whose quote reads as a plan gets no adjustment.
- A verified quote that dates the refinanced debt within the 12 months after the year-end. Without that it is "not
  year 1": no adjustment.
- The amount is optional. It is shown with its share of year-1 principal; there is no minimum share today.
- The refinancing record is kept once in `payload.triggers.dscr_t1.refinancings` and shared with Liquidity T2
  (`shared_with`), which should read it rather than re-check.

A trigger **escalates DSCR only when it is unresolved** (`payload.triggers.dscr_t1.unresolved`): the agent's stated
answer and the outcome its verified evidence supports disagree, in either direction. That is `dscr_t1:dscr`, a
`measure_override` marked as a disagreement: the analyst accepts the engine's score or overrides it. When they agree,
the verified outcome stands with no analyst step: an adjustment is applied, and with no adjustment (or a plan only)
the DSCR score stays as calculated. With no passage to read, the agent isn't called, there is no adjustment and
nothing is unresolved. The DSCR column keeps `principal_reported`, `debt_service_reported` and `trigger_adjustment`
(`calculated`, `recomputed`, `refinancing`, `score`), and the composite is recomputed on the adjusted score. The keyword cross-check (commercial paper, backstop, a refinancing in subsequent events) only
adds notes. The agent's exchange is written to `trigger_transcript.json`.

## When the agent and the verified evidence disagree

The verified outcome always drives the measures. But when the agent's own answer is **more severe** than the outcome
its verified evidence supports, the analyst decides (`runner.disagreements`):

| Agent said | Verified gives | Escalated |
|---|---|---|
| Going concern FAIL | WATCH or PASS | A run decision: `use_verified` (the verified result stands), or `continue` / `stop` (side with the agent's FAIL: overall score 10, measures superseded / not scored) |
| Going concern WATCH | PASS | Liquidity (the watch floor wasn't applied) |
| Covenant breach or relief | An outcome with less effect | Liquidity |
| A deal unresolved | Resolved, or the deal held back | DSO, and Debt/EBITDAR for a divestiture after the year-end |
| Prepaid model unresolved | Another outcome | DSO |
| DSCR T1: any answer | A different outcome (either direction) | DSCR (T1 is unresolved) |

A measure gets one decision even when several checks escalate it: the escalation merges their reasons (`checks`).
When the engine is the more severe side it already escalates. The run completes once every escalation is decided.
The page lists the held-back quotes, so the analyst can see why the two differ. `payload.gate.disagreements` lists
the escalation ids.

**Analyst overrides.** The run's `escalations` list holds what waits for an analyst. A `measure_override` (today:
liquidity after a covenant breach; DSO or Debt/EBITDAR after an unresolved deal; DSCR after an unresolved trigger T1) is decided
with `fo-override RUN --measure liquidity|dso|leverage|dscr --decision accept|override [--score N]`, or with the form
on the run's page.
The decision goes in `financial_overview_overrides`: one per measure per run, never rewritten. The analyst's score
replaces the measure's score in the composite, which is re-weighted on it, and no floor applies. The recorded
payload never changes. The run folder gets `overrides.json`, and the HTML is re-rendered.

Input is a folder in `edgar_filings/` (project root), one per 10-K; see `edgar_filings/README.md`.

Output, as BO writes `<output>/bo/<case>/<run>/`: every run writes
`<output>/fo/<case_id>/<run_id>/financial_overview.json` (the recorded payload, canonical JSON) and
`financial_overview.html` (the standalone scorecard). `<output>` is `--output` for the CLI (default
`output`) and `OUTPUT_DIR` for the browser (`python -m app.preview` sets it to `output/browser_demo`).
The run is also recorded in the database, and its page offers the same two files as downloads.
`CASE_ID` defaults to `TICKER_FY<year>` from the cover page.

## Run flow

```
loader.load(folder)              edgar_filings/<folder> -> parsed filing (cached by file signature)
  parsing.load_filing            instance .htm, .xsd, _lab/_pre/_cal, Item text, company.json
precheck.going_concern_precheck  passages -> agent (required) -> validation -> pass / watch / fail
covenants.covenant_precheck      search hits -> agent (required) -> validation -> outcome (a)-(e)
acquisitions.acquisitions_precheck  deal notes -> agent (required) -> validation -> per-deal outcome
measures.build_financial_overview  DSCR, ICR, Debt/EBITDAR, RCF/debt, liquidity, EBITDAR margin, DSO
prepaid.prepaid_precheck         Item 1 + revenue note -> agent (required) -> validation -> factor
acquisitions.apply_deal_adjustments  latest-year DSO revenue, Debt/EBITDAR debt and EBITDAR
prepaid.apply_prepaid_adjustments    DSO on billed revenue; the DSO weight factor for the composite
industry.classify_industry       ticker / company name -> industry group (config/financial_overview)
scoring.scoring_settings         that group's benchmarks and p; defaults for m, Δ, cap, D, weights
scoring.score_financial_overview benchmark curve + fixed bands; covenant adjustment on liquidity;
                                 composite_risk (with the going-concern gate) first
dscr_triggers.dscr_t1_trigger    DSCR score ≥ 9 and triggers enabled -> passages -> agent (required) ->
                                 validation -> principal removed / −2 / held
dscr_triggers.apply_t1           DSCR recomputed on the headline column; composite_risk again
runner.FinancialOverviewRunner   records one financial_overview_runs row (payload + content hash);
                                 anything in payload.escalations is recorded as awaiting_review
review.review_run                the analyst's continue / stop decision (financial_overview_reviews)
review.override_measure          the analyst's score for an escalated measure (financial_overview_overrides)
```

## Module map

| File | Responsibility |
|---|---|
| `parsing.py` | Port of FO's filing parser: file discovery, linkbases, contexts/units/facts, sections, Item split |
| `measures.py` | Concept fallbacks, fixed bands, every measure, `build_financial_overview` |
| `scoring.py` | Benchmark curve (`ratio_gap`, `points_gap`, `gap_score`), settings, composite |
| `industry.py` | Industry mapping, benchmarks, reference-data hash |
| `loader.py` | `edgar_filings/` listing, safe folder resolution, size limit, parse cache |
| `precheck.py` | Going-concern passages, keyword rules, the Pre-check Agent call, validation, backends |
| `covenants.py` | Covenant search, keyword rules, the Pre-check Agent call, waiver-length and headroom checks |
| `acquisitions.py` | Deal passages, keyword signals, the Pre-check Agent call, evidence checks, per-deal rules, DSO and Debt/EBITDAR adjustments |
| `prepaid.py` | Item 1 and revenue-note passages, the business-model check, the Pre-check Agent call, evidence checks, billed-revenue DSO and the DSO weight factor |
| `dscr_triggers.py` | Trigger Agent, DSCR T1: debt-note / MD&A / subsequent-events passages, the agent call, evidence checks, principal correction and the −2 refinancing relief |
| `review.py` | The analyst's decisions: continue / stop (`review.json`), measure overrides (`overrides.json`) |
| `runner.py` | `FinancialOverviewRunner` (`build`, `run_filing`), JSON-safe payload, case ids, run state |
| `workbench.py` | Read-only views: `fo_runs`, `fo_view`, `fo_artifact` |
| `reporting.py` | Scorecard view model and the standalone HTML page |

Routes: `app/api/financial_overview.py`. Templates: `fo_workbench.html.j2`, `fo_scorecard.html.j2`,
`fo_overview.html.j2`. Tables: `financial_overview_runs`, `financial_overview_reviews` and `financial_overview_overrides`
(`FinancialOverviewRunRow`, `FinancialOverviewReviewRow` in `app/models/orm.py`).

## Measures and composite

| Measure | Scored by | Composite weight |
|---|---|---|
| Debt / EBITDAR, net (composite leverage) | benchmark curve | 25% |
| Liquidity and debt maturity (worse of coverage and refinancing) | fixed bands | 20% |
| RCF / debt | benchmark curve (points) | 15% |
| EBITDAR margin | benchmark curve (points) | 12.5% |
| DSCR (forward column first) | fixed bands | 10% |
| ICR | fixed bands | 10% |
| DSO change | benchmark curve (days) | 7.5% (half below 15 days) |

The weights are re-normalised over the measures that have a score. There is no composite without
leverage and liquidity, except that a going-concern fail always gives 10. If leverage, liquidity or DSCR
scores 9 or more, the composite is at least 7.

DSCR bands (this project's own since 2026-10-22, not the FO project's): ≥ 2.0x → 1, 1.5–2.0x → 2, 1.25–1.5x → 3,
1.0–1.25x → 5, 0.5–1.0x → 7, above 0x and below 0.5x → 9, 0x or below → 10. Each band includes its lower bound. So
DSCR scores differ from the FO project's for ratios below 2.0x, and so may the composite. DSCR's year-1 principal
(the maturity table's year 1 + current finance-lease principal) and the interest DSCR and ICR use (interest incurred:
expensed + capitalised) are this project's own too (2026-10-22), so DSCR and ICR differ for filers that tag a year-1
maturity or capitalised interest.

ICR bands (this project's own since 2026-10-22, set for interest incurred): ≥ 12x → 1, 8–12x → 1.5, 6–8x → 2.5,
4–6x → 3.5, 3–4x → 5, 2–3x → 6, 1.5–2x → 7.5, 1.0–1.5x → 8.5, below 1.0x → 10. Each band includes its lower bound.

Another deliberate difference from the FO project: on a going-concern fail, the FO project also set the
liquidity row to 10. Here every row keeps its own score for display, is marked superseded and is not counted
(no weights, no contributions). The composite is 10 either way.

## Not yet ported (planned extras)

* A Scoring settings form. `scoring_settings(args, group)` already parses the FO overrides; it is called with `{}` today.
* Comparison years (earlier 10-Ks for DSO). The `days_sales_outstanding(..., earlier=)` hook is in place.
* Debt schedule CSV override.
* Further pre-checks (only going concern exists). A pass or watch run carries `human_review_status = "pending"`;
  only a fail requires a decision today.

## Things that will trip you up

* **The FO parser is separate from `app/services/extraction/sec`.** That parser is stricter and keeps a
  different fact shape, so FO uses its own port to guarantee identical figures. Do not switch FO to it
  without re-running the parity check.
* **Only "Hospitals/Healthcare Facilities" has benchmarks.** Any other company gets "no industry
  benchmark" on the four benchmark-scored measures. With no leverage score there is no composite.
* **Infinite coverage** (nothing falls due) is stored as the string `"inf"` (`runner.json_safe`).
* **Every `*.json` in `config/financial_overview/` is read as a mapping file.** Files of another shape are skipped.
