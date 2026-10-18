# Structural Overview Thin-Slice Integration Report

> Historical delivery record for schema 1.0. The governed M3 integration and
> schema 1.1 behavior are documented in `SO_M3_INTEGRATION_REPORT.md`.

## Delivery baseline

- Source branch: `test/bo-live-end-to-end`
- Source commit: `aca6a1bb5a05c9cb63867ed15d59a616c9a8110b`
- Working branch: `feat/structural-overview-thin-slice`
- Source BO case: `HCA_2025_BO_LIVE`
- Source BO run: `ar_36d4a0589d224737`
- Source handoff: `bo_07644807b5944874`, schema `bo-handoff-1.2`
- Transformation: `bo-to-so-1.0`
- Output schema: `structural-overview-assessment-1.0`

The branch was created directly from the validated BO commit. No newer remote branch contained that commit when the work began.

## Existing architecture reviewed and reused

The repository already had a usable downstream boundary in `AcceptedBusinessOverview`. That strict Pydantic model is documented as the interface for Structuring and the Credit Orchestrator, and it carries module conclusions, evidence IDs, compound risks, governance state, canonical references, and a tamper-evident content hash. The new integration consumes that boundary rather than BO agent internals.

The existing Credit Orchestrator guard in `app/services/orchestration/credit.py` is implemented for a later stage. It requires accepted Business, Financial, and Structuring conclusions plus applicable scores. Those inputs do not yet exist for this case, so the thin slice does not call it and records final synthesis as blocked.

The existing structuring services concern facility candidates, schedules, stress tests, and structuring orchestration. They are implemented components, but they require facility information outside this milestone. They were not repurposed as a borrower-level Structural Overview.

`FinalCaseSnapshot` is the canonical post-analysis snapshot and reporting already follows a JSON-first, deterministic-rendering pattern. The thin slice remains an intermediate assessment because the prerequisites for a final snapshot are incomplete. It reuses `canonical_json`, `content_hash`, strict Pydantic validation, Jinja autoescaping, and the existing reporting template package.

## Implementation

`app/schemas/structural_overview.py` defines the versioned intermediate contract. It separates:

- borrower and source context;
- normalized BO findings grouped into the four downstream business sections;
- curated strengths, vulnerabilities, mitigants, and compound risks;
- evidence, hypothesis, claim, module, section, run, and handoff lineage;
- conclusion qualification, challenges, questions, missing information, human review, and provisional scoring;
- BO-to-FO handoffs and explicit downstream section states;
- audit and content-hash metadata.

`app/services/structural_overview/adapter.py` implements the deterministic transformation. It validates the full BO Pydantic contract, exact schema version, source content hash, IDs, case identity, run references, accepted finding links, curated claim links, evidence links, and compound-risk components. It copies source conclusion wording and classifications without model synthesis. Unsupported, malformed, tampered, cross-case, or broken-link inputs fail closed.

The output content hash covers every assessment field except `content_hash` itself. Serialization uses canonical UTF-8 JSON with sorted keys and no insignificant whitespace. The deterministic assessment ID is derived from the source handoff, run, output schema, and transformation version.

`app/services/structural_overview/reporting.py` writes canonical JSON first and reloads that file before rendering HTML. The renderer validates the assessment hash and reads only normalized assessment data. The module command is:

```powershell
python -m app.services.structural_overview <handoff.json> <output-directory> --expected-case-id <case-id>
```

## HCA input verification and mapping

The local source artifact existed at:

`output/bo_hca_live/bo/HCA_2025_BO_LIVE/ar_36d4a0589d224737/handoff_v1_2.json`

It validated against `AcceptedBusinessOverview`; its recorded handoff content hash recomputed successfully. The borrower is HCA Healthcare, Inc., the as-of date is 2025-12-31, all M1-M4 module statuses are complete, and its canonical run reference matches `ar_36d4a0589d224737`. The conclusion remains `accepted_with_qualification`, its BusinessRiskScore band remains 3 with `provisional` status, and human review remains `pending`.

The generated assessment contains 28 source module findings and 32 resolved evidence references. Each normalized finding retains the exact source claim, classification, evidence IDs, source hypothesis ID, BO module, source section, handoff ID, and run ID. The eight findings selected by BO are marked `accepted_for_handoff`; the remaining module findings stay distinguishable rather than being silently promoted.

The compound margin-compression risk `R-001` retains component hypothesis `M1A6-H001` and both underlying evidence IDs. The unresolved `CH-001` challenge remains present in JSON and HTML. BO supplied three explicit financial questions but zero structured `fo_handoffs`; the assessment preserves both facts and keeps Financial Overview unavailable.

## Generated artifacts

- JSON: `output/thin_slice/HCA_2025/assessment.json`
- HTML: `output/thin_slice/HCA_2025/assessment.html`
- Assessment ID: `soa_39a9c555e6698aa3`
- Canonical content hash: `ad0d0e0ea097e4cccd8a1bd34c13ae0f7a8aa0f935561162a0273f800037f554`
- Overall status: `partial`

These real-data outputs are Git-ignored. They were generated locally from the live BO handoff and are intentionally not committed with source code.

## Validation

Targeted adapter tests cover the genuine local HCA handoff, qualification preservation, unavailable downstream sections, absence of manufactured final outputs, compound-risk lineage, challenge visibility in JSON and HTML, unsupported versions, malformed IDs, mismatched cases and runs, broken hypothesis and evidence links, source hash validation, and deterministic assessment/HTML output.

Results at implementation time:

- Thin-slice suite: `12 passed`
- BO downstream, orchestrator, snapshot, and thin-slice regression set: `33 passed`
- Complete repository suite: `1272 passed, 119 skipped`, with one existing Starlette deprecation warning
- No live Vertex calls were made by tests.

The genuine-artifact test skips in environments where the Git-ignored local handoff is absent. Synthetic data is limited to deterministic contract and negative-path coverage.

## Integration readiness and limitations

The intermediate output is ready to accept a future Financial Overview contract and later feed final credit synthesis after the required accepted topic conclusions exist. Its versioned boundary, explicit section states, stable lineage, and deterministic hash provide a suitable integration point.

The current result cannot support a final credit rating or facility decision. Financial Overview has not run; legal entity and obligor structure are unavailable; facility terms, collateral, guarantees, and covenant protections are unavailable; and the Credit Orchestrator prerequisites are incomplete. The HCA BO conclusion also has a pending human review and an unresolved medium-severity language challenge.

Upstream BO limitations remain visible: missing price-versus-mix revenue detail, comparative market-share evidence, independent local-market rankings, quantified labor-cost/reimbursement sensitivity, and supplier-concentration metrics. The empty structured BO-to-FO handoff list must not be interpreted as completed financial analysis; the three open financial questions remain explicit downstream dependencies.

The next integration step is to define or connect a versioned FO handoff with equivalent identity, provenance, qualification, and hash guarantees. After borrower structure and any facility inputs exist, the existing cross-topic guard can enforce same-run accepted inputs before final synthesis and `FinalCaseSnapshot` assembly.
