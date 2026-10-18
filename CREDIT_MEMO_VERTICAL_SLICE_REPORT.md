# Mixed real/simulated credit memo vertical slice

## Result

The offline pipeline produces a validated partial assessment, deterministic HTML, and an actual six-page PDF for HCA Healthcare, Inc. It combines the existing live BO handoff with invented FO inputs and a deterministic, replaceable Structural Overview scaffold. Every PDF page carries an incompleteness/simulation notice. There is no final obligor rating, facility rating, credit decision, or human approval.

Branch: `feat/credit-memo-vertical-slice`. The verified remote starting tip was `65df1a04c05457856407b65b7cd5166da5fdbb77`, one commit after the handoff's `e54f9a9e190bf53cbfb133bb61ff1e31723a9ba8`. That extra commit is the existing temporary agent I/O PDF exporter. Work is isolated in `.worktrees/credit-memo-vertical-slice`; earlier worktrees were preserved.

## Architecture and contracts

Pipeline:

```text
local bo-handoff-1.2 -> existing adapt_business_overview (v1.0 validated BO mapping)
fo-adapter-1.0       -> strict FO boundary + existing MetricEngine
                    -> structural-overview-assessment-1.1
                    -> canonical JSON -> Jinja2 HTML -> existing WeasyPrint backend -> PDF
```

Inspected and reused the existing Structural Overview adapter/reporting, `CanonicalEvidenceSnapshot` and `FinalCaseSnapshot`, reporting templates and PDF backend helpers, Financial Orchestrator contract, deterministic metric definitions/engine, Credit Orchestrator guards, and all production LLM backends. The Financial Orchestrator expects accepted parameter results, a financial risk score, stress/covenant results and limitations; it is not a portable FO output handoff. Manufacturing those accepted objects would misstate this fixture's status. A small independent FO boundary therefore supplies the intermediate assessment without entering the real orchestrator.

- `app/schemas/credit_memo.py`: strict Pydantic `FinancialOverview` (`fo-adapter-1.0`) and `PartialCreditAssessment` (`structural-overview-assessment-1.1`). The latter extends the existing assessment; v1.0 remains unchanged. Versioned JSON Schema is available through each class's `model_json_schema()`.
- `examples/credit_memo/synthetic_fo_v1.json`: the only new tracked data fixture; two consecutive annual periods, units, assumptions, explicit missing information, input and finding provenance.
- `app/services/structural_overview/credit_memo.py`: BO/FO identity and period checks, deterministic integration, metric-engine reuse, structural gaps, cross-domain links, source/content hashes and rendering integrity checks.
- `app/services/reporting/templates/credit_memo_partial.html.j2`: six sections rendered solely from the frozen assessment. The same HTML becomes the PDF. Jinja autoescaping is enabled, including for this `.j2` filename.
- `scripts/credit_memo_vertical_slice.py`: offline CLI; requires the local BO path, accepts a replaceable FO packet path and output directory, and fails explicitly if a PDF backend is unavailable.

The scaffold carries borrower identity, unavailable parent/subsidiary and ownership relationships, segments/legal entities, debt composition/obligations, maturities, guarantees/collateral, facility terms, and illustrative liquidity. It does not infer legal structures. Missing dependencies are tracked separately for full SO, facility underwriting, Credit Orchestrator, final scoring and human approval. Completion states remain partial, unavailable or blocked.

## Real versus simulated content

The genuine local source is:

```text
output/bo_hca_live/bo/HCA_2025_BO_LIVE/ar_36d4a0589d224737/handoff_v1_2.json
```

It was found in the earlier `bo-validation-release` worktree and copied unchanged into the new worktree's ignored output directory for local regression tests. No real handoff or generated HCA assessment has been added to Git. Its original content hash is verified by the existing adapter.

Preserved without rewriting:

- All 28 normalized BO findings; eight selected and 20 non-selected remain distinguishable.
- All 32 BO evidence references, source hypothesis/claim/section/run links, and qualifications.
- The M1A6 compound margin-compression risk, including its qualified narrative state and evidence IDs.
- The unresolved challenge concerning the word “immune,” pending human review, and provisional BO band 3.

The PDF shows selected source findings and curated conclusions. All normalized findings remain in JSON. Evidence aliases on narrative pages resolve to complete BO evidence IDs on page six; original evidence documents and source locations remain in the referenced canonical evidence snapshot, not copied into this intermediate packet.

