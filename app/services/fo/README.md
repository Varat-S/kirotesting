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
them, when a measure trips its trigger (today DSCR T2 and T3, then DSCR T1; ICR T2 and T3, then ICR T1; then Debt/EBITDAR T1 and T2). Every number is computed by code. The agents only
classify and quote the filing's passages; code verifies the quotes and re-derives the outcome.

## Run it

| Where | How |
|---|---|
| Browser | `python -m app.preview`, then `/` → "Financial Overview runs" (`/fo`): pick a folder, then Run (needs a live provider: the Run button is disabled without one) |
| CLI | `python -m app.cli run-fo-case [CASE_ID] --filing "HCA 10-K FY2025" --live` (or `--responses agent.json` offline, with `{"going_concern": ..., "covenants": ..., "acquisitions": ..., "prepaid": ..., "dscr_t1": ..., "dscr_t2": ..., "dscr_t3": ..., "icr_t1": ..., "icr_t2": ..., "icr_t3": ..., "leverage_t1": ..., "leverage_t2": ...}`; `dscr_t1` is needed only when DSCR scores 9 or more, `dscr_t2` only when CapEx is above 1.5× depreciation, `dscr_t3` only when the cash-tax gap is above 5% of CFADS, `icr_t1` only when ICR scores 8.5 or more, `icr_t2` only when ICR's interest is from a net or operating tag, `icr_t3` only when ICR's EBIT is operating income, `leverage_t1` only when net one-offs are above 5% of EBITDAR or EBITDAR is zero or negative, `leverage_t2` only when the gross and net leverage scores are 2 or more apart); one of the two is required |
| Analyst decision | the run's page (browser), or `python -m app.cli fo-review RUN_ID --decision continue\|stop --reviewer "…" --reason "…"` (going-concern fail), or `fo-override RUN_ID --measure liquidity\|dso\|leverage\|dscr\|icr --decision accept\|override [--score N] …` (an escalated measure) |
| Tests | `.venv\Scripts\python -m pytest tests/unit/test_fo_precheck.py tests/unit/test_fo_scoring.py tests/unit/test_fo_measures.py tests/unit/test_fo_covenants.py tests/unit/test_fo_acquisitions.py tests/unit/test_fo_prepaid.py tests/unit/test_fo_disagreements.py tests/unit/test_fo_dscr_trigger.py tests/unit/test_fo_dscr_t2.py tests/unit/test_fo_dscr_t3.py tests/unit/test_fo_icr_trigger.py tests/unit/test_fo_icr_t2.py tests/unit/test_fo_icr_t3.py tests/unit/test_fo_leverage_trigger.py tests/unit/test_fo_leverage_t2.py tests/integration/test_fo_runner_api.py -q` |

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

A measure gets one decision even when several checks escalate it: the escalation merges their reasons (`checks`).
When the engine is the more severe side it already escalates. The run completes once every escalation is decided.
The page lists the held-back quotes, so the analyst can see why the two differ. `payload.gate.disagreements` lists
the escalation ids.

**Analyst overrides.** The run's `escalations` list holds what waits for an analyst. A `measure_override` (today:
liquidity after a covenant breach; DSO or Debt/EBITDAR after an unresolved deal; DSCR after an unresolved trigger T2, T3 or T1; ICR after an unresolved ICR T2, T3 or T1; Debt/EBITDAR after an unresolved Debt/EBITDAR T1 or T2) is decided
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
leverage_triggers.leverage_t1_trigger  |net one-offs| > 5% of EBITDAR or EBITDAR ≤ 0 and triggers enabled ->
                                 passages -> agent (required) -> validation -> normalised / operational /
                                 3-year average / unresolved
leverage_triggers.apply_leverage_t1  Debt/EBITDAR recomputed and rescored
leverage_t2.leverage_t2_trigger  |gross score − net score| ≥ 2 and triggers enabled -> passages -> agent
                                 (required) -> validation -> cash reduced / debt maturities / freely available /
                                 unresolved
leverage_t2.apply_leverage_t2   Net debt / EBITDAR recomputed on cash less earmarked and trapped cash;
                                 composite_risk again
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
