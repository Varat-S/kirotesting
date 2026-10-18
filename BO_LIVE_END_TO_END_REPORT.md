# BO live end-to-end validation — HCA FY2025

**Assessment date:** 17 October 2026

**Verdict:** **CONDITIONALLY READY**

**Working branch:** `test/bo-live-end-to-end`
**Remote baseline:** `origin/feat/bo-validation-release` at `7dc095cbfbc643f44437f659a67105ec655aa10e`

## Executive result

The complete implemented BO v3 workflow ran against HCA Healthcare, Inc.'s real
FY2025 Form 10-K using live Vertex-backed agents. The final run
`ar_36d4a0589d224737` validated every specialist, the Business Orchestrator and
the independent Business Challenger. It produced an
`accepted_with_qualification` conclusion, a provisional illustrative risk band
of 3, and a handoff pending human review.

The output is suitable for controlled downstream integration because its source
references resolve, all four v3 modules are complete, missing information is
explicit, and challenge state remains visible. It is not suitable as an
independently validated credit conclusion: the review manifest is unverified,
one expected HCA concept (Galen College) was missed, the scoring policy is
illustrative, and the challenger left a medium wording objection unresolved.

## Configuration and source evidence

The repository `.env` contains non-empty values for `LLM_PROVIDER`,
`VERTEX_API_KEY`, `VERTEX_MODEL_NARROW`, `VERTEX_MODEL_ORCHESTRATOR`, and
`VERTEX_MODEL_CHALLENGE`. The application-level Vertex configuration check
passed without printing any credential. A machine-level `DEBUG=release`
variable is invalid for the boolean setting and overrides `.env`; validation
commands therefore set `DEBUG=false` explicitly. The worktree's ignored `.env`
was corrected to `DEBUG=false`.

| Item | Verified value |
|---|---|
| Provider | Vertex |
| Narrow model | `gemini-3.1-flash-lite` |
| Orchestrator model | `gemini-3.1-flash-lite` |
| Challenger model | `gemini-3.1-flash-lite` |
| Borrower | HCA Healthcare, Inc. (`HCA`) |
| Filing | Form 10-K, accession `0001193125-26-044769` |
| Filing date / report date | 10 February 2026 / 31 December 2025 |
| Evidence cutoff | 31 March 2026 |
| Evidence type | Actual SEC filing bundle; no synthetic evidence used in live runs |
| Bundle integrity | All 16 source-file SHA-256 values match `bundle.json` after the Git attribute fix |
| Ingestion | Real-filing tests pass; admitted evidence and provenance are persisted in the audit |

The Windows checkout initially changed the tracked filing from LF to CRLF,
causing all 16 manifest checks to fail. `.gitattributes` now treats
`examples/healthcare/*/filing/**` as byte-preserved content.

## Actual execution mode

The complete runner supports `single_pass` only. The experimental
`full_investigation` and `hybrid_selective` modes are wired only to the isolated
M1S2 comparison entry point, so neither was represented as a complete BO run.

The final live order was:

1. Eight primary v3 agents in parallel: M1S1–M1S5, M2, M3 and M4.
2. Targeted retrieval follow-up for M1S2, M2 and M4.
3. Coverage follow-up for M1S2.
4. Gated M1 Agent 6 resilience synthesis.
5. Preliminary deterministic BusinessRiskScore.
6. Business Orchestrator.
7. Independent Business Challenger.
8. Authoritative post-challenge score and structured reports.

No replayed, mocked or scripted response was used in the final analytical run.
The challenger did not request a specialist rerun.

## Live runs and remediation

| Run | Calls | Tokens in / out | Outcome | Purpose |
|---|---:|---:|---|---|
| `ar_192282f60a8a43c2` | 10 | 164,261 / 23,767 | Blocked | Baseline: orchestrator rejected for an unbound number and two qualification defects |
| `ar_01baa7bef2094bd2` | 15 | 267,774 / 36,311 | Blocked | Verified bounded orchestrator repair; repaired answer contained one hallucinated evidence ID |
| `ar_36d4a0589d224737` | 15 | 250,152 / 33,682 | Accepted with qualification | Final complete run, including independent challenge |

