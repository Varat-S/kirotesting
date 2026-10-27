# Module 3 retirement and verification — 27 October 2026

The rebuilt two-component Module 3 is the sole supported M3 architecture on
`m3-rebuild`. The old specialist cannot be selected, invoked as fallback, or
requested through challenge remediation. All changes remain uncommitted.
The live extraction test did **not** produce an analytically complete management
assessment: seven names were extracted, zero profiles passed evidence admission.

## Retirement and dependencies

The [inventory](m3-retirement-inventory.json) records **90 deleted tracked paths**:
14 legacy BO implementation/rendering modules, two experimental schemas, 50 old
fixtures/examples, six old M3 reports, 15 legacy-only test modules and three
legacy-dependent BO/FO/SO demonstration artifacts. Old specialist prompts and
schema constants, Validator 7, hypothesis mappings, M4 relationship injection,
registry jobs and graph nodes, old standalone/replay commands and architecture
selection/fallback branches were removed from shared files.

Shared code was inspected before removal. The runner and completion bridge remain
for M1/M2/M4; general single-specialist and hybrid execution remain for other
specialists. Evidence/quotation/numeric safeguards, scoring, challenge handling,
Vertex authentication/model configuration and generic BO reporting remain in use.
Registry/viewer access controls and BO/FO partial memo adapters are shared and were
updated, rather than removed. M4 keeps its own debt validation without assuming
M3's simulated diagram establishes legal relationships. Retention reasons and
paths are in the inventory. The original user structure inputs were preserved in
`examples/m3_rebuild`, independently of the retired fixture directory.

Surviving historical outputs are read-only, labelled **UNSUPPORTED LEGACY M3** /
`unsupported_legacy`, and skipped for active use. Recorded legacy specialist
imports are explicitly rejected. Historical task records under `.agents/tasks`
and the superseded rebuild milestone report are not runtime inputs or selectable
backups. Git history preserves removed code.

## Default workflow and compatibility

Every normal BO run selects M3 sources through `SourcePackage.module3`. Shared case
preparation admits the corporate JSON, captures the selected filing's narrative,
and the existing router persists the only execution packet. Corporate JSON and
management narrative/extraction inputs have independent statuses. Missing or
rejected inputs yield explicit `not_attempted` reasons; a failure in one component
does not hide the other. A saved extraction or explicitly authorized extractor is
required for management; a filing alone never silently causes a paid request.
Both components also run independently through `app.cli run-m3` or
`python -m app.services.bo.m3_rebuild`.

Normal BO commands no longer accept direct M3 file or manifest flags. Company,
period, cutoff, document identity and hashes are carried through the canonical
snapshot and packet. Existing rebuild manifests remain consumable as historical
artifacts through the registry/adapters, but cannot bypass admission in a new BO
run. The old `--m3-rebuild-manifest` switch, `run-m3-rebuild` alias and old
`run-m3 --responses/--live` syntax were removed. See the
[current runbook](m3-rebuild.md) for commands and package examples.

Legacy `m3_assessment`, verified-entity and M3 hypothesis contracts are no longer
accepted. Dedicated typed management claims carry observation and citation IDs
through BO/SO to the actual HTML partial credit memo. The SO/partial memo schema
is `structural-overview-assessment-1.2` and transformation `bo-to-so-1.2`.
Registry versions are default 1.2.0, BO v2 2.1.0 and BO v3 3.1.0. The default
registry has 26 model jobs, BO v3 29, after removal of the old M3 job. Operations
workforce/capex remains; litigation/material events are owned by M2. The old
subjective management score is not fabricated or replaced with a neutral value;
unchanged scoring policy can leave the business score unavailable.

## Defects and offline acceptance

Before the fix, the synthetic OtherCo CFO biography was admitted as internal,
producing a positive mitigation, and a mixed dotted/plain roster was incorrectly
considered a complete one-person roster. Those observed reproductions were fixed:

- Role-specific selected-company/internal-entity attribution is required.
  Explicit other employers are rejected; generic “previously served as” is
  insufficient. Dated company context and repeated role titles are handled
  conservatively without HCA-specific exceptions. Historical external recruitment
  does not turn a later internal promotion into a current external hire.
- Bounded dotted and whitespace roster rows, wrapped positions and biographies
  across pages are reconciled. Unknown content, duplicates and missing biographies
  make coverage partial, retaining confidently admitted profiles. Universal PDF
  layout support is not claimed.
