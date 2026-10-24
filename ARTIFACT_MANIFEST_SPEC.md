# Unified Artifact Manifest — Specification

**Schema id:** `artifact-manifest-1.0`
**Implementation:** `app/schemas/artifact_manifest.py`, `app/services/registry/`

## Purpose

A lightweight, versioned index over the governed JSON outputs produced by
Business Overview (BO), Financial Overview (FO) and, in future, Structural
Overview (SO). The manifest **indexes existing artifacts** — it never rewrites
them or changes their established contracts. It lets the Pipeline Explorer and
downstream consumers (notably SO) discover every output of a case/run, see its
producer, schema, integrity hash, evidence lineage, dependencies and status, and
tell apart the real execution states.

## Why a manifest (not a rewrite)

Each producer already writes governed, canonical-JSON artifacts with their own
versioned contracts:

| Producer | On-disk location | Key files |
|----------|------------------|-----------|
| BO | `<output>/bo/<case>/<run>/` | `business_overview.json` (`bo-memo-1.0`), `audit_report.json` (`bo-audit-1.0`), `handoff.json` |
| M3 (standalone) | M3 demo dir | `m3_corporate_structure.json`, `m3_analysis.json` (`bo-m3-analysis-1.0`), `m3_structure_provenance.json`, `validation_report.json`, `evaluation_scorecard.json`, `run_manifest.json` |
| FO | `<output>/fo/<case>/<run>/` | `financial_overview.json`, `precheck_transcript.json`, `trigger_transcript.json` |

The manifest reads these in place, re-computes each one's canonical
`content_hash` (`app/core/hashing.content_hash`), and maps the governance fields
the producers already wrote onto a uniform index. Source bytes are never
modified (enforced by `test_manifest_does_not_rewrite_source_files`).

## Entry schema (`ArtifactEntry`)

Each indexed artifact (or a deliberately absent/skipped producer) carries:

| Field | Meaning |
|-------|---------|
| `artifact_id` | Stable id unique within the manifest (e.g. `m3.analysis`, `bo.handoff.<run>`) |
| `artifact_type` | memo / audit / handoff / module_output / corporate_structure / validation_report / evaluation / run_manifest / financial_overview / transcript / evidence_snapshot / agent_output / other |
| `case_id`, `run_id` | Case and run association |
| `producer_module` / `producer_agent` / `producer_validator` | Who produced it (e.g. `M3` / `m3_corporate_management` / `validator_7`) |
| `schema_name`, `schema_version` | Governed schema identity read from the payload |
| `file_location` | Repo-relative immutable path (`None` for absent/skipped) |
| `content_hash` | Canonical SHA-256 of the payload (`None` when no payload) |
| `source_evidence_snapshot` | `"<case>@v<N>"` (the `(case_id, snapshot_version)` identity) |
| `depends_on` | Parent/dependency `artifact_id`s (declared, never guessed) |
| `status` | One of the six execution states below (+ `error`) |
| `status_reason` | Human-readable reason, especially for non-validated states |
| `validation_status` | Validator summary (`validated` / `failed` / `None`) |
| `human_review_status` | not_required / pending / accepted / rejected / escalated |
| `escalations` | Escalation ids attached to this artifact |
| `model_id`, `prompt_version`, `model_invoked` | Model/prompt provenance when applicable |
| `created_at` | Creation timestamp when recorded |

## Execution-status taxonomy

The manifest distinguishes the states the handoff requires, so a reviewer always
sees the real outcome and **no fabricated or placeholder output is ever recorded
as a successful execution**:

| Status | Meaning |
|--------|---------|
| `absent` | Producer never ran; no artifact exists (still represented, not dropped) |
| `skipped` | Producer deliberately not run for this case/run |
| `failed` | Producer ran and failed (e.g. validator failure, rejection) |
| `incomplete` | Ran partially; some sections unavailable |
| `qualified` | Produced, accepted only with qualification / requires human review |
| `validated` | Produced and passed its deterministic validation |
| `error` | The indexer could not read/parse the artifact |

`is_successful()` returns true only for `validated` and `qualified` (the latter
is "successful with caveat"); `incomplete`, `failed`, `absent`, `skipped` and
`error` are never counted as successes. The mapping is conservative: anything not
explicitly complete/validated is reported as the nearest honest state, never
silently promoted to `validated` (`_status_from_completion`).

## Integrity

* `content_hash` is the canonical SHA-256 of the exact on-disk payload; the
  Pipeline Explorer's JSON download re-serialises the same canonical bytes, and
  tests assert the exported JSON equals the recorded artifact.
* The manifest itself is reproducible: `ArtifactManifest.generated_at` is the
  only non-content field, and the Explorer serves the manifest via
  `canonical_json`.

## API

```python
from app.services.registry import build_manifest, build_pipeline_graph

manifest = build_manifest(output_root, case_id, m3_dirs=[...], repo_root=".")
graph = build_pipeline_graph(manifest)   # None -> illustration-mode graph
```

* `ArtifactManifest.by_id(id)`, `.successful()`, `.counts_by_status()`.
* `app/services/registry/explorer.py` adds case discovery, access-bounded
  artifact loading and the node-inspector assembler used by the web routes.

## Graph (`pipeline-graph-1.0`)

