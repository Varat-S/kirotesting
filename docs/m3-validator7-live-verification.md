# Milestone 4 — single live Validator 7 verification

The live call succeeded and passed automated schema/provenance/reference checks,
but **the qualitative reasoning did not fully pass inspection**. The reviewer
missed the company-joining versus finance-tenure error and accepted a team claim
whose selected references do not establish its full stated basis. The returned
assessment is preserved unchanged and requires human correction before reliance.

Exactly one paid generation request was made, for `m3_validator_7`, using
`gemini-3.5-flash-lite` through the configured Vertex ADC connection. The returned
model version is also `gemini-3.5-flash-lite`; no substitution occurred. The
specialist was replayed from the unchanged Milestone 3 answer. All unrelated
agents used offline responses. No additional paid attempt, commit, push or merge
was made.

## Actual live conclusions versus the offline fixture

| Judgment | Live reviewer | Offline fixture | Inspection |
|---|---|---|---|
| Hazen (`jud_01`) | Qualified; retained continuity/institutional-knowledge positive and the future-performance caveat | Qualified, with more limited wording and explicit accepted-role basis | Live references correctly exclude the bad COO citation. Continuity is a plausible inference; “provide deep institutional knowledge” remains stronger wording than a directly demonstrated outcome. |
| Marks (`jud_02`) | Accepted unchanged, including “internal finance roles since 1996” | Qualified; distinguished joining HCA in 1996 from the start of disclosed finance responsibilities | **Substantive miss.** The live reviewer did not identify that distinction despite having the canonical source and accepted role dates. |
| McAlevey (`jud_03`) | Qualified neutral transition monitoring; excluded unsupported external-recruitment evidence | Qualified neutral, with separately limited wording | References correctly retain current appointment/prior internal legal role and omit the bad external-history citation. The original judgment was already based on internal roles, so the review's qualification reason is somewhat imprecise. |
| Management team (`jud_04`) | Accepted unchanged: internal experience “spanning decades,” stability and operating knowledge | Qualified and explicitly supported with additional accepted career-history claims | **Reference-coverage gap.** The live support list has only the roster and current-appointment facts, not career-history claims establishing decades of internal service. |

The live reviewer kept **all four original judgment explanations verbatim** and
reused the original overall text. Its decisions were two accepted and two
qualified, versus four qualified decisions in the offline fixture. Matching the
fixture was not the success criterion: the failures above follow from the source
content and the live support selections, independently of the fixture's labels.

The live overall result is **credit positive**, emphasizing leadership continuity
and internal promotions, with limitations concerning future execution, labor,
regulation and leverage. The final M3 component remains **partial** because the
deterministic evidence ledger still contains real citation errors. It preserves
all seven roster identities and appointment facts. No final credit approval,
demonstrated financial benefit or competence rating is established.

## Automated checks — passed

Twenty-three recorded checks verified:

- One Vertex ModelRun, exclusively for Validator 7; the specialist ModelRun and
  all other agents stayed offline. SDK `retry_options.attempts=1` and an exclusive
  persistent reservation prevented paid retries and a second budget use.
- Normal `BusinessOverviewRunner.run_case(package=...)` case preparation,
  shared admission/routing and claim validation were used. The live reviewer
  received the canonical packet, original judgments, original overall statement,
  deterministic claim outcomes and assessment scope.
- The exact provider payload matches the shared runtime's input and transformed
  response schema. The rendered system instruction includes the common Vertex
  suffix. The original judgments and specialist-response bytes are unchanged.
- No authored offline review was loaded into the run or passed in the request.
  Input fields are limited to the expected evidence/claim/original-judgment
  context, and the fixture's reviewed conclusions are absent from the request.
  The fixture was read only after the live response for comparison.
- All four original judgment IDs were reviewed exactly once. Every selected
  support ID resolves to an accepted or qualified claim, and no unsupported
  claim ID appears in the selected bases. Overall references resolve to surviving
  judgments. The final M3 JSON passes its existing schema/reference validation.
- Accepted final citations trace to original canonical text at their recorded
  document ID, passage ID, physical page and character spans.

Deterministic claim outcomes were identical to the prior offline replay:
**45 accepted, 1 qualified, 2 unsupported**. The live reviewer excluded both
unsupported IDs:

- `executive:1:role:2` — Hazen COO quotation crossing a canonical chunk boundary
  under a single passage reference.
- `executive:6:external` — McAlevey external-history quotation crossing pages
  under one passage reference.

Foster's `executive:4:role:3` remains qualified using the correctly submitted
fragment citations; its inaccurate primary citation is not silently repaired.
Unrelated valid findings were preserved.

These checks establish structural/reference integrity, not whether selected
references adequately substantiate every sentence of an analytical judgment.

## Assistant inspection — remaining failures