The final run took 51.062 seconds and used 283,834 total tokens. Monetary cost is
not determinable because no provider price inputs were supplied.

Two focused fixes were made without weakening validation:

- A rejected synthesis receives one orchestrator-only repair attempt containing
  the exact deterministic errors. Specialists are not rerun by this mechanism,
  and a second invalid response still fails closed.
- During that one repair attempt, an unknown model-supplied evidence ID may be
  replaced only by the canonical evidence IDs already attached to the claim's
  validated hypotheses. Claim text, type, parameters and source findings are
  unchanged; all remaining validation rules still apply.

## Specialist quality assessment

| Specialist | Representative supported result | Qualification or weakness | Outcome |
|---|---|---|---|
| M1S1 / BO1 — revenue quality | HCA is a hospital and outpatient-services provider; seasonal utilization is directly supported | Recurring demand is qualified because payer contracts do not guarantee volume; demand defensiveness is not fully established | Validated; 2 established and 2 qualified findings |
| M1S2 / BO2 — customers and contracts | Broad payer-category concentration is distinguished from individual counterparty concentration; reimbursement-rate contracts are not treated as volume guarantees | “High contractual dependence” remains a management-attributed interpretation; switching costs are not established | Validated after retrieval and coverage follow-ups; 2 established, 1 attributed, 1 qualified |
| M1S3 / BO3 — pricing | Revenue per equivalent admission is correctly separated from pure pricing and explicitly includes rate, service and payer mix limitations | Payer bargaining pressure is based on risk language and stays qualified; the validator remains lexical rather than a full entailment judge | Validated; 1 established and 2 qualified |
| M1S4 / BO4 — operations | Labor cost exposure is supported and management recruitment/retention initiatives are kept unproven | The expected Galen College concept was not recovered, so the workforce-development effect is absent rather than invented | Validated; 2 established and 1 attributed, with a material coverage omission |
| M1S5 / BO5 — competition | Competitive pressure from payer consolidation and patient competition is supported; leading market position is not claimed | Comparative local-market share/ranking evidence is missing; barriers-to-entry conclusions remain bounded | Validated; 1 established, 1 attributed and 1 qualified |
| M1A6 / BO6 — resilience | Rising labor costs interacting with limited pricing power is identified as a compound margin-compression mechanism | The effect is qualified and unquantified; no debt-capacity conclusion is made | Validated; 1 qualified interaction |

The additional v3 modules also completed: M2 produced two established industry
findings; M3 produced one established and four qualified management/governance
findings; M4 produced two established and one qualified strategy/capital
findings. Several M3 claims passed lexical validation while remaining weak
semantic inferences, which is a reason for human review rather than stronger
certainty.

## Orchestrator, challenger and scoring

The final orchestrator produced six claims, each linked to governed hypotheses
and admitted evidence. The resulting summary preserves government-reimbursement
dependence, Florida/Texas concentration, labor-cost exposure, limited pricing
power and the compound margin-compression risk.

The challenger executed independently and raised one medium objection against
`OA-001`: it treated the phrase “resilient, though not immune” as a linguistic
contradiction involving “immune.” This did not warrant reanalysis and remains
visible in the handoff. The objection did not materially improve the analysis;
it demonstrates that challenge execution and unresolved-objection governance
work, while also showing that challenger precision needs further evaluation.

Post-challenge scoring ran and persisted an authoritative-stage result. The band
is 3 with `provisional` status, 70% configured weight coverage, and missing
`customer_hhi`. The rubric is labelled `ILLUSTRATIVE - NOT BANK POLICY`; the
score is therefore not a final governed credit rating.