`build_pipeline_graph` derives the left-to-right dataflow as **layout metadata
only** (it carries no financial conclusions). Nodes are laid out in ten layers
(raw input → evidence admission → canonical snapshot → modules/agents →
validators/module outputs → synthesis V1 → challenger → synthesis V2 → final
governance → governed outputs). Edges come from the agent registry's **declared**
`dependencies` and the fixed BO synthesis/challenger/governance stages — never
from guessed file naming. With no manifest the graph still renders (every node
`absent`); with one, each node is annotated with its recorded status for the UI
to colour. Illustration nodes (fixed scaffolding such as synthesis V2 and the
SO handoff) are flagged `illustration=true` and never claim a successful run.

---

## Hardening addendum (PR #3 follow-up)

### Status semantics corrected (Priority 4)

File existence alone can **never** yield a `validated` status. `_entry_for_file`
no longer seeds a default of `validated`; absent an authoritative recorded
signal (`completion_status`, `specialist_validated`/`specialist_valid`,
`requires_human_review`), an existing-but-unannotated JSON is `incomplete`
(reason `unknown_completion_status`). A produced-but-pending-review artifact is
`qualified`; a recorded validator failure is `failed`. M3 standalone demo files
(structure, provenance, run_manifest) carry no completion metadata of their own
and now **inherit** the authoritative status recorded in the run_manifest's
`validation` sub-block, rather than being judged from existence. BO handoffs are
`qualified` (pending review), never a false `validated`.

### Five-level provenance (Priority 2)

Each entry now carries a `ProvenanceRecord` (`provenance` field,
`app/services/registry/provenance.py`) distinguishing: (1) case, (2) filing +
period, (3) source documents, (4) canonical snapshot, (5) metric definition. BO
artifacts record a verified canonical-snapshot identity
(`snapshot_equivalence=verified`). FO artifacts come from an independent filing
loader and are `snapshot_equivalence=unverified`, preserving `filing_identifier`
and `source_signature` — equivalence is **never** assumed from a shared case id.
`source_evidence_snapshot` is populated only when a snapshot version is recorded.

`cross_module_consistency` compares overlapping BO/FO facts only within
compatible units and reporting periods, flags inconsistencies (`requires_review`),
and never overwrites either value or any scoring.

### Per-agent artifact discovery (Priority 3)

The indexer extends BO runs into per-producer entries read from the existing
`audit_report.json` (no new write path): `bo.agent.<agent_id>.<run>` for each
specialist, `bo.synthesis.<run>`, `bo.challenger.<run>`. Each carries recorded
status (from the agent's `status` + run `validation_status`), evidence ids,
declared dependencies and prompt/model provenance, and `audit_subpath` locating
its slice. The node inspector returns that producer's **own** output slice (agent
block + its hypotheses + parameters + evidence packet, or the synthesis/challenger
block) — not the whole memo. Graph nodes carry `prefer_artifact_type` so they
resolve to the correct per-agent record within the **same run**.

### JSON export integrity — two operations (Priority 5)

* **Original download** (`original.json`): exact recorded bytes, `X-Original-SHA256`
  of those bytes, strong `ETag`. Not available for per-agent slices (no own file).
* **Normalized export** (`normalized.json`): canonical serialization, labelled
  `X-Export-Kind: normalized-canonical`, with `X-Canonical-SHA256`. The canonical
  hash (= the entry's `content_hash`) may differ from the original file hash when
  the recorded file is pretty-printed. The inspector surfaces both
  (`original_file_sha256`, `canonical_content_hash`).

---

## Addendum — PR #3 final acceptance remediation

### Governed module / validator / governance / snapshot records (Fix 2)

For a recorded BO run, the indexer now also extracts, from the persisted memo +
audit, these per-run inspectable artifacts (no new write path; absent stages are
`absent`, never fabricated):

| Artifact id | Source slice | Producer |
|-------------|-------------|----------|
| `bo.m4_debt.<run>` | `module_conclusions.M4.m4_debt_assessment` | `module4_debt_structure_specialist` |
| `bo.validator8_debt.<run>` | `…m4_debt_assessment.validator_report` | `validator_8_debt_structure` |
| `bo.validator8_strategy.<run>` | `agents[agent_id=m4_strategy_capital]` | `validator_8` |
| `bo.validator7.<run>` | `module_conclusions.M3.m3_assessment` | `validator_7` |
| `bo.governance.<run>` | `final_conclusion` (+ memo `status`) | `final_governance` |
| `bo.snapshot.<run>` | `evidence_snapshot` | `canonical_evidence_snapshot` |

Graph nodes (`validator.v7/v8_strategy/v8_debt`, `governance`,
`canonical_snapshot`, `agent.m4_debt_structure`) resolve to these per-run
records. Clicking a node returns that producer's own slice (the explorer resolves
both `agents[agent_id=…]` and dotted `audit_subpath`s).

### FO status + provisional rating (Fixes 3 & 4)

FO overview status is derived from the FO payload (`run_state` /
`escalations` / `composite`) rather than BO completion fields: a completed+scored
run is `qualified` (produced, pending approval), **not** `incomplete`. The FO
entry exposes `composite_score` / `credit_rating` / `risk_level`, the seven
`fo_measures`, `fo_triggers_fired`, `fo_prechecks`, and `human_review_status`.
`rating_provisional=true` whenever the composite/rating awaits human approval
(the score is retained); per-stage FO nodes `fo.precheck/measures/triggers/
scoring.<run>` are indexed. No weights or scoring rules were changed.

### Effective M4 debt flag (Fix 1)

`run-bo-case --enable-m4-debt-specialist` (default false) is recorded in the run
`model_configuration` as `m4_debt_specialist_requested` and
`m4_debt_specialist_enabled` (the latter requires the v3 roster), so the manifest
and summary report the real effective value.