Every FO fact, financial finding and derived metric has `synthetic_fixture` provenance. Invented FY2024/FY2025 figures use USD million. Examples: revenue 60,000/63,000; operating income 9,000/8,820; debt 40,000/42,000; cash 2,000/2,200; CFO 8,000/8,200; capex 4,500/5,000. Nothing claims these values were extracted from HCA filings.

The existing metric engine calculates growth, operating margins, net debt/EBITDA, EBITDA/interest coverage, free cash flow and liquidity. Illustrative FY2025 results include 5% revenue growth, 14% operating margin, 3.325x net leverage, 5.441x interest coverage, 3,200 free cash flow and 7,000 liquidity. Ratios retain formulas, input values/references, definition version and engine version. Bad denominators retain an explicit review state rather than returning zero. Assumptions identify EBITDA adjustments, unrestricted cash/revolver availability, positive capex outflows and lease/acquisition exclusions.

The cross-domain observation links actual `M1A6-H001` to both simulated operating-margin observations. It is explicitly simulation-dependent and unverified, with no asserted causal attribution or increase in BO certainty. This relationship is emitted only when the relevant source finding exists.

## Generated artifacts and reproduction

Within the implementation worktree:

- JSON: `output/thin_slice/HCA_2025/credit_memo_partial.json`
- HTML: `output/thin_slice/HCA_2025/credit_memo_partial.html`
- PDF: `output/thin_slice/HCA_2025/credit_memo_partial.pdf`
- Layout review images: `output/thin_slice/HCA_2025/page_1.png` through `page_6.png`
- Assessment SHA-256: `44d775a14cef630545d8b18dc12452d2b3e21580b9bcc5da99ef866dcc52bdb3`
- Normalized FO packet SHA-256: `6e835b284fe4ee3ec3a753d9acf0b53c5193f22b763e591f0523554681de1a3e`

The assessment hash covers every field except its own `content_hash`. The renderer revalidates contracts, hashes and financial calculations before using the frozen JSON. Identical assessment input produces identical HTML and substantively identical PDF text; binary PDFs may contain renderer metadata.

Windows reproduction from the worktree, using the existing workspace environment:

```powershell
$env:WEASYPRINT_DLL_DIRECTORIES='C:\msys64\ucrt64\bin'
& C:\Users\pearn\NTU\BU5203\.venv\Scripts\python.exe -m scripts.credit_memo_vertical_slice `
  --bo-handoff output\bo_hca_live\bo\HCA_2025_BO_LIVE\ar_36d4a0589d224737\handoff_v1_2.json