- Unsupported facts leave a precise rejected-profile record and coverage/status
  effect. Quote accuracy and semantic support are checked separately.
- New typed narrative validation admits bounded transition risk, internal
  experience mitigation and information limitations, with resolvable citations;
  unsupported text, directions, observations and references are rejected even
  after rehashing.

The manual HCA fixture still accounts for seven executives, correct appointment
precision and inclusive review window **2022-02-01 through 2025-02-01**. Recent
internal promotions retain both adverse transition and positive experience
observations. Corporate conversion retains **14 nodes, 13 ownership links and
five guarantor flags**. Geometry, independent failure handling, artifact reuse,
legacy rejection, M4, FO and downstream tests pass.

The offline end-to-end memo acceptance passed. Its saved example is
`output/m3_retirement/memo_acceptance/partial_credit_assessment.{json,html}`,
explicitly labelled synthetic. It shows all three intended management statements,
source appendix and simulated-corporate disclaimer through production renderers.
The actual boundary is an **incomplete BO/FO partial credit memo**, with no final
rating, facility approval or completed credit-decision stage. This test does not
claim that those absent stages have been implemented.

The new M3 tests replace legacy-only coverage; shared registry/HTTP/PDF tests
were ported to current artifacts. Shared recorded-completion tests now generate
current offline recordings and execute evidence-integrity, scoring, challenge,
replay and legacy-rejection safeguards instead of skipping for an absent old R5
artifact. No failing safeguard was deleted merely to conceal a regression.

Commands use the original checkout's existing virtual environment with
`PYTHONDONTWRITEBYTECODE=1`, pytest `-q -p no:cacheprovider`:

- **Before either paid request:** full offline gate: **1824 passed, 141 skipped,
  one warning**, 97.14 seconds. Log: `output/m3_retirement/pre_paid_offline_gate.txt`.
- After SO schema version update: targeted M3/M4/BO/SO/FO/registry gate:
  **200 passed, two skipped, one warning**. Log:
  `output/m3_retirement/post_contract_targeted_gate.txt`.
- Current recorded-completion replacement tests: **14 passed**. Log:
  `output/m3_retirement/recorded_completion_gate.txt`.
- Final full offline gate after replacing the legacy-recording tests: **1837 passed,
  129 skipped, one warning**, 107.60 seconds. Log:
  `output/m3_retirement/final_full_offline_gate.txt`.
- Final shared-code cleanup checks: **59 passed, one warning**. Log:
  `output/m3_retirement/final_cleanup_gate.txt`.
- `git diff --check` passed.

Skipped checks depend on absent optional genuine recordings/benchmark files,
explicit live gates or an optional HTML-to-PDF backend. All live execution remained
outside pytest, in the bounded harness. The warning is the existing Starlette/httpx TestClient deprecation.

## Exactly two authorized paid requests

Both authorized requests were executed sequentially, after the pre-paid offline
gate. No other BO/SO agents were run live. The configured and response-reported
model was **gemini-3.1-flash-lite**, Vertex location `global`, temperature 0,
maximum output 12000, SDK 2.29.0. Adapter `m3-rebuild-vertex-1.1`, prompt
`m3-management-facts-1.1`, extraction schema `m3-management-extraction-1.0`.
`http_options.retry_options.attempts=1` disables automatic SDK retries (attempts
includes the initial request). A persistent ledger reserves requests before
execution and denies a third; mocked offline budget tests verify those bounds.

| Request | Result | Input tokens | Output tokens | Total |
|---|---|---:|---:|---:|
| 1: tiny synthetic probe | Answer returned; parse, strict schema, evidence, complete roster/profile and mixed-rule gates passed | 912 | 291 | 1203 |
| 2: HCA FY2024 | Answer returned; parse/schema passed; evidence admission partial, seven raw profiles / zero admitted | 2416 | 2954 | 5370 |
| Total | Exactly two attempted | 3328 | 3245 | 6573 |

Returned SDK metadata did not expose HTTP status; it is **unavailable**, not an
assumed 200. Estimated cost is **unavailable** because no documented pricing basis
was supplied. Request contents/schema hashes, latency, usage and configuration
are preserved without credentials in
`output/m3_retirement/authorized_live_20261027/request_ledger.json`.