## Analytical limitations

- Citation validity was 75/75 in the unverified evaluation manifest, but that
  manifest explicitly states `unverified_pending_filing`; it is diagnostic, not
  independent ground truth.
- Five of six trap checks passed. The Galen existence/effectiveness check failed
  because the required reading was not found in validated findings.
- Concept recall was 5/7 in validated findings and 4/7 in the memo. Uninsured or
  uncompensated-care exposure and Galen were omitted.
- The final memo contains six retrieval gaps and three explicit open questions,
  including price-versus-mix decomposition, supplier concentration and margin
  sensitivity.
- No mitigant was validated as demonstrated. Four capabilities remain explicitly
  unproven.
- Logical entailment is still not generally proven by lexical validation. Some
  accepted phrasing, particularly governance and strategy claims, needs analyst
  review against the cited filing text.

## Downstream handoff assessment

The original live `bo-handoff-1.1` artifact carries module conclusions,
strengths, vulnerabilities, evidence IDs, limitations, score state, open
questions and challenge history. Validation found three contract gaps:
borrower/reporting-period fields were absent, FO handoffs had no explicit field,
and v3 compound risk was missed when it referenced the M1A6 interaction through
one synthesis hypothesis.

The handoff contract is now `bo-handoff-1.2`. It adds borrower name and entity
ID, as-of date, evidence-cutoff timestamp and `fo_handoffs`; compound risks also
resolve through a referenced multi-component hypothesis. A validated derived
`handoff_v1_2.json` is preserved beside the untouched original live artifact.
The final HCA run has no explicit FO handoff item, so the new list is honestly
empty; the unquantified margin sensitivity remains in missing information and
should be considered by FO.

## Artifacts

Final live artifacts are under:

`output/bo_hca_live/bo/HCA_2025_BO_LIVE/ar_36d4a0589d224737/`

- `business_overview.json`
- `business_overview.html`
- `audit_report.json`
- `audit_report.html`
- `handoff.json` (original live v1.1 artifact)
- `handoff_v1_2.json` (validated contract-remediated derivative)
- `evaluation.json`

The SQLite audit database is `data/bo_hca_live.db`. These evidence-heavy and
potentially sensitive artifacts remain Git-ignored.

## Verification commands

```powershell
$env:DEBUG='false'
C:\Users\pearn\NTU\BU5203\.venv\Scripts\python.exe -m pytest -q --basetemp output/pytest_bo_final `
  tests/unit/test_bo_remediation.py tests/unit/test_bo_roster_v3.py `
  tests/integration/test_bo_specialists_demo.py tests/integration/test_bo_pipeline.py `
  tests/integration/test_bo_v3_complete.py tests/integration/test_challenge_rerun.py `
  tests/integration/test_bo_real_filing.py tests/integration/test_bo_cli_workbench.py

C:\Users\pearn\NTU\BU5203\.venv\Scripts\python.exe -m app.cli `
  --database data/bo_hca_live.db --output output/bo_hca_live `
  run-bo-case HCA_2025_BO_LIVE `
  --package examples/healthcare/hca-2025/package.json `
  --manifest examples/healthcare/hca-2025/review_manifest.json `
  --live --roster v3
```

The focused regression result after orchestration remediation was 136 passed,
4 expected skips, and one third-party deprecation warning. The final complete
repository suite passed 1,260 tests with 119 documented optional/data-dependent
skips and the same third-party warning.

## Integration-readiness verdict

**CONDITIONALLY READY.** The final output is traceable and structurally suitable
for controlled Structural Overview integration using `bo-handoff-1.2`. Before
production or autonomous downstream use, a reviewer must verify the HCA manifest
against the filing, resolve or accept the challenger wording objection, assess
the missing Galen and uninsured-care topics, and treat the score as provisional
and illustrative. FO must independently quantify the margin and debt-capacity
effects; BO does not establish them.
