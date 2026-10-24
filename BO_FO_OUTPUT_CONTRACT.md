# Consolidated BO + FO Output Contract for SO

**Status:** stable, versioned. **Not** a new DB write path — this documents the
existing governed artifacts plus the artifact-registry index that makes them
discoverable as a single, case-coherent handoff for Structural Overview (SO).

## 1. Case & evidence identity (five-level provenance)

The artifact registry makes an explicit, verifiable distinction between five
levels of "same source" (see `ProvenanceRecord` in
`app/schemas/artifact_manifest.py` and `app/services/registry/provenance.py`):

1. **Same borrower/case** — `Case.case_id`.
2. **Same filing & fiscal period** — `filing_identifier` + `reporting_period`.
3. **Same underlying source documents** — `source_signature` / `source_document_ids`.
4. **Same canonical evidence snapshot** — `canonical_snapshot_version` +
   `canonical_snapshot_hash`, classified by `snapshot_equivalence`.
5. **Same numerical metric definition** — carried per-fact by the cross-module
   consistency check, not assumed.

**Important correction (hardening):** a matching `case_id` does **not** establish
that BO and FO used the same canonical evidence snapshot. BO records a real
canonical snapshot identity, so BO artifacts are `snapshot_equivalence=verified`.
FO runs through an **independent filing loader** (`app/services/fo/loader.py`,
keyed by filing folder + `folder_signature`) with **no recorded link** to the
`CanonicalEvidenceSnapshot`; FO artifacts are therefore
`snapshot_equivalence=unverified`, preserving their filing identity / source
signature. SO must treat FO↔BO canonical-snapshot equivalence as **unverified**
unless a recorded snapshot link exists — it is never inferred from the case id
alone. `source_evidence_snapshot = "<case>@v<N>"` is populated only when a
snapshot version is actually recorded.

SO already consumes the case anchor: `app/schemas/structural_overview.py`
requires `case_id`, `evidence_snapshot_version` and `source_bo_run_id` /
`source_handoff_id`.

## 2. Business Overview handoff (authoritative BO output)

* **Artifact:** `<output>/bo/<case>/<run>/handoff.json`, persisted as
  `AcceptedBusinessOverviewRow` (`handoff_id`, `content_hash`,
  `human_review_status`, `artifacts` path map).
* **Schema:** `AcceptedBusinessOverview` (`app/schemas/bo.py`).
* **Consumed by SO via:** `source_handoff_id`, `source_bo_run_id`,
  `evidence_snapshot_version` + `NormalizedFinding.provenance`
  (`FindingProvenance.source_claim_ids` / `source_hypothesis_id`).
* **Module outputs inside the memo** (`module_conclusions`): M1 (six-agent, incl.
  Agent 6 synthesis), M2, **M3** (authoritative corporate/entity relationships,
  Validator 7 numeric binding) and **M4** (see §3). Orchestrator synthesis V1,
  challenger, bounded synthesis V2 and final deterministic governance are all
  preserved and unchanged.

Registry ids: `bo.memo.<run>`, `bo.audit.<run>`, `bo.handoff.<run>`.

## 3. Module 4 — one governed output, two internal specialists

Module 4 exposes **one governed Module 4 output** backed by two distinct
internal specialist capabilities. Neither was silently replaced:

| Capability | Agent / service | Validator | Checks |
|------------|-----------------|-----------|--------|
| Strategy & Capital Allocation / Financial Stewardship | `m4_strategy_capital` (`app/services/bo/roster_v3.py`, executable in BO v3) | declared `validator_id="validator_8"` | acquisition strategy, integration risk, capex strategy |
| Debt Structure, Refinancing History & Financial Stewardship | `Module4Specialist` (`app/services/modules/module4/`) | deterministic `Validator8` (`validator8.py`) | reconciliation, subordination_ratio, maturity_order, legal_entity, double_counting |

* **Validator-id reconciliation.** The two "Validator 8"s are disambiguated and
  kept separate under the same Module 4 governance umbrella — in the pipeline
  graph as `validator.v8_strategy` and `validator.v8_debt`. The roster-v3
  agent's declared validator id is unchanged (`validator_8`); the debt-structure
  validator is identified by its five stable check ids.
* **Verified entity relationships.** The debt-structure specialist may *consume*
  verified M3 entity relationships when available (graph edge
  `optional_verified_entity_relationships`) but never treats unsupported
  relationships as fact — its `legal_entity` check enforces borrower-vs-guarantor
  attribution.
* **Mandatory escalation.** A debt-structure Validator 8 failure raises a
  **mandatory** escalation (`R-MODULE4-VALIDATOR8-01`, severity `MANDATORY`) via
  the existing `EscalationEngine`, which drives case status to `escalated`. The
  failure is a queryable signal, never silent prose, and it marks
  `module_conclusions["M4"]` **incomplete** so `_finalize` forces
  `human_review_required` (tested in `tests/integration/test_bo_m4_dual_specialist.py`).