Only the selected local FY2024 filing executive pages (physical 35–36), source IDs,
company/period and disclosed roster were sent for HCA. No manually transcribed
extraction or expected rule answers were sent. The cover was independently
verified on physical page 5; the disclosed roster date is February 1, 2025.
No FY2025 or web evidence was substituted.

## Actual HCA findings

The untouched answer has all seven correct roster names/suffixes and appointment
values/precisions, plus the 18 prior role/range pairs represented in the verified
fixture and checked against the filing. This does not prove universal biographical
completeness. Of 25 main/history quotation fields, 19 match the assigned extracted
page exactly, five change PDF intraword spacing and one has a wrong page reference.

| Executive | Appointment | Actual admission issue |
|---|---|---|
| Samuel N. Hazen | 2019-01, month | `W estern` typography mismatch; compound CEO/director title also needs role-specific handling |
| Jennifer L. Berres | 2019-11-01, day | `H uman` typography mismatch |
| Michael S. Cuffe, M.D. | 2022-01-01, day | `P hysician` typography mismatch |
| Jon M. Foster | 2023-01-01, day | Exact quotes but overly narrow pronoun/biography-context validator |
| Michael A. Marks | 2024-05-01, day | Two Finance/Financial typography mismatches; quotes omit wider company context |
| Michael R. McAlevey | 2024-04-01, day | Short quote lacks employer context; historical GE recruitment incorrectly placed in current external-recruitment field |
| Sammie S. Mosier | 2021-12-01, day | Current quote points to p35 but belongs to p36 |

All seven profiles were held back atomically. The actual assessment is partial:
expected seven, admitted zero, missing seven; substantive outcome unavailable and
Rule D information negative only. This is **not** evidence of poor HCA management.
No external-employer role created an internal mitigation and no historical GE
employment created an integration negative. Corporate conversion independently
succeeded with 14/13/five. The live management output is not ready for unattended
use. Valid JSON/schema and accurate appointment facts are separate from usable,
source-admitted analysis.

The detailed seven-person review, including each prior role/date, employer,
quotation, rejection and expected-versus-actual rules, is in
`output/m3_retirement/authorized_live_20261027/hca_expected_vs_actual.{md,json}`.
No repaired live answer was substituted. Diagnostic space removal was used only
to categorize mismatches, never to admit production evidence.

Artifacts under `output/m3_retirement/authorized_live_20261027/`:

- `synthetic_probe/`: raw response, validated extraction, assessment, sources and diagnostics.
- `hca_fy2024/management_response.txt`: raw answer, untouched.
- `hca_fy2024/management_extraction.json`: parsed actual answer.
- `hca_fy2024/management_validated_extraction.json`: schema-valid actual answer;
  **schema validity does not mean evidence admission**.
- `hca_fy2024/Senior Management Assessment.json`: actual partial assessment.
- `hca_fy2024/management_diagnostics.json`, `management_sources.json`: admission
  diagnostics and selected filing text/provenance.
- `hca_fy2024/Visualized HCA Healthcare Corporate structure.json`,
  `corporate_structure.svg`, `m3_rebuild_manifest.json`: independent corporate
  output and combined manifest.

A byte-identical copy of the HCA run is in `output/m3_rebuild/HCA_2024/hca_fy2024`
for normal viewer discovery; this is not another model run. The independent,
manual offline fixture result is separately labelled at
`output/m3_rebuild/HCA_2024/run_retirement_offline_verified`.

## Remaining limitations and preservation

Next work should use the saved raw answer offline to design source-anchored PDF
typography matching, biography-level person/employer attribution and separate
compound appointments, preserving strict quotation and external-hire semantics.
Atomic rejection amplifies one bad support field into loss of an otherwise useful
profile. Scanned/unfamiliar roster layouts still require review/OCR. Another paid
verification requires new authorization; this task's two-request budget is used.

No commit, push, merge or reset occurred. `m3-rebuild` remains at
`40ccb2b17c5bc2953b6ea1b11af7f1f645d80255`. The previous WIP branch/original
checkout remains `feature/daniel-module-3-wip` at
`2b2b3649b732992770b89959deffe55bf6355f1a`, with prior work preserved.

Automatic approval review rejected adding implicit paid Vertex extraction to
normal BO because it could exceed the explicit two-request budget. The completed
safe workflow requires saved extraction or explicit extractor authorization;
missing authorization has its own independent status. The bounded paid tests were
executed only within the existing authorization.
