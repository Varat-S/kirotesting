# Milestone 3 — management facts and credit assessment

Implemented in the existing `m3-rebuild` worktree; all changes remain uncommitted.
The specialist now identifies facts and interprets their credit implications in
one shared BO runtime call. The authorized HCA response did **not** pass evidence
admission. Its original failed run and subsequent offline partial replay are
preserved separately; no evidence checks were relaxed to admit its judgments.

## Input, execution and output

Normal `BusinessOverviewRunner.run_case(package=...)` performs case preparation,
shared admission, canonical snapshot creation and routing. Corporate conversion
still consumes its admitted structured payload independently. Management source
discovery operates on canonical passages, reconstructing physical pages for
discovery while retaining every individual passage ID and original quotation text.
The final packet is ordered by physical page and passage offset. HCA's selected
packet contains cover-page context and all six executive-section chunks on
physical pages 35–36; no PDF is reopened during M3 execution.

The new response contract `m3-management-response-2.0` contains executive facts,
extractive supporting facts and canonical citations, model-authored judgments
with credit_positive/credit_negative/neutral/insufficient_evidence signals,
fact references, explanations, uncertainties and a balanced overall assessment.
Multiple supporting citations allow a biography or appointment to span passages.

Python calculates tenure bounds and the dated review window. It validates closed
schemas, source IDs, exact quotations (whitespace normalization only), roster
coverage, role/date/employer binding, fact excerpts and judgment references. It
no longer assigns qualitative positives or negatives from tenure, promotion or
external recruitment. Model neutrality is preserved. Rejected facts cannot
support accepted judgments; unresolved judgments cannot support a validated
overall assessment. A limited lexical safeguard rejects claims that tenure proves
competence or performance; it is not a general analytical verifier.

`m3-management-assessment-2.0` retains the compact component interface and adds
facts, model assessment, validation detail and calculated tenure. Existing
BO/SO claim adapters carry accepted model observations with their citation IDs.
Historical v1 artifacts remain readable with their original meaning; facts-only
v1 extractions are explicitly insufficient for a new v2 assessment.

Management generation uses PromptRegistry → AgentRuntime → LLMClient → shared
VertexProviderBackend, with common ModelRun, AgentRun and audit persistence.
The normal runner accepts an explicitly authorized `m3_backend` or a saved v2
response in the source package. Offline fixtures also use the shared schema/audit
boundary. The former private Vertex SDK route and private two-request test batch
now reject execution.

## Exact prompt and preserved artifacts

The [exact final system prompt](m3-management-specialist-prompt.txt) includes the
common Vertex instruction suffix. It was checked against the provider's rendered
system instruction. Prompt version: `m3-management-credit-2.0`; registered ID:
`m3_management_assessment_v1.0`; prompt/schema hash:
`6732db5b55b11b24909f9748bdd98ec7f809e9182746b28f21a7cb58f53e22bd`.

Original paid run:
`output/m3_milestone3/hca_single_request/output/m3_rebuild/HCA_M3_CREDIT_2024/run_073f8dc6734a/`

- `management_rendered_prompt.txt`: exact system instruction plus canonical JSON input.
- `management_request.json`: instructions, packet and local response schema.
- `management_response.txt`: unchanged raw Gemini answer.
- `management_extraction.json`: parsed JSON.
- `management_validated_extraction.json`: schema-valid JSON, **not** evidence admission.
- `management_validation.json`, `management_diagnostics.json`: failed evidence check.
- `m3_rebuild_manifest.json`: independent corporate success and management failure.

The parent test directory preserves `case.db`, `canonical_snapshot.json`,
`m3_packet.json`, `auth_preflight.json`, `source_preflight.json`, `summary.json`
and `request_ledger.json`. The database contains the actual shared model/agent
records and audit trail.

Offline replay:
`output/m3_milestone3/hca_saved_replay/output/m3_rebuild/HCA_M3_CREDIT_2024/run_81b80387a1d8/`

Its `Senior Management Assessment.json` is the partial v2 assessment and includes
all rejection reasons. Raw response bytes match the original. The replay made
zero paid requests.

## Paid test result

| Item | Actual result |
|---|---|
| Paid generation count | 1; no retries or unrelated paid calls |
| Requested / returned model version | gemini-3.5-flash-lite / gemini-3.5-flash-lite |
| Authentication / endpoint | Existing ADC, Vertex global, original project preserved; empty VERTEX_API_KEY |
| Input / output / total tokens | 9,056 / 9,509 / 18,565 |
| Thinking tokens / HTTP status | Not supplied by SDK; left unavailable |
| Cost estimate | USD 0.0264893 using user-supplied 0.30 input and 2.50 output per million tokens; not an invoice |
| Raw executive coverage | All seven expected names; 7 supporting facts and 4 judgments |
| Initial validation | JSON/schema passed; evidence failed on roster-date scoping |
| Offline replay after objective bug fix | Partial; 0/7 profiles admitted, 7 facts and 4 judgments withheld |
| Corporate sibling | Complete in both runs |