```

WeasyPrint's Python package was installed already but Pango was missing. Installed `mingw-w64-ucrt-x86_64-pango` and its dependencies using the existing MSYS2 installation, then set `WEASYPRINT_DLL_DIRECTORIES` for each relevant process. No repository dependency or lockfile change was necessary. This follows the [WeasyPrint Windows setup instructions](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows). Other machines need the existing `[pdf]` extra and native libraries. HTML alone is never reported as PDF completion.

## Sampling configuration

`app/services/llm/sampling.py` centralizes a pure provider sampling policy. Unset overrides preserve existing defaults, with the requested Gemini 3.x exception: 1.0. This follows [Google's Gemini 3 guidance](https://ai.google.dev/gemini-api/docs/gemini-3) and [documented 0–2 range](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/prompts/adjust-parameter-values).

- `LLM_TEMPERATURE`: optional explicit experiment setting; a direct `LLMRequest.temperature` takes precedence.
- `LLM_TEMPERATURE_SUPPORTED`: explicit capability switch for custom/other models; false omits the parameter and rejects an override. OpenAI Responses defaults continue to omit temperature; an override requires confirmed support through this setting. The legacy compatible endpoint continues to send 0 unless configured otherwise.
- `LLM_TEMPERATURE_MAX`: a narrower verified model range where necessary, bounded by the supported 0–2 interface range. Non-finite, negative and excessive overrides fail before a provider call.
- Gemini 3.x defaults to 1.0; older Gemini retains the earlier 0 default. Vertex model tier routing and provider/model identity remain intact.
- Executor cache identities include the routed model, provider, requested/effective temperature and output-token limit; changing sampling configuration cannot reuse the previous experiment's cached output. Recording wrappers delegate this identity. Invalid sampling settings also use the shared sanitized provider-error path.
- Run metadata records requested/effective temperature and the existing backend/model identity. The model-run temperature column stores the effective value when known; omitted provider defaults are not guessed.

Fake/scripted/replay backends make no sampling calls. Temperature zero does not guarantee deterministic live model output. All provider checks use mocked requests; no HCA agent was rerun.

## Verification

Final combined focused and regression run: **246 passed** in 31.58 seconds:

```powershell
$env:DEBUG='false'
$env:WEASYPRINT_DLL_DIRECTORIES='C:\msys64\ucrt64\bin'
python -m pytest tests/integration/test_credit_memo_vertical_slice.py tests/unit/test_sampling_configuration.py tests/unit/test_vertex_provider.py tests/unit/test_real_provider.py tests/unit/test_llm_client.py tests/integration/test_structural_overview_thin_slice.py tests/unit/test_agent_runtime.py tests/unit/test_agent_cache.py tests/integration/test_dag_executor.py tests/unit/test_bo_validation.py tests/unit/test_bo_remediation.py tests/unit/test_bo_m3_services.py tests/unit/test_memo_output.py tests/integration/test_bo_pipeline.py tests/integration/test_bo_governance_contract.py tests/integration/test_bo_downstream_contract.py tests/integration/test_orchestrators.py tests/integration/test_memo_output_pipeline.py -q --tb=short --basetemp=output/pytest_credit_release
```

The genuine HCA test ran locally and rendered PDF twice; it did not skip. It checks finding/selection counts, M1A6 qualification and lineage, pending review, unresolved challenge, partial status, expected PDF sections, synthetic notices on every page, illustrative ratios and repeated-render text parity. Other checks cover invalid contracts/identities/periods/provenance, rejected fake SEC evidence links, bad denominators, required PDF failure, content tampering and protected incompleteness disclosures. Future FO contract mechanics are tested with explicitly invented test data, never presented as real analysis. Runtime, cache and DAG regressions verify the provider configuration change alongside the existing BO, reporting and orchestration checks.

The host exposes `DEBUG=release`; test processes override it with `DEBUG=false` so Settings can parse the inherited environment. This does not change application configuration.

After refining the invalid-sampling error handler, the affected sampling, LLM-client and DAG-executor checks were rerun: **38 passed** in 2.74 seconds.

Rendered all six final PDF pages to PNG and visually inspected each. Section overflows found during development were corrected; final pages have readable content, complete references, no clipped tables, and visible simulation/incompleteness notices. The audit reference table intentionally uses smaller type than narrative pages. Layout is tuned for this bounded prototype, not arbitrary future packet lengths.

## Exact FO replacement point and remaining work

Replace the file passed as `--fo-fixture`, or supply the same `FinancialOverview` packet to `build_partial_assessment(bo_payload, fo_payload)`. A future upstream translator maps real accepted FO outputs into `fo-adapter-1.0`: matching borrower/case, two consecutive periods, positive expense conventions, explicit units/assumptions, financial findings, missing information and evidence references. Real packets use `validated_fo` provenance for every fact/finding and must provide evidence IDs. The same builder, metric engine, assessment schema, template and PDF renderer already accept that packet shape and vary disclosures accordingly.

This boundary validates structure and consistency; it does **not** admit new evidence or certify the future FO team's governance. A producer's `validated_fo` label is a declaration, not an admission decision. Source/evidence verification and accepted-run mapping must occur upstream. Even with a real packet, this assessment stays partial, BO qualifications stay intact, and final synthesis remains blocked.

Integration is ready for a real FO adapter at this boundary, but genuine credit synthesis still requires substantial analytical work: admitted/reconciled financial statements and debt/lease data; accepted FO parameters, conclusion and financial score; legal entities, obligors, guarantees/security, maturities and facility/covenant terms; accepted structural conclusions and required obligor/facility scores in the Credit Orchestrator's common run; cross-topic challenge resolution; and recorded human review. Existing Credit Orchestrator acceptance guards are unchanged and this prototype does not invoke it. Completing those upstream analyses and governance steps, rather than another renderer or reporting framework, is the main remaining work.
