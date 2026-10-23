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
them, when a measure trips its trigger (today DSCR T2 and T3, then DSCR T1; ICR T2 and T3, then ICR T1; then Debt/EBITDAR T4, T1, T2 and T3; EBITDAR Margin T1 and T2; then RCF/Debt T1-T4; then DSO T1; then Liquidity T1-T3). Every number is computed by code. The agents only
classify and quote the filing's passages; code verifies the quotes and re-derives the outcome.

## Run it

| Where | How |
|---|---|
| Browser | `python -m app.preview`, then `/` → "Financial Overview runs" (`/fo`): pick a folder, then Run (needs a live provider: the Run button is disabled without one) |
| CLI | `python -m app.cli run-fo-case [CASE_ID] --filing "HCA 10-K FY2025" --live` (or `--responses agent.json` offline, with `{"going_concern": ..., "covenants": ..., "acquisitions": ..., "prepaid": ..., "dscr_t1": ..., "dscr_t2": ..., "dscr_t3": ..., "icr_t1": ..., "icr_t2": ..., "icr_t3": ..., "leverage_t1": ..., "leverage_t2": ..., "leverage_t3": ..., "leverage_t4": ..., "rcf_t1": ..., "rcf_t2": ..., "rcf_t3": ..., "rcf_t4": ..., "margin_t1": ..., "margin_t2": ..., "dso_t1": ..., "liquidity_t1": ..., "liquidity_t2": ..., "liquidity_t3": ...}`; `dscr_t1` is needed only when DSCR scores 9 or more, `dscr_t2` only when CapEx is above 1.5× depreciation, `dscr_t3` only when the cash-tax gap is above 5% of CFADS, `icr_t1` only when ICR scores 8.5 or more, `icr_t2` only when ICR's interest is from a net or operating tag, `icr_t3` only when ICR's EBIT is operating income, `leverage_t1` only when net one-offs are above 5% of EBITDAR or EBITDAR is zero or negative, `leverage_t2` only when the gross and net leverage scores are 2 or more apart, `leverage_t3` only when the leverage score with leases is 2 or more worse than without, `leverage_t4` only when total debt is from a fallback: the tagged total skipped or finance leases added, `rcf_t1` only when RCF / debt scores 9 or more or is negative, `rcf_t2` only when tagged one-off inflows are above 5% of FFO or FFO is up 25% or more and over 10 points faster than revenue, `rcf_t3` only when NCI distributions aren't tagged under a standard concept and NCI is material, `rcf_t4` only when NCI distributions come from PaymentsToMinorityShareholders, `margin_t1` only when Debt/EBITDAR T1 removed one-off items, `margin_t2` only when the EBITDAR margin scores 9 or more or 2 or less and a segment with 30% or more of revenue has a margin 5 points or more from all segments', `liquidity_t1` only when the revolver availability at the year-end isn't tagged or found in the 10-K text, `liquidity_t2` only when the coverage or refinancing score is 9 or more after Liquidity T1, `liquidity_t3` only when the largest year of maturities in years 2–5 is 25% or more of total debt); one of the two is required |
| Analyst decision | the run's page (browser), or `python -m app.cli fo-review RUN_ID --decision continue\|stop --reviewer "…" --reason "…"` (going-concern fail), or `fo-override RUN_ID --measure liquidity\|dso\|leverage\|dscr\|icr\|rcf --decision accept\|override [--score N] …` (an escalated measure) |
| Tests | `.venv\Scripts\python -m pytest tests/unit/test_fo_precheck.py tests/unit/test_fo_scoring.py tests/unit/test_fo_measures.py tests/unit/test_fo_covenants.py tests/unit/test_fo_acquisitions.py tests/unit/test_fo_prepaid.py tests/unit/test_fo_disagreements.py tests/unit/test_fo_dscr_trigger.py tests/unit/test_fo_dscr_t2.py tests/unit/test_fo_dscr_t3.py tests/unit/test_fo_icr_trigger.py tests/unit/test_fo_icr_t2.py tests/unit/test_fo_icr_t3.py tests/unit/test_fo_leverage_trigger.py tests/unit/test_fo_leverage_t2.py tests/unit/test_fo_leverage_t3.py tests/unit/test_fo_leverage_t4.py tests/unit/test_fo_rcf_trigger.py tests/unit/test_fo_dso_trigger.py tests/unit/test_fo_rcf_t2.py tests/unit/test_fo_rcf_t3.py tests/unit/test_fo_rcf_t4.py tests/unit/test_fo_margin_trigger.py tests/unit/test_fo_liquidity_trigger.py tests/unit/test_fo_liquidity_t2.py tests/unit/test_fo_liquidity_t3.py tests/integration/test_fo_runner_api.py -q` |

**Picking the model.** The run form has a "Pre-check Agent model" dropdown, and the CLI has `--model` (with
`--live`). `models.py` names the candidate Vertex models and offers only the text-analysis ones: no TTS,
transcribe, live, translate or image models, because the pre-checks need structured JSON back. Each is checked with a
free `count_tokens` call, cached for 6 hours, because Vertex can't list models with an API key. Models that return 404
for your key and region are hidden. The default is `VERTEX_MODEL_NARROW`. The pick applies to that run only, and is
recorded as `payload.agent_model` and shown in the runs table.

**Stopping a run.** After Run, the progress panel has a "Stop the analysis" button. It posts the form's run token to
`/fo/run/cancel` (`cancel.py`). The runner checks for it before each step (reading the filing, each pre-check,
scoring, each trigger check, recording), then ends with `FoRunCancelled`: no run is recorded, and the page says "Run
stopped". A model call already in flight finishes first, and its `model_runs` row stays logged. The stop registry is in
memory, so this works in a single-process server. On the CLI, Ctrl+C stops a run.

**Which step a run is on.** The same calls record each step as it starts. While a run is under way the progress panel
polls `GET /fo/run/progress?run_token=…` every second and shows "Step N: …" with the steps done. When a run fails, the
page says which step it failed in ("Run failed during step 14: the ICR trigger T1 check") and lists the steps completed
before it; an unexpected fault in a later step is reported as such, not as an unreadable filing (that message is kept
for errors while reading the filing). On the CLI, `run-fo-case` prints each step to stderr (`[fo] step N: …`).

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

## Trigger Agent: DSCR T2

`dscr_t2.py`, prompt `fo_trigger_dscr_t2`. Like T1 it runs only when `payload.gate.agent_triggers` is "enabled". It
runs **before T1**, whatever DSCR scored: it corrects an input to cash flow, so T1 then judges the corrected DSCR
(a T2 recompute can lift DSCR below T1's trigger).

| Trigger | Why | What the agent checks |
|---|---|---|
| CapEx above 1.5× depreciation (the DSCR headline column's year) | Growth CapEx counted as maintenance understates DSCR | Does the MD&A or segment note split maintenance from growth CapEx? Are the growth projects committed or under construction? |

**The inputs T2 works on** (the DSCR column, `measures.py`): CapEx is total CapEx (purchases of PP&E), which DSCR
deducts as maintenance CapEx because filers don't tag maintenance CapEx. Depreciation is depreciation only, excluding
amortisation of acquired intangibles: the tagged `Depreciation`, else D&A less `AmortizationOfIntangibleAssets` (none
tagged = none). Without CapEx or depreciation the trigger isn't set off.

The agent reads MD&A liquidity and capital resources (with its capital-expenditure sub-sections) and the segment note:
paragraphs and tables that mention capital expenditures, maintenance, growth, expansion, new facilities,
construction, projects or commitments (K01…). Code checks every fact and re-derives the outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| Growth projects committed or under construction | No adjustment |
| Maintenance/growth split disclosed | Recompute DSCR with the disclosed maintenance CapEx (or total CapEx less the disclosed growth CapEx) |
| Growth described but not quantified | Input correction: depreciation used as maintenance CapEx, DSCR recomputed |
| No growth spending described | Hold the score |

What each needs:
- Committed: one verbatim quote that names a growth project (expansion, new capacity, development, construction…) and
  says it is committed or under construction.
- Split: the year's maintenance (or growth) CapEx, with the amount in a verbatim quote that says maintenance
  (sustaining, replacement, routine) or growth. A forecast or budget for a later year is held back ("expect",
  "budgeted", "guidance"…), and so is an amount above total CapEx.
- Growth described: a verbatim quote describing growth spending.

The recompute replaces total CapEx with maintenance CapEx in cash flow for debt service on the headline column only.
The column keeps `capex_reported`, `cash_flow_reported` and `capex_adjustment` (`calculated`, `ratio_calculated`,
`recomputed`, `maintenance_capex`, `score`). Escalation works as for T1: only when unresolved (`dscr_t2:dscr`). When
T2 and T1 are both unresolved, DSCR gets one escalation carrying both (`checks`), and its calculated score is DSCR's
before either trigger.

## Trigger Agent: DSCR T3

`dscr_t3.py`, prompt `fo_trigger_dscr_t3`. Like T1 and T2 it runs only when `payload.gate.agent_triggers` is
"enabled". It runs after T2 and before T1, whatever DSCR scored: it corrects cash taxes in cash flow, so T1 judges the
corrected DSCR.

| Trigger | Why | What the agent checks |
|---|---|---|
| \|cash taxes paid − current tax expense\| > 5% × CFADS (CFADS = the DSCR numerator, after T2) | Refunds or one-off payments distort cash flow | Refunds received; settlements paid in cash to tax authorities; scheduled one-off payments; the stated reasons for the gap between current tax expense and cash taxes paid |

**The inputs T3 works on** (the DSCR column, `measures.py`): cash taxes paid are `IncomeTaxesPaidNet` (net of
refunds), the figure DSCR deducts. Current tax expense is `CurrentIncomeTaxExpenseBenefit`, else federal + state and
local + foreign current tax. Without either the trigger isn't set off.

The agent reads the income tax note and MD&A (income taxes, liquidity, cash flows): paragraphs and tables that mention
taxes paid, refunds, settlements, the IRS or tax authorities, one-time payments, NOLs, bonus depreciation, R&D
expensing, tax credits or the CAMT (X01…). Code checks every fact and re-derives the outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| One-off quantified | Input correction: backed out of cash taxes paid (a payment added back, a refund subtracted), DSCR recomputed |
| The gap is structural (NOLs, bonus depreciation, R&D expensing, purchased tax credits, CAMT) | No adjustment |
| A one-off mentioned but not quantified, or no explanation given | Hold the score; DSCR flagged as low data confidence |

What each needs:
- A one-off: its amount in a verbatim quote that names it (refund; settlement with a tax authority; one-time,
  transition-tax or installment payment), for the year (a forecast or budget is held back). A settlement counts only
  if the filing says it was paid in cash (in its quote, or a second `paid_in_cash` quote).
- Structural: a verbatim quote naming the reason. EHC's FY2025 10-K is an example: bonus depreciation and R&D
  expensing under the OBBBA lowered 2025 cash taxes (cash taxes $124.0 million vs current tax expense $170.6 million).
- Several one-offs are netted (payments − refunds) and backed out together.

The column keeps `cash_taxes_reported`, `cash_flow_reported` (as tagged, before T2 and T3) and `tax_adjustment`
(`calculated`, `ratio_calculated`, `recomputed`, `backed_out`, `score`). Low data confidence sets the DSCR measure's
`data_confidence = "low"`, shown on its card. Escalation works as for T1 and T2: only when unresolved (`dscr_t3:dscr`),
merged into one DSCR escalation with any other unresolved DSCR trigger.

## Trigger Agent: ICR T1

`icr_triggers.py`, prompt `fo_trigger_icr_t1`. It runs after the DSCR triggers, only when `payload.gate.agent_triggers`
is "enabled".

| Trigger | Why | What the agent checks |
|---|---|---|
| ICR headline score ≥ 8.5 (the latest year: ICR below 1.5x) | Weak cover | One-offs in EBIT; undesignated swap mark-to-market in interest expense; capitalised interest |

ICR = (EBIT + rent) / (interest incurred + rent); EBIT is pre-tax income plus interest expense. The agent reads MD&A
(without its critical-accounting and new-standards boilerplate) and every note: paragraphs and tables that mention a
one-off item, a swap or derivative, or capitalised interest (I01…). Code checks every fact; the two adjustments apply
together:

| Finding (verified) | Adjustment |
|---|---|
| Quantified one-offs in EBIT | Input correction: charges added back, gains removed, ICR recomputed |
| An undesignated swap's mark-to-market in interest expense | Removed from interest incurred (a loss out, a gain back in), ICR recomputed; EBIT already excludes it |
| Nothing found | Unresolved: ICR escalated to an analyst |
| Capitalised interest | Cross-check only: a disclosed amount the tagged figures missed is noted, not added |

What each needs:
- A one-off: its amount in a verbatim quote that names it (impairment, restructuring, litigation, a gain or loss on a
  sale or extinguishment, transaction costs, a write-down, an insurance recovery…) and shows a charge or a gain, for
  the year (a forecast is held back).
- A swap: its amount in a verbatim quote naming the swap or derivative and a change in fair value, and verified quotes
  saying the swaps are not designated as hedges and the change is in interest expense. A loss can't exceed interest
  incurred.

Unlike the DSCR triggers, **nothing found escalates ICR** (`icr_t1:icr`). As for every trigger, a disagreement between
the agent's answer and its verified evidence escalates it too, even when an adjustment holds. The column keeps
`ebit_reported`, `interest_reported` and `trigger_adjustment` (`calculated`, `ratio_calculated`, `recomputed`,
`ebit_change`, `interest_change`, `score`).

## Trigger Agent: ICR T2

`icr_t2.py`, prompt `fo_trigger_icr_t2`. It runs on ICR's latest year, whatever ICR scored, after the DSCR triggers
and before ICR T1 (it corrects the interest base, so T1 judges the corrected ICR), only when
`payload.gate.agent_triggers` is "enabled".

| Trigger | Why | What the agent checks |
|---|---|---|
| Interest from a net-interest tag (`InterestIncomeExpenseNet`, or its nonoperating variant) or the operating-interest tag (`InterestExpenseOperating`) | May be net of interest income, or classified unusually | Gross interest expense in the debt note (and interest income, swaps) |

The ICR column records the interest tag behind interest incurred (`interest_expense_concept`, none when
`InterestCostsIncurred` is tagged) and, when the only interest figure is net income, `net_interest_income`.

| Finding | Outcome |
|---|---|
| Net interest is income (code, no agent) | ICR n/a: flagged, no score, not counted in the composite (the prior year doesn't stand in) |
| Gross interest expense disclosed | ICR recomputed: interest incurred = gross + capitalised interest as tagged |
| Only net interest given, interest income disclosed | Gross = net + interest income, ICR recomputed |
| Operating tag, only interest income disclosed | Kept as tagged |
| Neither gross interest nor interest income disclosed | Unresolved: ICR escalated to an analyst (`icr_t2:icr`) |
| An undesignated swap's mark-to-market in interest expense | Removed; other swap effects stay. ICR T1 then doesn't remove it again |
| An operating-interest tag (often a captive finance arm) | Flagged on the ICR card: ICR less meaningful |

EBIT is not changed (pre-tax income + the interest expense tagged), so interest income is never counted as cover. A
gross figure counts only with its amount in a verbatim quote that names interest expense, doesn't describe a net figure
("interest expense, net", "net of interest income") and isn't below the net expense tagged. The column keeps
`interest_reported` and `interest_adjustment` (`calculated`, `ratio_calculated`, `recomputed`, `interest_change`,
`gross_used`, `score`); the measure keeps `flag_notes` and, when n/a, `not_applicable`. As for every trigger, a
disagreement between the agent's answer and its verified evidence escalates ICR; with ICR T1 also unresolved, ICR gets
one escalation for both.

## Trigger Agent: ICR T3

`icr_t3.py`, prompt `fo_trigger_icr_t3`. It runs on ICR's latest year, whatever ICR scored, after ICR T2 and before
ICR T1, only when `payload.gate.agent_triggers` is "enabled" and ICR isn't n/a.

| Trigger | Why | What the agent checks |
|---|---|---|
| EBIT source is operating income, not pre-tax income + interest expense (the engine's fallback when pre-tax income, or the interest to add back, isn't tagged) | Operating income leaves out non-operating items (other income and expense, the non-service part of pension cost, investment gains and losses, FX), so EBIT can be too high or too low against the standard definition and companies aren't compared on the same basis | Pre-tax income: the "income before income taxes" line for continuing operations (every 10-K has it: Regulation S-X 5-03). Interest expense: the expensed amount deducted in arriving at pre-tax income |

The agent reads the income statement, MD&A and the notes (K01…). Code checks every fact; first match wins:

| Finding (verified) | Outcome |
|---|---|
| Pre-tax income found | EBIT = pre-tax income + interest expense, ICR recomputed (resolved) |
| Pre-tax income not found | EBIT stays operating income: unresolved, ICR escalated (`icr_t3:icr`); the items the agent found between operating income and pre-tax income are recorded |

Pre-tax income counts only with its amount in a verbatim quote that says income (or loss) before income taxes and
isn't for discontinued operations; a loss must be called one (`is_loss`) and shown as one, since amounts are parsed
without their sign. Interest expense is the agent's verified figure, else the tagged expense; with neither, EBIT can't
be rebuilt and ICR is unresolved. Only ICR's EBIT changes: Debt/EBITDAR and DSCR keep theirs. The column keeps
`ebit_reported`, `ebit_concept_reported` and `ebit_adjustment` (`calculated`, `ratio_calculated`, `recomputed`,
`ebit`, `score`).

## Debt/EBITDAR conventions

Rules every Debt/EBITDAR figure follows, whichever trigger touches it. The engine enforces them; the four Debt/EBITDAR
Trigger Agent prompts state them (`fo_prompts.LEVERAGE_CONVENTIONS`) so the agents know what the figures mean.

| Rule | Where |
|---|---|
| Total debt reconciles to the debt note and the balance sheet, at carrying amount; finance leases are added only if they aren't already in the total | `measures.total_debt` (tags); checked against the debt note by trigger T4 when debt comes from a fallback |
| Lease debt = the ASC 842 operating lease liability | `measures.operating_lease_liability` |
| Rent = the operating lease cost (lease-cost table), never cash paid (the EBITDAR margin alone falls back to cash lease payments, labelled). No leases: rent 0. Leases tagged but no lease cost: rent can't be found, so EBITDAR (and DSCR, ICR, the margin) is n/a ("No operating lease cost tagged") | `measures.rent_for` |
| Restricted cash is never netted against debt | `measures.CASH_CONCEPTS` (cash and cash equivalents only) |
| Net debt ≤ 0: the net score is 1 and no ratio is computed (debt ≤ 0 the same for the gross score); the composite then follows its usual rule (+1 when the gross score is 2 or more worse) | `scoring.score_leverage` (`net_cash`, `raw` keeps the value) |
| EBITDAR ≤ 0 (after any T1 normalisation): both scores 10, no ratio, flagged "EBITDAR ≤ 0"; it wins over net cash | `scoring.score_leverage` (`flag`) |
| A negative value is a real value, not n/a; n/a only when an input can't be found | The two rules above; `reason` is set only for a missing input |

The page shows "n/m: EBITDAR ≤ 0" or "n/m: net cash" where no ratio is computed, and "—" only for a missing input.
Every rescore (T4, T1, T2, T3's score without leases) goes through `score_leverage`, so the rules hold after each
trigger. On the FY2025 filings, ACHC's FY2025 Debt/EBITDAR is 10 (was 9.7); EHC and HCA don't change.

## Trigger Agent: Debt/EBITDAR T1

`leverage_triggers.py`, prompt `fo_trigger_leverage_t1`. It runs on Debt/EBITDAR's headline column (the latest year),
after the ICR triggers, only when `payload.gate.agent_triggers` is "enabled" and Debt/EBITDAR has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| \|net one-offs\| > 5% × EBITDAR, or EBITDAR ≤ 0. Net one-offs = one-off charges − one-off gains | One-offs distort EBITDAR: charges make leverage look worse than it is, and gains make it look better | Impairments, litigation, acquisition costs, restructuring |

**The inputs T1 works on** (the Debt/EBITDAR column, `measures.one_offs_for`): net one-offs as tagged, the first
tagged concept of each group. Charges: impairments (one group, so goodwill isn't counted twice), restructuring,
litigation, acquisition costs, integration costs. Gains (signed): on the sale of a business, on disposals of PP&E,
on litigation settlements. None tagged = 0, so EBITDAR ≤ 0 alone can set it off. The column shows them as "Net
one-offs as tagged (trigger T1)".

The agent reads MD&A (without its critical-accounting and new-standards boilerplate) and every note: paragraphs and
tables that mention a one-off item or an operating driver (operating income, EBITDA, volumes, admissions, payer mix,
wages and labor costs) (L01…). Code checks every fact and re-derives the outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| One-offs quantified and not recurring | Normalised EBITDAR = reported + one-off charges − one-off gains; Debt / EBITDAR recomputed |
| The change in EBITDAR is operational, with no one-offs found | No adjustment |
| One-off identified but not quantified | The 3-year average EBITDAR (2-year for a smaller reporting company), labelled on the column ("3-year average EBITDAR (FY2023–FY2025)"); Debt / EBITDAR recomputed |
| Partly quantified with an unexplained remainder, or no explanation at all | Unresolved: Debt/EBITDAR escalated (`leverage_t1:leverage`) |

What each needs:
- A one-off: its amount in a verbatim quote that names it (impairment, restructuring, litigation, acquisition or
  transaction costs, a gain or loss on a disposal…) and shows a charge or a gain, for the year (a forecast is held
  back).
- Recurring: an item counts as recurring (explained, not adjusted) only with a verbatim quote showing it recurs
  ("each year", "recurring", "ongoing"…). A recurring claim that doesn't hold up leaves the item a one-off.
- Unquantified: a verbatim quote naming the one-off.
- Operational: a verbatim quote naming an operating driver (volumes, pricing, payer mix, wages, costs…).
- An unexplained remainder: a one-off left unquantified beside quantified ones, or the verified items (one-offs and
  recurring items) falling short of the tagged net one-offs by more than 5% of EBITDAR. Over-explaining is fine (the
  tags miss items), so "operational" can't stand while tagged one-offs above the threshold are unexplained.
- The average is the latest three columns' EBITDAR as the measure holds them (the latest after any deal
  adjustment), or two for a smaller reporting company: the cover page's box, `dei:EntitySmallBusiness` (tagged as a
  boolean, "true" or the ticked box ☒), since an SRC presents two years of income statements. With fewer years, an
  unquantified one-off is unresolved. The status is `average_ebitdar`; the trigger records
  `smaller_reporting_company` and `average_years`.

Both ratios are recomputed on the new EBITDAR and rescored on the same benchmark curve (`scoring.score_leverage` with
the measure's own `scoring` record). Only Debt/EBITDAR changes: the EBITDAR margin and DSCR keep their EBITDAR. The
column keeps `ebitdar_before_trigger`, `ebitdar_basis` (the 3-year label) and `ebitdar_adjustment` (`calculated`,
`ratio_calculated`, `net_ratio_calculated`, `ebitdar_calculated`, `recomputed`, `ebitdar`, `score`). As for every
trigger, a disagreement between the agent's answer and its verified evidence escalates Debt/EBITDAR too; with an
unresolved deal on leverage, Debt/EBITDAR gets one escalation for both. On the FY2025 filings, ACHC sets it off (EBITDAR
−704.9 million, tagged one-offs +1,313.8 million); EHC and HCA don't.

## Trigger Agent: Debt/EBITDAR T2

`leverage_t2.py`, prompt `fo_trigger_leverage_t2`. It runs on Debt/EBITDAR's headline column after Debt/EBITDAR T1 (T1
corrects EBITDAR, which moves both scores), only when `payload.gate.agent_triggers` is "enabled" and Debt/EBITDAR has a
score.

| Trigger | Why | What the agent checks |
|---|---|---|
| \|gross score − net score\| ≥ 2 (Debt / EBITDAR score against Net debt / EBITDAR score) | Cash does much of the work, and cash can be spent | Trapped cash and earmarked cash |

The agent reads MD&A (without its boilerplate) and every note: paragraphs and tables that mention cash held abroad or
by subsidiaries, repatriation, restrictions, regulatory or statutory capital, captive insurance, variable interest
entities, minimum cash, pending acquisitions, declared dividends, cash set aside, or general corporate purposes (C01…).
Code checks every fact and re-derives the outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| Acquisition or payout earmark, or trapped cash, quantified | Subtracted from cash (never below zero); net debt and Net debt / EBITDAR recomputed and rescored |
| Earmarked only for debt maturities | No adjustment: repaying debt with cash leaves net debt unchanged |
| Cash freely available (or nothing earmarked or trapped) | No adjustment |
| Earmark or trapping described but not quantified | Unresolved: Debt/EBITDAR escalated (`leverage_t2:leverage`) |

What each needs:
- An item: a verbatim quote that names it and mentions cash. Acquisition: an acquisition, merger or purchase price.
  Payout: a dividend or other distribution. Trapped: restricted, held abroad or by a regulated, insurance or captive
  subsidiary, a joint venture or VIE, or a minimum cash requirement. Debt maturity: a maturity, repayment or
  redemption.
- **A share repurchase (buyback) authorisation isn't an earmark**: a quote mentioning a repurchase or buyback is held
  back as an acquisition, payout or trapped item.
- Quantified: the amount in its quote. An amount that doesn't hold up leaves the item described but not quantified.
  Earmarks are forward-looking by nature, so a future use (a deal closing next year, a dividend payable next quarter)
  isn't held back as a forecast.
- First match is literal: a quantified item comes out of cash even when another is described without an amount (noted
  on the trigger, not subtracted). "Only for debt maturities" means no acquisition, payout or trapped item at all.
- Freely available needs no quote: without any earmark or trapped item, the cash counts as it is. A quote saying the
  cash is available for general corporate purposes is shown when given.
- The agent is told not to report restricted cash shown apart from cash and cash equivalents (already excluded), and to
  count the same cash once. Code caps the total at the cash counted.

Only cash changes: debt, EBITDAR and the gross ratio stay as they are. The column keeps `cash_before_trigger` and
`cash_adjustment` (`calculated`, `net_score_calculated`, `net_ratio_calculated`, `cash_calculated`, `removed`,
`recomputed`, `cash`, `score`). Escalation works as for T1; T1 and T2 together give Debt/EBITDAR one escalation, which
also merges with an unresolved deal.

## Trigger Agent: Debt/EBITDAR T3

`leverage_t3.py`, prompt `fo_trigger_leverage_t3`. It runs on Debt/EBITDAR's headline column after Debt/EBITDAR T2 (T1
and T2 correct EBITDAR and cash, which move both scores), only when `payload.gate.agent_triggers` is "enabled" and
Debt/EBITDAR has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| Score with leases − score without leases ≥ 2 | The lease and guarantee notes, for a master lease with a cross-default or a parent guarantee | REIT master leases, cross-defaults, guarantees, escalators, terms |

**The score without leases** (`leverage_t3.without_leases`) rescores the same column on the same benchmark curve with
operating lease liabilities left out of debt and rent left out of EBITDAR: debt / EBITDA and (debt − cash) / EBITDA,
EBITDA = EBITDAR − rent (EBITDAR and cash as the measure holds them, so after T1 and T2), then the composite leverage
score. The score with leases is the composite leverage score as it stands. Both use the lease-adjusted benchmarks.

The agent reads MD&A (without its boilerplate) and every note: paragraphs and tables that mention master leases, REITs,
landlords or lessors, sale-leasebacks, cross-defaults, lease guarantees, escalators, renewal or termination options, or
lease terms (R01…). Code checks every fact and re-derives the outcome:

| Finding (verified) | Adjustment |
|---|---|
| Master lease, cross-default or parent guarantee disclosed | +1 on the composite leverage score (capped at 10) |
| None disclosed | Unresolved: Debt/EBITDAR escalated (`leverage_t3:leverage`) |
| Rent escalators or cancellable leases | No lease adjustment |

What each needs:
- A feature: a verbatim quote that names it and is about a lease (a lease, landlord, lessor, REIT or rent). Master lease:
  "master lease". Cross-default: a cross-default, cross-collateralisation, or a default under one lease being a default
  under others. Parent guarantee: a guarantee. The agent names the landlord (a REIT) as `counterparty` when given.
- Escalators, cancellable leases and lease terms are verified and shown, but never count as disclosed: with only those,
  T3 is unresolved.
- A guarantee of debt only (no lease in the quote) is held back.
- Without any passage, nothing is disclosed: unresolved, with no model call.

Only the composite leverage score changes: the ratios and their scores stay as they are. The column keeps
`lease_adjustment` (`calculated`, `score_without_leases`, `recomputed`, `score`) and the composite records
`lease_uplift`. Escalation works as for T1 and T2; the three give Debt/EBITDAR one escalation, which also merges with an
unresolved deal. On the FY2025 filings, none sets it off (ACHC, EHC and HCA's scores with and without leases are within
0.2).

## Trigger Agent: Debt/EBITDAR T4

`leverage_t4.py`, prompt `fo_trigger_leverage_t4`. It runs on Debt/EBITDAR's headline column **first** among the
Debt/EBITDAR triggers (debt feeds the scores T2 and T3 compare), only when `payload.gate.agent_triggers` is "enabled"
and Debt/EBITDAR has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| Debt from a fallback (finance leases added, total tag skipped) | The debt definition may be off | The list of instruments in the debt note, tied to the balance sheet; finance lease liabilities in the lease note; whether finance leases are already inside the debt note total |

**The fallback** (`measures.total_debt`, recorded as `debt_fallback` on the column): a tagged total is skipped when
the balance sheet presents the current and non-current debt lines (`total_skipped`: its concept and value), and
finance lease liabilities are added when the balance sheet shows them as lines of their own (`finance_leases_added`).
`borrowings` is the debt before them. **T4 fires when finance leases were added, or when a skipped total differs
from the balance-sheet lines by more than 1%** (the tie tolerance): when the two agree, two independent figures
already confirm the debt, so there is nothing for the agent to reconcile. The last-resort route (Long-Term Debt + short-term borrowings) isn't a fallback
for T4.

The agent reads the balance sheet, the debt note and the lease note (B01…): paragraphs and table rows that mention
debt, borrowings or finance leases. It quotes, at carrying amount and at the year-end, each instrument (borrowings,
finance leases listed as debt, discounts and issuance costs, premiums), the note's total debt (current portion
included), the lease note's finance lease liabilities, and whether the note's total already includes finance leases.
Code does the arithmetic:

| Finding (verified) | Adjustment |
|---|---|
| Reconciles: the instruments add up to the note's total, which ties to the balance sheet's debt lines (within 1%) | Debt / EBITDAR recomputed with the reconciled total debt (resolved) |
| Doesn't reconcile: no total, the instruments don't add up, or the total doesn't tie | Unresolved: Debt/EBITDAR escalated (`leverage_t4:leverage`) |

What each needs:
- Every amount in its verbatim quote, with its unit (written, or the passage's "in millions"). Instruments need the
  words of their kind (a note, loan, facility…; finance or capital leases; a discount or issuance costs; a premium).
- The tie: the note's total against the balance sheet's debt lines (`borrowings`), or, when finance leases are in the
  note's total, against those lines plus the finance leases the engine added (the balance sheet may show them either
  way). Without a stated total, the instruments' sum is the total.
- **Finance leases counted once.** Reconciled total debt = the note's total when finance leases are already in it (an
  instrument of kind finance lease, or a verified quote saying so), else the note's total + the finance lease
  liabilities (the lease note's, else the tagged ones; a difference is noted). So finance leases the engine added on
  top of a total that already holds them come out, and finance leases kept in other liabilities go in.

Debt, debt + op. leases and net debt are recomputed, and both ratios rescored on the same benchmark curve. EBITDAR
and cash don't change; RCF / debt and the liquidity measure keep the debt as calculated. The column keeps
`debt_before_trigger` and `debt_adjustment` (`calculated`, `ratio_calculated`, `net_ratio_calculated`,
`debt_calculated`, `recomputed`, `debt`, `score`). T4, T1, T2 and T3 give Debt/EBITDAR one escalation. On the FY2025
filings, none sets it off: EHC's and HCA's skipped totals equal their balance-sheet lines (2,490.8 and 46,492.0
million), and ACHC's debt isn't a fallback.

## Trigger Agent: RCF/Debt T1

`rcf_triggers.py`, prompt `fo_trigger_rcf_t1`. It runs on RCF / debt's headline column (the latest year), after the
Debt/EBITDAR triggers, only when `payload.gate.agent_triggers` is "enabled" and RCF / debt has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| Score ≥ 9, or RCF / debt < 0 | A one-off cash cost or a one-off payout may be driving the weak result, not the company's normal cash generation | Cash one-offs in FFO, special dividends, and recurrence |

The agent reads MD&A (without its boilerplate) and every note: paragraphs and tables that mention a one-off item
(restructuring or severance, litigation, acquisition or integration costs, special pension contributions, tax on a
disposal), a special or one-time dividend, or operating cash flow (F01…). Code checks every fact and re-derives the
outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| One-off cash cost or special dividend quantified, not recurring | Added back (cash costs to FFO, special dividends out of dividends paid); RCF / debt recomputed and rescored |
| No one-offs found | No adjustment |
| One-off identified but not quantified | Unresolved: RCF / debt escalated (`rcf_t1:rcf`) |

What each needs:
- A cash cost: its cash amount in a verbatim quote that names the one-off and shows cash paid (paid, payments,
  contributions…). A charge or an accrual isn't cash, so it is held back. The year's figure only: a forecast is held
  back.
- A special dividend: a quote naming a special, one-time or extra dividend (or distribution) that shows it was paid.
  One declared or payable after the year-end is held back (it isn't in the year's dividends paid). Special dividends
  are capped at the dividends paid as tagged.
- Recurring: an item counts as recurring (not adjusted) only with a verbatim quote showing it recurs; with only
  recurring items, no one-offs are found.
- First match is literal: a quantified one-off is added back even when another is identified without an amount (noted
  on the trigger, not added).
- Without any passage, there is nothing to read: no one-offs found, no adjustment, no model call.

Only RCF / debt changes: the CFO-based comparison stays as tagged. The column keeps `ffo_before_trigger`,
`dividends_before_trigger`, `rcf_before_trigger` and `rcf_adjustment` (`calculated`, `ratio_calculated`,
`rcf_calculated`, `cash_costs`, `special_dividends`, `recomputed`, `rcf`, `score`). On the FY2025 filings, none sets
it off (ACHC is closest: RCF / debt 5.2%, score 8.9).

## Trigger Agent: RCF/Debt T2

`rcf_t2.py`, prompt `fo_trigger_rcf_t2`. It runs **at any score** (good scores too) on RCF / debt's headline column,
after RCF/Debt T1, only when `payload.gate.agent_triggers` is "enabled" and RCF / debt has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| Tagged one-off inflows > 5% × \|FFO\|, or FFO up 25% or more on the prior year **and** more than 10 points faster than revenue | A one-off inflow (supplemental-payment catch-up, insurance or litigation proceeds, tax refund, one-time working-capital release) lifts CFO and FFO for one year only, and a good score hides it | The cash flow statement (operating section, supplemental disclosures), MD&A on liquidity and the change in CFO, and the revenue, government-assistance, insurance, litigation and income-tax notes |

**Decided by code, on FFO** (the RCF numerator): a CFO rise from working capital alone doesn't move FFO, so it doesn't
fire. Nor does a business that is simply growing: the growth leg needs FFO to outgrow revenue (each column holds its revenue, `revenue_concept`); without revenue tagged for both years, growth alone fires it. The tagged inflows are `measures.ONE_OFF_INFLOW_CONCEPTS` (income tax refunds, insurance proceeds in operating
activities; recorded per year as `tagged_inflows` on the RCF columns). The spec's "one-off inventory" can't decide
the trigger, since that inventory is the agent's work.

The agent reads the passages (G01…) with the working-capital lines FFO subtracts (`data["working_capital"]`, from the
calculation linkbase) and the items RCF/Debt T1 counted. **Code places each inflow and tests recurrence**, first match
wins:

| Finding (verified) | Adjustment |
|---|---|
| Inflow in operating cash flow outside the working-capital lines, amount stated, not recurring | Removed from CFO and FFO; RCF / debt (and the CFO-based comparison) recomputed |
| Working-capital release on one of the working-capital lines | No adjustment: FFO already excludes it |
| Working-capital release outside them | Only the amount quoted as one-time or timing (a catch-up, a prior-year collection) is removed |
| Working-capital release, but no working-capital lines tagged (composition can't be determined) | Unresolved |
| Inflow recurs (2 of the 3 years presented) | No adjustment |
| Inflow identified, amount not stated | Unresolved (never estimated) |
| In investing or financing cash flow, or none found | No adjustment |

What each needs:
- An inflow: its amount in a verbatim quote that names it and shows cash received, the year's (a forecast is held
  back), from the 10-K only (no estimates, outside sources or non-GAAP figures). A general rise in CFO isn't an
  inflow.
- Placement: the agent names the working-capital line an inflow sits in, or none; a line that isn't one FFO subtracts
  is held back.
- Recurrence: tested on the tags where they cover the category (tax refunds, insurance proceeds), else on verified
  quotes of the earlier years' amounts.
- A quantified removal comes first (another item unquantified is noted, not removed); otherwise anything unresolved
  escalates RCF / debt (`rcf_t2:rcf`), even beside items that get no adjustment.
- **With RCF/Debt T1:** two calls, netted in code. T1 runs first and adds its one-off cash costs back; T2 sees T1's FFO
  and the items T1 counted (held back if reported again) and removes its inflows from the same column: FFO + T1's
  costs − T2's inflows. One escalation for both.

The column keeps `cfo_before_trigger` (and `ffo_before_trigger` / `rcf_before_trigger` from before either trigger)
and `rcf_t2_adjustment` (`calculated`, `ratio_calculated`, `ffo_calculated`, `rcf_calculated`, `removed`,
`recomputed`, `rcf`, `score`). On the FY2025 filings, none sets it off (FFO: ACHC −74%, EHC +19%, HCA +15%; no tagged
inflow for the year). The NVDA FY2026 test fixture's FFO is up more than 25%, but not more than 10 points faster than revenue, so it doesn't either.

## Trigger Agent: RCF/Debt T3

`rcf_t3.py`, prompt `fo_trigger_rcf_t3`. It runs on RCF / debt's headline column after RCF/Debt T2, only when
`payload.gate.agent_triggers` is "enabled" and RCF / debt has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| No standard NCI-distributions concept tagged for the year, while NCI is material | Distributions to partners are cash that leaves the group: if they are missing, RCF looks too high, which overstates debt capacity | The financing section of the cash flow statement (including lines under the company's own labels); the statement of changes in equity (NCI column) and the redeemable NCI rollforward in mezzanine equity; the ASC 810 schedule of changes in the parent's ownership interest |

**Decided by code.** The standard concepts are `measures.NCI_DISTRIBUTION_CONCEPTS`; each RCF column records
`nci_distributions_tagged` and the NCI facts. NCI is material when |NCI share of net income| is above 5% of
|consolidated net income| (`ProfitLoss`, else continuing operations including NCI, else parent net income + the NCI
share); when consolidated net income is zero, negative or not tagged, when NCI equity (plus redeemable NCI) is above 5%
of total equity (including NCI, else the parent's when all NCI is redeemable), or total equity is zero or negative
(NCI then outweighs the group's equity). With neither test possible, NCI isn't treated as material.

The agent reads the cash flow statement, the equity statement and every note (N01…). Code checks every fact and
re-derives the outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| Distributions found and the cash paid stated | RCF recomputed with the cash paid, distributions to redeemable NCI included, purchases of NCI excluded |
| Only declared amounts disclosed | The declared amount is used, labelled on the column as a timing difference |
| The equity statement and redeemable NCI rollforward show no distributions in the year | Zero is correct: no adjustment |
| Lumped into "other financing" or with buyouts, the split not disclosed (or nothing found) | Unresolved: RCF / debt escalated (`rcf_t3:rcf`) |

What each needs:
- A distribution: its amount in a verbatim quote that names a distribution (or dividend) and NCI, minority or redeemable
  interests, partners or joint ventures, for the year. The cash flow statement's cash paid comes first; a declared
  amount needs a quote showing it was declared.
- Redeemable NCI: from the mezzanine rollforward, not the equity statement's NCI column; the agent reports each payment
  once.
- **Purchases of NCI are always excluded**: a quote that combines distributions with purchases or buyouts is held back
  (and, if the filing gives no split, it is the lumped case).
- None in the year: a verbatim quote showing no distribution (no, none, a dash or zero). Without one, and with nothing
  else found, T3 is unresolved: material NCI with no distributions found can't be confirmed as zero.

The distributions found replace the ones counted (none, as tagged): RCF, RCF / debt and the CFO-based comparison are
recomputed and rescored. The column keeps `nci_distributions_before_trigger`, `nci_distributions_basis` (shown as a
column note) and `rcf_t3_adjustment` (`calculated`, `ratio_calculated`, `nci_calculated`, `rcf_calculated`,
`recomputed`, `rcf`, `score`). T1, T2 and T3 give RCF / debt one escalation. On the FY2025 filings, none sets it off:
all three tag NCI distributions under a standard concept (EHC's NCI is 25% of net income, HCA's 13%, ACHC's redeemable
NCI 10% of equity, so all are material). T4 needs a standard concept to be present, so T3 and T4 can't both fire.

## Trigger Agent: RCF/Debt T4

`rcf_t4.py`, prompt `fo_trigger_rcf_t4`. It runs on RCF / debt's headline column after RCF/Debt T3, only when
`payload.gate.agent_triggers` is "enabled" and RCF / debt has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| `PaymentsToMinorityShareholders` is the source of the NCI-distributions figure (`nci_distributions_concept`) | The tag can hold buyouts of partners as well as distributions; a buyout is a discretionary investment, not a payout, so mixed in it makes RCF look too low | The financing section of the cash flow statement; the statement of changes in equity and the redeemable NCI rollforward (distributions against purchases of ownership); the ASC 810 schedule |

The agent reads the cash flow statement, the equity statement and every note (M01…, the same sections as T3). Code
checks every fact and re-derives the outcome:

| Finding (verified) | Adjustment |
|---|---|
| Buyouts found in the tagged amount and quantified | Excluded: RCF recomputed with distributions only |
| Buyouts mixed in but not quantified | Unresolved: RCF / debt escalated (`rcf_t4:rcf`) |
| The equity statement and ASC 810 schedule show no purchases of NCI | The tag holds distributions only: no adjustment |
| Nothing found either way | Unresolved: the tag's content can't be confirmed |

What each needs:
- A buyout: a verbatim quote naming a purchase of NCI ownership (a purchase, buyout, an additional interest, a
  redemption of redeemable NCI) and NCI or partners, for the year; quantified when its amount is in the quote.
- No purchases: a verbatim quote mentioning purchases of NCI ownership that shows none (no, none, a dash or zero).
- **Settled from the tags first:** when the equity statement's distributions to NCI
  (`MinorityInterestDecreaseFromDistributionsToNoncontrollingInterestHolders`) are at least the cash tagged (within
  1%) and no purchase of NCI ownership is tagged (`measures.NCI_PURCHASE_CONCEPTS`), the cash can't hold buyouts:
  distributions only, decided by code, no model call. Otherwise the agent reads the passages.
- A buyout identified without an amount is checked before "no purchases" (the two can't both hold); a quantified one
  comes first (an unquantified one beside it is noted, not excluded). Buyouts never take the distributions below zero.

RCF, RCF / debt and the CFO-based comparison are recomputed and rescored. The column keeps
`nci_distributions_before_trigger`, `nci_distributions_basis` and `rcf_t4_adjustment` (`calculated`,
`ratio_calculated`, `nci_calculated`, `rcf_calculated`, `excluded`, `recomputed`, `rcf`, `score`). T1-T4 give RCF /
debt one escalation. On the FY2025 filings, EHC and HCA set it off (both take NCI distributions from
`PaymentsToMinorityShareholders`: 152.1 and 827.0 million) and both are settled from the tags (equity-statement
distributions of 164.1 and 827.0 million, no purchases tagged), with no model call; ACHC doesn't set it off
(`PaymentsOfDividendsMinorityInterest`).

## Trigger Agent: EBITDAR Margin T1

`margin_triggers.py`, prompt `fo_trigger_margin_t1`. It runs after the Debt/EBITDAR triggers, only when
`payload.gate.agent_triggers` is "enabled" and the margin has a score.

| Trigger | Why | What the agent checks |
|---|---|---|
| Debt/EBITDAR T1 removed one-off items (status `normalised`) from the margin's headline year | One-off revenue raises both EBITDAR and revenue: correcting only EBITDAR still leaves the margin distorted | Where each item was booked: revenue, other operating income, or below operating income |

**Decided by code.** Whether an item sits in revenue is the agent's finding, so code fires on what it knows: T1
removed one-offs (T1's result is passed in; the periods must match). Before this trigger, T1's normalised EBITDAR
fed Debt/EBITDAR only; now **the margin takes T1's normalisation in every outcome** (the same one-offs), and only
revenue depends on where they were booked.

The agent reads the income statement, MD&A and the notes (E01…), with T1's items and the quotes that verified them.
Code checks each placement and re-derives the outcome:

| Finding (verified) | Adjustment |
|---|---|
| An item booked in revenue | It comes out of revenue too (a gain lowers revenue; a charge booked as a reduction of revenue raises it); the margin is recomputed on T1's EBITDAR and the corrected revenue |
| Every item booked outside revenue | No further adjustment: the margin on T1's EBITDAR, revenue as reported |
| Where an item was booked isn't clear | Unresolved: the margin (on T1's EBITDAR, revenue as reported) escalated (`margin_t1:margin`) |

What each needs:
- A placement: a verbatim quote that places the item: revenue (revenues, net sales, patient service revenue); other
  operating income (operating expenses, other operating income, impairment, restructuring…); below operating income
  (other income or expense, non-operating, gains on sales…). Without one, or when the agent leaves an item out, it is
  unclear.
- An item in revenue comes first (an unclear one beside it is noted); otherwise an unclear one leaves the margin
  unresolved.
- **Rent:** the margin alone falls back to cash paid for operating leases (`OperatingLeasePayments`) when the
  lease-cost table is missing, labelled on the column ("cash lease payments: lease-cost table missing"); this counts as
  resolved. Debt/EBITDAR, DSCR and ICR keep the strict rule (n/a).

The column keeps `ebitdar_before_trigger`, `revenue_before_trigger` and `margin_adjustment` (`calculated`,
`ratio_calculated`, `ebitdar_calculated`, `revenue_calculated`, `normalisation`, `revenue_adjustment`, `recomputed`,
`ebitdar`, `revenue`, `score`). On the FY2025 filings, only ACHC sets Debt/EBITDAR T1 off (EBITDAR ≤ 0); Margin T1
follows only if T1 normalises EBITDAR there.

## Trigger Agent: EBITDAR Margin T2

`margin_t2.py`, prompt `fo_trigger_margin_t2`. It runs after Margin T1 (on the margin T1 left), only when
`payload.gate.agent_triggers` is "enabled".

| Trigger | Why | What the agent checks |
|---|---|---|
| The margin scores 9 or more, or 2 or less; more than one reportable segment; a segment with 30% or more of the segments' revenue has a segment margin 5 percentage points or more from all segments' margin | One benchmark doesn't fit a company whose segments earn at very different rates: the outlier segment, not the credit, may be driving the score | Whether segment profit is measured the same way across segments (segment note); whether MD&A or the segment note explains the outlier's margin by how that business works, rather than by the year's performance; whether the filing reports a single reportable segment |

**Decided by code.** `segment_table` reads the segment note's tags for the margin's headline year: facts on
`StatementBusinessSegmentsAxis` alone, or with `OperatingSegmentsMember` on the consolidation axis; corporate, other
and elimination members aren't segments. Revenue is the first revenue concept tagged for each segment. Segment
profit is one concept tagged for every segment: a standard profit concept (`OperatingIncomeLoss`, pre-tax income,
`GrossProfit`), else a company concept whose name says profit (adjusted EBITDA, segment income) and not a cost, an
NCI share or another kind of income. Segment margin = segment profit / segment revenue; all segments' margin = their
profit / their revenue. The outlier is the qualifying segment furthest from it. Nothing is classified as core or
non-core, so there is no list of sub-sectors. **Segment profit only compares segments; it never feeds a score.** If
a mix-weighted benchmark is built, drop this trigger.

Code also works out the **direction**: the EBITDAR margin moved by (the remaining segments' margin − all segments'
margin), scored on the measure's own settings for the comparison only. `closer`: at least 1 point closer to p;
`as_extreme`: still on the same extreme, or further from p; otherwise `neither`.

The agent reads the segment note and MD&A (G01…: passages that name a segment or mention reportable segments,
segment profit or the chief operating decision maker), with code's segment table. Code checks each finding and
re-derives the outcome (first match):

| Finding (verified) | Adjustment |
|---|---|
| A single reportable segment, no segment profit for each segment, or segment profit not shown to be measured the same way (not comparable, or the filing doesn't say) | Unresolved: the margin escalated (`margin_t2:margin`) |
| Closer to p, and the filing explains the outlier's margin by how that business works | Move 1 point toward p, never past p (the ratio unchanged) |
| Just as extreme, or the outlier's margin explained by the year's performance | No adjustment |
| Otherwise (the results don't point either way) | Unresolved: the margin escalated |

The validity checks come first. The trigger computes the comparison from segment profit, so the comparison only counts
once segment profit is shown to be comparable across more than one reportable segment.

What each needs:
- A single reportable segment: a quote that says so (single / one reportable segment). Otherwise code's tagged segments stand.
- Segment profit measured the same way: a quote naming the measure (segment EBITDA, operating income, the chief
  operating decision maker's measure); not comparable also needs a word saying it differs.
- An explanation of the outlier's margin: a quote from a passage that names the outlier segment. How that business
  works: mix, pricing or reimbursement model, cost structure, capital intensity, pass-through revenue…; the year's
  performance: volumes, increases or declines, a one-off, an event.
- More than one segment tagged but no segment profit for each: unresolved without a model call.

The column keeps `margin_t2_adjustment` (`calculated`, `ratio_calculated`, `outlier`, `outlier_margin`,
`segment_margin`, `remaining_margin`, `direction`, `recomputed`, `score`). Margin T1 and T2 give the margin one
escalation. On the FY2025 filings none sets it off: ACHC (9.7) and EHC (1.6) tag a single segment; HCA's three
segments sit within a point of each other (23.5% together) and its margin scores 2.3.

## Trigger Agent: DSO T1

`dso_triggers.py`, prompt `fo_trigger_dso_t1`. It runs on DSO's headline column (the latest year), last of the
triggers, only when `payload.gate.agent_triggers` is "enabled" and DSO has a score. It reads the change **after the
acquisitions-and-divestitures pre-check's correction** (pro forma or divested revenue): the measures the triggers read
already carry the year's deals, and `fired_by` says so when the revenue was adjusted.

| Trigger | Why | What the agent checks |
|---|---|---|
| \|DSO this year − DSO last year\| ≥ 5 days | The change may reflect collections getting worse or better, or something mechanical that moved receivables | Mechanical causes, both directions: receivables sold and derecognised (ASC 860 disclosures, MD&A liquidity) and write-offs (allowance rollforward). Operational causes, DSO up only: payer disputes; billing or system problems; new facilities ramping up; whether MD&A or subsequent events say the problem has been fixed |

The agent reads MD&A (without its boilerplate) and every note: paragraphs and tables that mention receivables, their
sale or securitisation, the allowance or write-offs, collections, payers, billing or new facilities, and every
subsequent-events paragraph (S01…). Code checks every fact and re-derives the outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| Mechanical cause quantified (receivables sold and derecognised, or an unusual write-off) | Added back to receivables in the year it happened, for both years; both DSOs, the change and the score recomputed |
| DSO up, cause described, and the 10-K says collections recovered after the year-end | −1 on the DSO score (not below 1) |
| DSO up, cause described, no evidence it's been fixed | Unresolved: DSO escalated (`dso_t1:dso`) |
| Mechanical cause mentioned but not quantified | Unresolved: DSO escalated |
| No cause found | No adjustment: the score reflects what was reported |

What each needs:
- Receivables sold: a quote naming receivables and their sale (sold, factoring, derecognised, accounted for as a
  sale, ASC 860…) with the amount. **Only receivables sold and derecognised count**: a quote describing a
  securitisation accounted for as a secured borrowing (collateralised, remaining on the balance sheet) is held back,
  because those receivables stay in receivables.
- A write-off: a quote showing the write-off with its amount, and a second quote showing it is unusual (one-time,
  a payer's bankruptcy, specific, significant…). Routine write-offs from the allowance rollforward don't count.
- Each mechanical item names its year (`current` or `prior`): the agent reports each year's amount as its own item,
  so the add-back applies to both years and the change compares like with like. The prior year's own change is
  recomputed and rescored too.
- An operational cause: a quote naming the cause (a payer dispute or denials; billing, claims or system problems; new
  facilities, ramp-up or credentialing) that ties it to receivables or collections. Used only when DSO went up.
- The recovery: a quote showing collections improved, recovered or the problem was fixed, about receivables or
  collections, **after the year-end** (from the subsequent-events section, or saying so: "subsequent to year-end",
  "in January 2026"…). A plan or expectation ("we expect", "will") is held back.
- First match is literal: a quantified mechanical cause is applied even when an operational cause is also found
  (noted on the trigger).
- Without any passage, there is nothing to read: no cause found, no adjustment, no model call.

The column keeps `receivables_before_trigger` (on the prior-year column too, when its items were added back) and
`dso_adjustment` (`calculated`, `dso_calculated`, `prior_dso_calculated`, `change_calculated`, `added`,
`prior_added`, `relief`, `recomputed`, `dso`, `change`, `score`). An unresolved DSO T1 shares DSO's escalation with an
unresolved deal or prepaid-model check.

## Trigger Agent: Liquidity T1

`liquidity_triggers.py`, prompt `fo_trigger_liquidity_t1`. It runs on the liquidity-and-maturity measure's headline
column (the latest year-end), after DSO T1, only when `payload.gate.agent_triggers` is "enabled" and the measure has a
score. It doesn't fire when the revolver availability was found (tagged, or in the 10-K text: `revolver_found`), after
a covenant breach not waived (the availability is set to 0 whatever it is), or when the scoring settings give a manual
revolver availability.

| Trigger | Why | What the agent checks |
|---|---|---|
| Revolver availability at the year-end not found in the extracted data | The main liquidity source is missing, so both scores (liquidity coverage and refinancing coverage) are understated | The availability figure in MD&A liquidity; the commitment, drawn amount and letters of credit in the debt note; the borrowing base, for an asset-based revolver; the revolver's maturity date |

The agent reads the debt note and MD&A liquidity: paragraphs and tables that mention a revolver or credit facility, an
asset-based facility or its borrowing base, letters of credit or availability (L01…). It reports each revolving
facility once (term loans, notes, commercial paper and securitisations aren't revolvers; one stated availability for
all facilities together is one facility). Code checks every fact and re-derives each facility's availability, **first
match wins**:

| Finding (verified) | Adjustment |
|---|---|
| Availability stated (at the year-end) | Input correction with that figure: both coverages and scores recomputed |
| Not stated, but commitment, drawn and letters of credit given | Availability = commitment − drawn − letters of credit (not below 0); for an asset-based revolver, the lower of this and the borrowing base |
| No revolver in the debt note | No adjustment: the revolver availability stays at 0 |
| Revolver exists, but availability can't be worked out | Unresolved: liquidity and debt maturity escalated (`liquidity_t1:liquidity`) |

What each needs:
- The facility: a quote naming the revolving facility (revolver, credit facility, line of credit, ABL…); without it,
  the facility is held back.
- Availability, drawn amount, letters of credit, borrowing base: the amount in the quote and a date at the year-end
  (in the quote, or in the cited passage for a table row's column heading). A figure at a later date (MD&A often gives
  one after the year-end too) is held back. A nil drawn amount or nil letters of credit counts only with a quote saying
  none is outstanding ("no borrowings outstanding", a dash in a table row…); letters of credit the filing doesn't
  mention leave the facility unresolved.
- The commitment: the facility's size, in its quote (not dated).
- Asset-based: the agent's flag, or any of the facility's quotes naming an asset-based facility or a borrowing base;
  an asset-based revolver without a verified borrowing base is unresolved.
- Several facilities are summed. If any can't be worked out, the trigger is unresolved and nothing is added.
- The maturity date: a quote saying when the facility matures or expires, with the date. It is recorded; a revolver
  maturing within 12 months of the year-end is noted on the trigger, and its availability is still counted.
- Without any passage, there is nothing to read: no revolver, no adjustment, no model call.

The column's `revolver_found` and `revolver_found_source` take the verified figure ("Trigger Liquidity T1: …", with
the working and the passages cited), and `score_liquidity` reruns both tests on the measure's fixed bands; a covenant
relief's +1 is applied again after. The column keeps `liquidity_adjustment` (`calculated`, `coverage_calculated`,
`coverage_score_calculated`, `refinancing_calculated`, `refinancing_score_calculated`, `availability`, `recomputed`,
`score`).

## Trigger Agent: Liquidity T2

`liquidity_t2.py`, prompt `fo_trigger_liquidity_t2`. It runs on the liquidity-and-maturity measure's headline column
after Liquidity T1 (so it reads the scores with T1's revolver availability in), only when
`payload.gate.agent_triggers` is "enabled", when the liquidity coverage score or the refinancing (maturity) score is 9
or more. It doesn't fire after a covenant breach not waived (the score is 10 whatever the maturities).

| Trigger | Why | What the agent checks |
|---|---|---|
| Liquidity score ≥ 9 or maturity score ≥ 9, after T1 | The debt driving the score may already have been refinanced, extended or repaid | The subsequent-events note, the debt note and MD&A liquidity; any going-concern disclosure (the note or the auditor's report); DSCR T1's result, reused |

The agent reads every subsequent-events paragraph, the debt note's and MD&A liquidity's paragraphs and tables that
mention a refinancing, extension, repayment, maturity or new financing, and the going-concern disclosure (the
auditor's report paragraphs and the sentences outside the risk factors that mention going concern or substantial
doubt) (R01…). **DSCR T1's verified refinancings are passed in and reused** (`dscr_t1`, the record T1 shares with
Liquidity T2): the agent cites a T1 item (`dscr_t1_item`), and code takes T1's verified facts (the event executed
after the year-end or only a plan, its date, the year-1 maturity, the amount, an ASC 470-10-45-14 classification)
without re-checking them; the agent adds what T1 doesn't give (the new maturity, the source of a repayment). Code
checks every fact and re-derives each debt item's outcome, **first match wins**:

| Finding (verified) | Adjustment |
|---|---|
| Debt already classified as long-term because it was refinanced (ASC 470-10-45-14) | No further adjustment |
| Refinanced or extended after the year-end, and executed | The debt moves from its original maturity year to its new one; both tests recomputed |
| Repaid after the year-end from cash | The debt and the cash used come out (cash never below 0); recomputed |
| Repaid from new borrowing or equity | The debt comes out; for new borrowing the new debt goes in at its maturity; recomputed |
| Only planned | Unresolved: liquidity and debt maturity escalated (`liquidity_t2:liquidity`) |
| Nothing refinanced, extended or repaid found | No adjustment |

What each needs:
- Executed: an event quote that shows the refinancing (issued, exchanged, replaced), extension (extended, amended) or
  repayment (repaid, redeemed, retired…), reads as done (completed, issued, redeemed… and no "intend", "expect",
  "will"), and is dated after the year-end. One that reads as a plan is "only planned".
- The original maturity (a dated quote, after the year-end) and the amount; the new maturity (a dated quote) for a
  refinancing, an extension or a repayment from new borrowing; the source (a quote showing cash on hand, proceeds of
  new borrowing, or proceeds of a share issue) for a repayment. An executed item missing any of these is
  **unresolved** too (it can't be placed).
- Maturity years: year 1 is the 12 months after the year-end, year 2 the next 12, … after year 5. A date given only as
  a year (or month) spanning two goes in the earlier year (more weight: the conservative side). The amount taken out
  is capped at what the table shows due in that year.
- The cash used for a repayment from cash is the amount repaid, or the cash paid when the filing gives it.
- Without a tagged maturity table (year 1 from the current portion of debt), only year 1 changes: a new maturity
  isn't placed, so the refinancing test stays unavailable.
- The verified executed items are applied even when another item is unresolved; the trigger is still unresolved.
- Without any passage and no DSCR T1 record, there is nothing to read: nothing found, no adjustment, no model call.

`set_ladder` recomputes the weighted maturities and `rescore_liquidity` (shared with Liquidity T1) reruns both tests,
keeping a manual revolver figure and applying a covenant relief's +1 again. The column keeps `maturity_adjustment`
(`calculated`, `coverage_calculated`, `coverage_score_calculated`, `refinancing_calculated`,
`refinancing_score_calculated`, `ladder_calculated`, `cash_calculated`, `recomputed`, `score`, `notes`). Liquidity T1
and T2 share one escalation.

## Trigger Agent: Liquidity T3

`liquidity_t3.py`, prompt `fo_trigger_liquidity_t3`. It runs last, after Liquidity T2 (on the maturity table with
T2's moves in), only when `payload.gate.agent_triggers` is "enabled", the measure has a score and a tagged maturity
table, and not after a covenant breach not waived.

| Trigger | Why | What the agent checks |
|---|---|---|
| The largest single year of maturities in years 2–5 is ≥ 25% of total debt (the maturity table's total) | A concentrated wall depends on refinancing; how easy that is depends on the kind of debt | For the debt in the wall year, the debt note: how much is secured bank debt or mortgages, how much unsecured bonds |

The agent reads the debt note's and MD&A liquidity's paragraphs and tables on notes, bonds, loans, mortgages, security
or maturities (W01…) and lists each instrument maturing in the wall year with its kind, principal and maturity date
in one verbatim quote. Code keeps an instrument only when the amount and a date inside the wall year are in its
quote and the quote shows its kind (secured and a bank loan or facility; a mortgage; notes, bonds or debentures that
are unsecured or senior notes). The shares are of the wall year's maturities in the table; debt not identified counts
as neither:

| Wall year (verified) | Adjustment |
|---|---|
| More than 50% secured bank debt or mortgages | −1 on the score (not below 1) |
| More than 50% unsecured bonds | +1 (not above 10) |
| Mixed (or not identified) | No change |

The column keeps `wall_adjustment` (`calculated`, `step`, `recomputed`, `score`). It is unresolved only when the
agent's answer and the verified evidence disagree; it shares the measure's escalation with Liquidity T1 and T2.

## Recomputations: what each step changed

Every step that can change a measure is recorded in the run as `payload.recomputations` (`reporting.recomputation`),
whether an agent's verified evidence drove it or the engine did:

- **Pre-checks**: the measures are also scored without each pre-check, so the acquisitions-and-divestitures, prepaid-
  model and covenant pre-checks each get a before and after (they change inputs before scoring, so there was no
  "score before" otherwise).
- **Triggers**: a snapshot before and after each trigger's adjustment, in run order.
- **The composite**: before and after each pre-check, and after the trigger agents' recomputations.

Each entry gives the step, the measure and column, every input row that changed (before → after, in millions), every
ratio that changed, and **the revised score, always** (marked "unchanged" when the recomputed ratio stays in the same
score). The page shows them on each trigger and pre-check card ("Recomputed"), on each measure card ("Recomputations",
step by step) and on the composite card. The agents never compute: they quote and classify, and code recomputes from
the verified evidence. Runs recorded before engine 1.22.0 have no recomputations.

**The agent's answer, recomputed (not applied).** When the agent's answer and its verified evidence disagree, the
verified outcome is what gets scored. For Debt/EBITDAR T1, the agent's answer is also worked out at face value
(`leverage_triggers.agent_reading`: "normalised" uses the one-offs it lists as not recurring, at the amounts it gives,
whether or not their quotes held up; "average EBITDAR" uses the average), applied to a copy of the measures
(`runner.agent_recomputation`, through the same `apply_leverage_t1`) and recorded as step `leverage_t1:agent`. It is
never applied to the scored measure. The T1 card shows it beside the verified result ("The agent's answer,
recomputed (not applied)"), the measure's Recomputations table lists it, and the escalation's reason gives the score
it implies, so the analyst can compare the two before accepting the engine's score or overriding it. An amount
without a readable unit is left out, and said so. Other triggers can supply a reading the same way
(`t["agent_reading"]["applies"]`: the trigger-result fields their apply function reads).

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
| DSCR T2: any answer | A different outcome (either direction) | DSCR (T2 is unresolved) |
| DSCR T3: any answer | A different outcome (either direction) | DSCR (T3 is unresolved) |
| ICR T1: any answer | A different outcome (either direction) | ICR (T1 is unresolved; so is "nothing found") |
| ICR T2: any answer | A different outcome (either direction) | ICR (T2 is unresolved; so is "neither disclosed") |
| ICR T3: any answer | A different outcome (either direction) | ICR (T3 is unresolved; so is "pre-tax income not found") |
| Debt/EBITDAR T1: any answer | A different outcome (either direction) | Debt/EBITDAR (T1 is unresolved; so is a remainder or no explanation) |
| Debt/EBITDAR T2: any answer | A different outcome (either direction) | Debt/EBITDAR (T2 is unresolved; so is an earmark or trapping not quantified) |
| Debt/EBITDAR T3: any answer | A different outcome (either direction) | Debt/EBITDAR (T3 is unresolved; so is no master lease, cross-default or parent guarantee) |
| Debt/EBITDAR T4: any answer | A different outcome (either direction) | Debt/EBITDAR (T4 is unresolved; so is a debt note that doesn't reconcile) |
| RCF/Debt T1: any answer | A different outcome (either direction) | RCF / debt (T1 is unresolved; so is a one-off identified but not quantified) |
| RCF/Debt T2: any answer | A different outcome (either direction) | RCF / debt (T2 is unresolved; so is an inflow not quantified or a release that can't be placed) |
| RCF/Debt T3: any answer | A different outcome (either direction) | RCF / debt (T3 is unresolved; so are distributions lumped without a split, or nothing found) |
| RCF/Debt T4: any answer | A different outcome (either direction) | RCF / debt (T4 is unresolved; so are buyouts not quantified, or nothing found) |
| EBITDAR Margin T1: any answer | A different outcome (either direction) | The EBITDAR margin (T1 is unresolved; so is an item's location unclear) |
| EBITDAR Margin T2: any answer | A different outcome (either direction) | The EBITDAR margin (T2 is unresolved; so are a single segment, segment profit not comparable, or results that don't point either way) |
| DSO T1: any answer | A different outcome (either direction) | DSO (T1 is unresolved; so are DSO up with a cause and no fix, or a mechanical cause not quantified) |
| Liquidity T1: any answer | A different outcome (either direction) | Liquidity and debt maturity (T1 is unresolved; so is a revolver whose availability can't be worked out) |
| Liquidity T2: any answer | A different outcome (either direction) | Liquidity and debt maturity (T2 is unresolved; so is a refinancing only planned, or executed but not placed) |
| Liquidity T3: any answer | A different outcome (either direction) | Liquidity and debt maturity (T3 is unresolved) |

A measure gets one decision even when several checks escalate it: the escalation merges their reasons (`checks`).
When the engine is the more severe side it already escalates. The run completes once every escalation is decided.
The page lists the held-back quotes, so the analyst can see why the two differ. `payload.gate.disagreements` lists
the escalation ids.

**Analyst overrides.** The run's `escalations` list holds what waits for an analyst. A `measure_override` (today:
liquidity after a covenant breach; DSO or Debt/EBITDAR after an unresolved deal; DSCR after an unresolved trigger T2, T3 or T1; ICR after an unresolved ICR T2, T3 or T1; Debt/EBITDAR after an unresolved Debt/EBITDAR T4, T1, T2 or T3; RCF / debt after an unresolved RCF/Debt T1-T4; the EBITDAR margin after an unresolved EBITDAR Margin T1 or T2; DSO after an unresolved DSO T1; liquidity and debt maturity after an unresolved Liquidity T1, T2 or T3) is decided
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
dscr_t2.dscr_t2_trigger          CapEx > 1.5× depreciation and triggers enabled -> passages -> agent
                                 (required) -> validation -> committed / split / depreciation / held
dscr_t2.apply_t2                 DSCR recomputed with maintenance CapEx on the headline column
dscr_t3.dscr_t3_trigger          |cash taxes − current tax| > 5% of CFADS and triggers enabled -> passages ->
                                 agent (required) -> validation -> backed out / structural / low confidence
dscr_t3.apply_t3                 DSCR recomputed with one-offs backed out of cash taxes; low data confidence
dscr_triggers.dscr_t1_trigger    DSCR score ≥ 9 (after T2 and T3) and triggers enabled -> passages -> agent (required) ->
                                 validation -> principal removed / −2 / held
dscr_triggers.apply_t1           DSCR recomputed on the headline column
icr_t2 / icr_t3 / icr_triggers   ICR T2, T3, then T1: ICR recomputed on its latest column
leverage_t4.leverage_t4_trigger  total debt from a fallback and triggers enabled -> passages -> agent (required) ->
                                 validation -> reconciles / doesn't reconcile
leverage_t4.apply_leverage_t4   Debt / EBITDAR recomputed with the reconciled total debt
leverage_triggers.leverage_t1_trigger  |net one-offs| > 5% of EBITDAR or EBITDAR ≤ 0 and triggers enabled ->
                                 passages -> agent (required) -> validation -> normalised / operational /
                                 3-year average / unresolved
leverage_triggers.apply_leverage_t1  Debt/EBITDAR recomputed and rescored
leverage_t2.leverage_t2_trigger  |gross score − net score| ≥ 2 and triggers enabled -> passages -> agent
                                 (required) -> validation -> cash reduced / debt maturities / freely available /
                                 unresolved
leverage_t2.apply_leverage_t2   Net debt / EBITDAR recomputed on cash less earmarked and trapped cash
leverage_t3.leverage_t3_trigger  score with leases − score without ≥ 2 and triggers enabled -> passages -> agent
                                 (required) -> validation -> disclosed / not disclosed
leverage_t3.apply_leverage_t3   +1 on the composite leverage score when disclosed
margin_triggers.margin_t1_trigger  Debt/EBITDAR T1 removed one-offs and triggers enabled -> passages -> agent
                                 (required) -> validation -> revenue adjusted / outside revenue / unresolved
margin_triggers.apply_margin_t1  the margin on T1's EBITDAR (and revenue less one-offs booked in it)
margin_t2.margin_t2_trigger    margin score >= 9 or <= 2, > 1 segment, a 30% segment 5 points off and triggers
                                 enabled -> segment table -> passages -> agent (required) -> validation ->
                                 moved toward p / no adjustment / unresolved
margin_t2.apply_margin_t2      1 point toward p, never past it, when moved toward p
rcf_triggers.rcf_t1_trigger     RCF / debt score ≥ 9 or ratio < 0 and triggers enabled -> passages -> agent
                                 (required) -> validation -> added back / no one-offs / unresolved
rcf_triggers.apply_rcf_t1       RCF / debt recomputed with the one-offs added back
rcf_t2.rcf_t2_trigger           tagged inflows > 5% of FFO or FFO up 25% (any score) and triggers enabled ->
                                 passages -> agent (required) -> validation -> removed / no adjustment / unresolved
rcf_t2.apply_rcf_t2             CFO and FFO less the one-off inflows, RCF / debt recomputed
rcf_t3.rcf_t3_trigger           NCI distributions untagged while NCI is material and triggers enabled -> passages
                                 -> agent (required) -> validation -> cash paid / declared / none / unresolved
rcf_t3.apply_rcf_t3             RCF / debt recomputed with the distributions to NCI found
rcf_t4.rcf_t4_trigger           NCI distributions from PaymentsToMinorityShareholders and triggers enabled ->
                                 passages -> agent (required) -> validation -> buyouts excluded / distributions
                                 only / unresolved
rcf_t4.apply_rcf_t4             RCF / debt recomputed with distributions only
dso_triggers.dso_t1_trigger     |change in DSO| >= 5 days (after the deal correction) and triggers enabled ->
                                 passages -> agent (required) -> validation -> recomputed / recovered (-1) /
                                 unresolved / no cause
dso_triggers.apply_dso_t1       receivables + mechanical items in both years, DSO rescored; or -1 on the score
liquidity_triggers.liquidity_t1_trigger  revolver availability not found (no covenant breach, no manual figure) and
                                 triggers enabled -> passages -> agent (required) -> validation -> stated /
                                 computed / no revolver / unresolved
liquidity_triggers.apply_liquidity_t1  revolver availability into both tests, liquidity rescored
liquidity_t2.liquidity_t2_trigger  coverage or refinancing score >= 9 after T1 (no covenant breach) and triggers
                                 enabled -> passages + DSCR T1's record -> agent (required) -> validation ->
                                 recomputed / already long-term / unresolved / nothing found
liquidity_t2.apply_liquidity_t2  debt moved to its new maturity year or removed (and cash used), liquidity rescored
liquidity_t3.liquidity_t3_trigger  largest year of years 2-5 >= 25% of total debt (maturity table, no breach) and
                                 triggers enabled -> passages -> agent (required) -> validation -> secured / unsecured
                                 / mixed
liquidity_t3.apply_liquidity_t3  -1 / +1 / no change on the score (1-10); composite_risk again
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
| `dscr_t2.py` | Trigger Agent, DSCR T2: MD&A capital-resources / segment-note passages, the agent call, evidence checks, maintenance CapEx correction |
| `dscr_t3.py` | Trigger Agent, DSCR T3: income tax note / MD&A passages, the agent call, evidence checks, cash-tax one-off correction, low data confidence |
| `icr_triggers.py` | Trigger Agent, ICR T1: MD&A / note passages, the agent call, evidence checks, EBIT one-off and swap mark-to-market corrections |
| `icr_t2.py` | Trigger Agent, ICR T2: debt note / MD&A / note passages, the agent call, evidence checks, gross interest base, ICR n/a on net interest income, the operating-tag flag |
| `icr_t3.py` | Trigger Agent, ICR T3: income statement / MD&A / note passages, the agent call, evidence checks, EBIT = pre-tax income + interest expense |
| `leverage_triggers.py` | Trigger Agent, Debt/EBITDAR T1: MD&A / note passages, the agent call, evidence checks, normalised or 3-year average EBITDAR, Debt/EBITDAR rescored |
| `leverage_t2.py` | Trigger Agent, Debt/EBITDAR T2: MD&A / note passages on cash, the agent call, evidence checks, earmarked and trapped cash out of net debt, Net debt / EBITDAR rescored |
| `leverage_t4.py` | Trigger Agent, Debt/EBITDAR T4: balance-sheet / debt-note / lease-note passages, the agent call, evidence checks, the reconciliation (instruments to the note's total to the balance sheet, finance leases once), Debt/EBITDAR rescored |
| `margin_triggers.py` | Trigger Agent, EBITDAR Margin T1: income statement / MD&A / note passages, where T1's one-offs were booked, the margin on T1's EBITDAR and corrected revenue |
| `margin_t2.py` | Trigger Agent, EBITDAR Margin T2: the segment table from the segment note's tags, the outlier and the direction against p, segment note / MD&A passages, the agent call, evidence checks, 1 point toward p |
| `rcf_t4.py` | Trigger Agent, RCF/Debt T4: cash flow / equity statement / note passages on distributions against purchases of NCI, the agent call, evidence checks, buyouts out of the NCI distributions |
| `rcf_t3.py` | Trigger Agent, RCF/Debt T3: NCI materiality, cash flow / equity statement / note passages on NCI distributions, the agent call, evidence checks, distributions to NCI into RCF |
| `rcf_t2.py` | Trigger Agent, RCF/Debt T2: cash flow statement / MD&A / note passages on cash receipts, the agent call, evidence checks, placement against the working-capital lines, recurrence, inflows out of CFO and FFO |
| `dso_triggers.py` | Trigger Agent, DSO T1: MD&A / note / subsequent-events passages on receivables and collections, the agent call, evidence checks (derecognised sales only, unusual write-offs, a recovery after the year-end), mechanical items added back in both years or −1, DSO rescored |
| `liquidity_triggers.py` | Trigger Agent, Liquidity T1: debt-note / MD&A liquidity passages on the revolver, the agent call, evidence checks (year-end dates, nil amounts, asset-based borrowing base), availability stated or worked out per facility, liquidity rescored |
| `liquidity_t2.py` | Trigger Agent, Liquidity T2: subsequent-events / debt-note / MD&A liquidity / going-concern passages, DSCR T1's refinancing record reused, the agent call, evidence checks (executed after the year-end, dated maturities, source of repayment), debt moved between maturity years or removed, liquidity rescored |
| `liquidity_t3.py` | Trigger Agent, Liquidity T3: the maturity wall (largest year of years 2-5 against total debt), debt-note / MD&A liquidity passages, the agent call, evidence checks (amount and a wall-year date in the quote, the kind's words), −1 secured / +1 unsecured |
| `rcf_triggers.py` | Trigger Agent, RCF/Debt T1: MD&A / note passages on one-off cash items and special dividends, the agent call, evidence checks, one-offs added back, RCF / debt rescored |
| `leverage_t3.py` | Trigger Agent, Debt/EBITDAR T3: the score without leases, lease and guarantee passages, the agent call, evidence checks, +1 for a master lease, cross-default or parent guarantee |
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