**Orchestration (hardening correction).** The debt specialist now executes
**inside the normal BO run** (not only standalone): `_BoRun._module4_debt_phase`
(`app/services/bo/runner.py`) runs `Module4Specialist` through the run's own
`LLMClient` + `EscalationEngine` on **admitted snapshot evidence only**
(`app/services/bo/m4_debt.py` selects debt-relevant passages/facts; no M1/2/3
outputs), and folds its result into `module_conclusions["M4"]["m4_debt_assessment"]`
alongside the strategy specialist's findings — one governed M4 output consumed by
cross-module synthesis, the challenger and final governance.

This is gated by the opt-in `enable_m4_debt_specialist` flag on
`BusinessOverviewRunner` (**default off** preserves the previously-recorded v2/v3
behaviour and scripts; turn it on for the combined-output path). When no admitted
debt evidence exists, the debt assessment is reported `unavailable` (no model
call, no fabricated analysis). A non-validated debt result (`unavailable` /
`rejected` / `failed`) keeps M4 incomplete.

## 4. Financial Overview handoff (reconciled onto the same case)

* **Artifact:** `<output>/fo/<case>/<run>/financial_overview.json` (+ transcripts),
  persisted as `FinancialOverviewRunRow`. Summary via `FoRunResult.summary()`.
* **Identity reconciliation:** FO is keyed by the **same `case_id`** and records
  its filing identity/provenance (`meta.entity_name`, `period_end`,
  `filing.folder`, `filing.signature`). This proves *same case* and lets SO check
  *same filing/period*, but it does **not** prove *same canonical snapshot* —
  FO's loader is independent of `CanonicalEvidenceSnapshot`, so the registry
  marks FO `snapshot_equivalence=unverified` (see §1). FO does **not** mint an
  independently authoritative copy of a shared fact.
* **Cross-module consistency check.** `cross_module_consistency`
  (`app/services/registry/provenance.py`) compares overlapping facts (total
  indebtedness, cash, reporting period/entity, debt maturities) **only** when
  units and reporting bases are compatible, flags inconsistencies for review, and
  **never overwrites** either side or Alan's scoring.
* **Deterministic measures preserved:** the seven measures, scoring engine,
  pre-checks and trigger agents are unchanged.
* **Rating methodology is PROVISIONAL.** `composite_score` / `credit_rating` /
  `risk_level` are labelled provisional and must be treated as such by SO; they
  are withheld entirely when the run is awaiting review.

Registry ids: `fo.overview.<run>`, `fo.precheck.<run>`, `fo.triggers.<run>`.

## 5. What SO can rely on (handoff guarantees)

1. **Discovery.** Every registered BO/FO/M3 output for a case is discoverable
   through `build_manifest(output_root, case_id, ...)` → `ArtifactManifest`
   (`artifact-manifest-1.0`), with producer, schema, content hash, evidence
   snapshot, dependencies and execution status per entry.
2. **Integrity (two guarantees).** Each entry's `content_hash` is the **canonical**
   SHA-256 of the parsed payload. For whole-file artifacts the registry also
   exposes the **original file SHA-256** of the exact recorded bytes
   (`original.json` / `X-Original-SHA256`); the two hashes differ when the
   recorded file is pretty-printed rather than canonical. Normalized export
   (`normalized.json` / `X-Canonical-SHA256`) is semantically equivalent but
   re-serialised. Per-agent slice artifacts have no independent original file, so
   only normalized export is available for them.
3. **Case coherence, not snapshot equivalence.** BO, FO and M3 share `case_id`.
   BO/M3 carry a verified canonical-snapshot identity; FO is
   `snapshot_equivalence=unverified`. SO must not assume BO and FO share a
   canonical snapshot from the case id alone.
4. **Honest status.** SO can filter on `is_successful()` (validated / qualified)
   and must respect `failed` / `incomplete` / `absent` / `skipped`. File
   existence alone never yields `validated` — only an authoritative recorded
   status does.
5. **Per-agent discovery.** Each BO specialist, the synthesis and the challenger
   are individually indexed (`bo.agent.<id>.<run>`, `bo.synthesis.<run>`,
   `bo.challenger.<run>`) from the recorded `audit_report.json`; clicking one in
   the Explorer returns that producer's own output. (The orchestrated M4 debt
   specialist's output lives in `module_conclusions["M4"]`, not the audit agents
   list, so its dedicated graph node is populated only when the debt phase runs.)
6. **Escalations & review.** Unresolved mandatory escalations and
   `human_review_status` travel with each entry and remain visible.
7. **Stability.** The manifest and graph are versioned (`artifact-manifest-1.0`,
   `pipeline-graph-1.0`); existing JSON contracts (`bo-memo-1.0`,
   `bo-audit-1.0`, `bo-m3-analysis-1.0`, FO payload) are unchanged.

## 6. Open items SO should account for

* FO rating methodology is provisional.
* **FO↔BO canonical-snapshot equivalence is unverified** by construction (FO's
  independent loader). Where an overlapping fact matters, consult the
  cross-module consistency report and treat `inconsistent` findings as unresolved.
* Cross-module synthesis V2 and the BO+FO→SO handoff node are shown as
  illustration scaffolding in the Explorer until an end-to-end cross-module run
  is recorded; SO should consume the **BO handoff + FO overview** artifacts
  directly, keyed by case + evidence snapshot, rather than assuming a single
  pre-merged cross-module artifact exists.