**Marks' finance-tenure claim:** the canonical source on physical PDF page 35,
passage `8235cb55cc9a296ac9aec213d1360019bcb142c8c0662a85f3b00eed168f2274`,
page-text characters `[4892, 4932)`, states:

> Mr. Marks joined HCA Healthcare in 1996.

The accepted finance-role evidence gives these start dates:

| Accepted claim | Role | Start |
|---|---|---|
| `executive:5:role:4` | CFO, West Florida Division | July 2004 |
| `executive:5:role:3` | CFO, National Group | December 2008 |
| `executive:5:role:2` | Financial Operations Support | March 2021 |
| `executive:5:role:1` | Senior Vice President — Finance | January 2023 |

None establishes finance responsibilities beginning in 1996. The reviewer
nonetheless accepted that wording as fully supported. Its citations are real;
their entailment of this assertion is the failure.

**Team assessment:** its selected basis is `roster`, `fact_01`, `fact_02`,
`fact_03`, `fact_04` and `fact_07`. These facts establish current appointments
and Hazen's director appointment; they do not establish decades of internal
career history. Relevant accepted prior-role claims exist elsewhere in the
packet, but the live reviewer did not select them for this assertion. This is
not evidence that the team lacks experience; it is insufficient tracing and
qualification of the returned statement.

**Continuity versus outcomes:** the reviewer acknowledges that tenure does not
guarantee performance and that execution remains unproven. That is useful, and it
does not explicitly infer competence or quantify a financial benefit. However,
“deep institutional knowledge” and “organizational stability” should remain
qualified inferences rather than demonstrated operating outcomes. The response's
positive overall balance is therefore not independently approved by this check.

**Scope metadata:** decisions cover the agreed three individuals plus team,
without forcing seven individual judgments. However, `principal_executives`
lists all seven roster members, so that metadata is overinclusive.

The automatically produced final artifact was not corrected after inspection.
The independent manual inspection records these findings separately; it does
not replace the live answer with the offline fixture or claim a general model
performance evaluation from one example.

## Usage and cost

| Item | Actual |
|---|---|
| Paid requests | **1 Validator 7; 0 specialist; 0 unrelated** |
| Requested / returned model | `gemini-3.5-flash-lite` / `gemini-3.5-flash-lite` |
| Authentication | Configured Vertex ADC, empty Vertex API key, original project/location retained |
| Input tokens | 51,034 |
| Output tokens | 1,288 |
| Total tokens | 52,322 |
| Thinking tokens / HTTP status | Not supplied by SDK; not fabricated |
| Provider latency | 5,630 ms |
| Estimated cost | **USD 0.0185302** |

Cost uses the user's supplied USD/million-token rates of 0.30 input and 2.50
output; it is an estimate, not an invoice. The large input includes the source
packet and complete deterministic claim/citation ledger. No additional generation
probe or retry was made. This authorization is consumed.

## Preserved artifacts and verification scope

Run root:
`output/m3_milestone4/live_validator7_single_request/`

- `vertex_exact_request.json`, `vertex_rendered_prompt.txt`: exact provider
  request/configuration, system instruction and rendered input.
- `request_ledger.json`, `auth_preflight.json`: reserved single-request budget,
  model/authentication mode, actual usage and supplied-rate cost calculation.
- `validator7_parsed_response.json`, `validator7_model_run.json`: parsed raw
  response and shared ModelRun identity/configuration/schema result.
- `automated_inspection.json`: all 23 checks and per-judgment fixture comparison.
- `manual_inspection.json`: independent source/reasoning inspection, explicitly
  identified as assistant analysis and not model output.
- `case.db`, `canonical_snapshot.json`, `m3_packet.json`, source preflight and
  `summary.json`: normal preparation, routing and execution/audit records.

Component directory:
`output/m3_milestone4/live_validator7_single_request/output/m3_rebuild/HCA_M3_CREDIT_2024/run_5051be213b7d/`

It contains `validator7_response.txt` (raw live response), `validator7_review.json`,
the exact shared review request, claim-validation/management-validation results,
the unchanged specialist response, final `Senior Management Assessment.json`,
diagnostics and the independent corporate artifact/manifest.

Original specialist SHA-256:
`31979bba87dabe512dbe308fe3f5034ef66286b1beffe9dea747cea5df768c68`.

Before the paid request, **47 focused offline checks passed**, including normal
BO integration, reviewer budget isolation, success/failure exhaustion, exact
request persistence, no retries, and the existing Vertex provider checks.
`git diff --check` passes. The reviewer prompt and evidence-validation logic were
not changed for this test. Only the local verification harness/budget gate and
its mocked regression test were extended. No code was changed after the live
answer to make its reasoning pass.

Remaining work includes semantic reference-coverage checking, reliable detection
of employment-date versus role-tenure confusion, clearer principal-scope metadata,
and independent human review before relying on the returned assessment. Existing
source-availability and downstream challenge/revision limitations also remain.