The response itself verifies model accessibility through the configured endpoint;
no model substitution or extra generation probe occurred. The original roster
gate mistakenly counted unrelated debt-disclosure “As of December 31” dates in a
retained canonical passage as roster dates. The fix scopes the existing date
consistency requirement to executive-roster declarations. Regression tests still
reject two conflicting actual roster declarations.

The replay then exposed substantive quotation/role problems. Hazen, Foster and
McAlevey had at least one quotation that did not match its assigned passage.
Berres, Cuffe and Marks failed prior-role wording checks, including native PDF
split-word artifacts. Mosier failed the existing executive-linkage check for a
prior-role quotation. Existing atomic profile rejection withheld each affected
profile and every dependent fact/judgment. The exposed overall assessment is
therefore unavailable; the model's positive overall response remains retained as
unvalidated source output.

Representative **raw, unadmitted** model judgments:

- Hazen: credit-positive continuity and institutional knowledge from internal
  operating roles, qualified by the absence of guaranteed future performance.
- Marks: neutral internal CFO promotion, with company experience as an offset
  and execution in the top finance role still to be demonstrated.
- McAlevey: neutral expansion of legal/administrative responsibilities requiring
  transition monitoring.
- Team: credit-positive internal operating experience, qualified by labor and
  regulatory pressures and missing performance/incentive information.

These demonstrate qualitative reasoning in the response, not validated credit
conclusions. No failing judgment was promoted merely because the model produced
a plausible explanation.

## Files and common-code effects

- `app/services/bo/m3_rebuild/{provider,contracts,management,sources,workflow}.py`:
  prompt/contract, shared execution adapter, objective checks, adjacent context,
  diagnostics and compact output.
- `app/services/bo/m3_rebuild/credit_test.py`: normal-BO preflight, persistent
  single-request budget, offline replay and cost accounting.
- `app/services/bo/m3_rebuild/authorized_tests.py`: explicit retirement error for
  the historical private batch.
- `app/services/bo/runner.py`: canonical packet ordering/selection and shared
  management backend execution; corporate/management independence retained.
- `app/services/agents/executor.py`: public persistence method for an independently
  scheduled runtime result; existing DAG scheduling remains unchanged.
- `app/services/llm/providers_vertex.py`: optional schema transform with the
  existing default preserved; SDK attempts=1 and real available response telemetry.
- `app/core/config.py`, `.env.example`: user-supplied cost configuration. Local
  worktree and original-checkout `.env` settings updated as requested, preserving
  project/location. After the user supplied their contact identity, both local
  SEC_USER_AGENT settings were updated to that exact name/email. Personal contact
  information remains in ignored local configuration, not the tracked example.
  Original tracked example left unchanged.
- `app/services/registry/json_render.py`: v2 known-schema rendering.
- `tests/m3_rebuild/`, `tests/registry_fixture.py`,
  `tests/unit/test_artifact_registry.py`: current response fixtures, shared-runtime
  integration, split passages, invalid references, neutral judgments, budget and
  registry regressions. Existing fact/quote/date checks remain exercised.
- Runbook, contracts, example provenance and README updated.

## Verification

Final full offline suite: **1,848 passed, 129 skipped**, with one existing
Starlette/httpx deprecation warning. Skips cover unavailable local workbooks,
filings/OCR/rendering dependencies and the deliberately disabled live-provider
test. `git diff --check` passes. Tests cover normal preparation and BO execution,
canonical-only model input, exact rendered-prompt persistence, shared model/agent
audit rows, split biography citations, neutral judgment preservation, invalid
facts/references, conflicting roster dates, budget exhaustion after success or
failure, independent corporate outcomes, registry rendering and existing modules.

The raw response SHA-256 is
`31979bba87dabe512dbe308fe3f5034ef66286b1beffe9dea747cea5df768c68`;
the offline replay was verified byte-identical. Both local configurations use
the exact requested model, empty Vertex key, 35,000-token output limit and all ten
user-supplied model prices. Project/location and SEC contact values match across
  the local configurations. The user-supplied real SEC contact is now configured
  in both local files; no configuration item remains unresolved.

## Remaining gaps

Validator 7 independent review of model credit mechanisms and calibration remains
unimplemented. Lexical fact checks remain conservative and can reject supported
facts when native extraction splits words, or a quotation uses pronouns/role
variants. Improving those checks requires a separately reviewed evidence-binding
design; this milestone did not normalize away the HCA failures.

M3 records are auditable through shared runtime tables, but M3 observations do
not yet enter the common challenge/rerun scheduling graph with observation-level
revision lineage. Existing adapters carry accepted observation text/citations;
they do not independently challenge qualitative reasoning or fully consume all
new overall-assessment/uncertainty fields. The corporate structure is still an
explicit simulation, not independently verified legal evidence.

The HCA test used an explicitly declared source-availability timestamp at the
2025-03-01 cutoff, not independently verified SEC publication metadata. Other
modules ran offline empty responses, so this run asserts no complete financial
or business conclusion. The regression suite separately tests their behavior.

No commit, push, merge or further paid request was performed.
