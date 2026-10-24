# Consolidated BO + FO Output Contract for SO

**Status:** stable, versioned. **Not** a new DB write path — this documents the
existing governed artifacts plus the artifact-registry index that makes them
discoverable as a single, case-coherent handoff for Structural Overview (SO).

## 1. Case & evidence identity (shared anchor)

BO, FO and M3 all attach to the same case through the canonical identity already
used across the platform:

* **Case id** — `Case.case_id`.
* **Evidence snapshot** — the `(case_id, snapshot_version)` pair of the
  `CanonicalEvidenceSnapshot` (`app/schemas/snapshots.py`). The manifest records
  this as `source_evidence_snapshot = "<case>@v<N>"` on each entry.

SO already consumes this anchor: `app/schemas/structural_overview.py` requires
`case_id`, `evidence_snapshot_version` and `source_bo_run_id` /
`source_handoff_id`. The integration does **not** introduce a competing case
identity or an independently authoritative version of a shared financial fact —
FO is reconciled onto the same case/snapshot (see §4).

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
  failure is a queryable signal in the production path, never silent prose
  (tested in `tests/integration/test_module4_specialist.py` and surfaced in the
  manifest as `status=failed` + `escalations`).

Module 4's governed output (strategy + debt structure) is the Module 4 contract
SO consumes; both capabilities appear under the single `module_conclusions["M4"]`
umbrella and as distinct, inspectable nodes in the Pipeline Explorer.

## 4. Financial Overview handoff (reconciled onto the same case)

* **Artifact:** `<output>/fo/<case>/<run>/financial_overview.json` (+ transcripts),
  persisted as `FinancialOverviewRunRow`. Summary via `FoRunResult.summary()`.
* **Identity reconciliation:** FO is keyed by the **same `case_id`** and derives
  its filing identity/provenance (`meta.entity_name`, `period_end`,
  `filing.folder`) so it associates with the same case and source version as BO.
  FO does **not** mint an independently authoritative copy of a shared financial
  fact; where BO/M3 already hold a governed figure, FO's deterministic measures
  are additive analysis over the same evidence.
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
2. **Integrity.** Each entry's `content_hash` is the canonical SHA-256 of the
   exact recorded JSON; SO can verify it has the bytes BO/FO produced.
3. **Case coherence.** BO, FO and M3 share `case_id` and
   `source_evidence_snapshot`; FO carries no conflicting case identity.
4. **Honest status.** SO can filter on `is_successful()` (validated / qualified)
   and must respect `failed` / `incomplete` / `absent` / `skipped` — no
   fabricated success is ever registered.
5. **Escalations & review.** Unresolved mandatory escalations and
   `human_review_status` travel with each entry and remain visible.
6. **Stability.** The manifest and graph are versioned (`artifact-manifest-1.0`,
   `pipeline-graph-1.0`); existing JSON contracts (`bo-memo-1.0`,
   `bo-audit-1.0`, `bo-m3-analysis-1.0`, FO payload) are unchanged.

## 6. Open items SO should account for

* FO rating methodology is provisional.
* Cross-module synthesis V2 and the BO+FO→SO handoff node are shown as
  illustration scaffolding in the Explorer until an end-to-end cross-module run
  is recorded; SO should consume the **BO handoff + FO overview** artifacts
  directly, keyed by case + evidence snapshot, rather than assuming a single
  pre-merged cross-module artifact exists.
