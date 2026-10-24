# BO + FO Pipeline Integration — Baseline Report

**Branch:** `feat/bo-fo-pipeline-integration` (from `main @ cb7918f`)
**Date:** 2026-10-24
**Runtime:** Python 3.11, `uv sync --locked --python 3.11 --extra dev`, tests run
with a process-local `DEBUG=false` override (`.env` never edited).

## 1. What was delivered

1. **Integrated branch** merging the three approved branches with no silently
   dropped functionality — see `BO_FO_INTEGRATION_MERGE_REPORT.md`.
2. **Merge & conflict-resolution report** (`BO_FO_INTEGRATION_MERGE_REPORT.md`).
3. **Unified artifact manifest** — spec (`ARTIFACT_MANIFEST_SPEC.md`) +
   implementation (`app/schemas/artifact_manifest.py`, `app/services/registry/`).
4. **Read-only Pipeline Explorer** — `app/api/pipeline_explorer.py` +
   `app/api/templates/pipeline_explorer.html.j2`, mounted at `GET /pipeline`.
5. **Generic JSON viewer + deterministic PDF exporter** —
   `app/services/registry/json_render.py`.
6. **Updated tests** — `tests/unit/test_artifact_registry.py` (20),
   `tests/integration/test_pipeline_explorer.py` (11).
7. **Consolidated BO + FO output contract for SO** (`BO_FO_OUTPUT_CONTRACT.md`).
8. **This baseline report.**

## 2. Test summary

| Scope | Result |
|-------|--------|
| Clean `main` baseline (whole suite) | 1323 passed · 131 skipped · **12 errors (environmental)** |
| Integrated branch (initial), excl. environmental file | 1860 passed · 131 skipped · 0 failed |
| **Integrated + hardening (PR #3 follow-up)** | **1892 passed · 131 skipped · 0 failed** |
| New registry/render tests | 20 passed |
| New explorer/integration tests | 11 passed |
| `test_bo_recorded_completion.py` (integrated) | 1 passed · 12 errors (same environmental blocker as baseline) |

The integrated branch adds **+537** passing tests over the clean-main baseline
(1323 → 1860) with **zero failures** and no change to the skip set beyond the
documented local-asset conditions.

### Reproduce

```bash
uv sync --locked --python 3.11 --extra dev
# full suite (excluding the env-blocked recorded-completion file):
DEBUG=false uv run --python 3.11 pytest -q \
  --ignore=tests/integration/test_bo_recorded_completion.py
# just the integration deliverables:
DEBUG=false uv run --python 3.11 pytest -q \
  tests/unit/test_artifact_registry.py tests/integration/test_pipeline_explorer.py
```

## 3. Integration decisions (as applied)

* **Module 1** — six-agent design (incl. Agent 6 synthesis), orchestrator,
  challenger, bounded synthesis V1→V2, deterministic validation and human-review
  controls preserved unchanged.
* **Module 3** — the complete M3 branch integrated; authoritative for verified
  corporate/entity relationships. Validator 7 numeric evidence binding preserved.
* **Module 4** — **both** internal specialists preserved under one governed
  output: `m4_strategy_capital` (strategy/capital-allocation) and the independent
  `Module4Specialist` (debt structure / refinancing) with its deterministic
  Validator 8 and **mandatory escalation** (`R-MODULE4-VALIDATOR8-01`). The two
  "Validator 8"s are disambiguated, not collapsed. Details in
  `BO_FO_OUTPUT_CONTRACT.md` §3.
* **Financial Overview** — measures, scoring, pre-checks, trigger agents,
  analyst review, API/UI and reporting integrated; deterministic calculations
  preserved; rating methodology treated as **provisional**; filing identity
  reconciled onto the shared case/evidence snapshot.
* **Shared infra** — one `app/cli.py` conflict resolved (kept all commands); FO
  ORM tables + additive migration verified; prompt catalogue append-only.

## 4. Acceptance criteria — status

| Criterion | Status |
|-----------|--------|
| All three branches integrated without silently dropping functionality | ✅ |
| Existing BO orchestration & governance remain functional | ✅ (suite green) |
| M3 & M4 outputs have stable, documented contracts | ✅ (`BO_FO_OUTPUT_CONTRACT.md`) |
| FO associated with the same case & source version as BO | ✅ (shared `case_id` + snapshot) |
| All registered outputs discoverable via the artifact registry | ✅ (`build_manifest`) |
| Navigate the left-to-right graph; click agents to inspect outputs | ✅ (`/pipeline`) |
| JSON & PDF exports correspond to actual recorded artifacts | ✅ (tested: export == recorded) |
| Known material limitations explicitly documented | ✅ (this report §5) |
| SO can consume a stable, versioned BO + FO handoff | ✅ (`BO_FO_OUTPUT_CONTRACT.md`) |

## 5. Known limitations & unresolved escalations

* **Environmental (not a regression):** `tests/integration/test_bo_recorded_completion.py`
  (12 errors) needs a git-ignored live HCA R5 artifact
  (`output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/...`) absent in a clean
  sandbox. Present identically on clean `main`.
* **FO rating methodology is provisional.** `composite_score` / `credit_rating` /
  `risk_level` must be treated as provisional; withheld entirely when awaiting
  review.
* **No live model invoked.** All verification used scripted/offline paths.
  Live HCA remains behind the explicit opt-in (`--live`) paths and was not run
  (no paid model was called; `edgar_filings/` 10-K folders are not shipped).
* **Cross-module synthesis V2 and the BO+FO→SO node** are shown as clearly
  labelled *illustration* scaffolding in the Explorer until an end-to-end
  cross-module run is recorded; SO should consume the BO handoff + FO overview
  directly, keyed by case + evidence snapshot.
* **Module 4 unresolved escalations** (if a debt-structure Validator 8 fails in a
  real run) surface as `status=failed` + `escalations` in the manifest and drive
  case status to `escalated` — visible, never silent.

## 6. Demonstration instructions

The UI is demonstrable **now** against real recorded M3 fixtures shipped in the
repo (byte-stable, clearly labelled "deterministic fixture", never a live run).

```bash
DEBUG=false uv run --python 3.11 uvicorn app.main:app --port 8000
# then open http://localhost:8000/pipeline  (also linked from the home page "/")
```

1. Pick a case in the header dropdown — e.g. **HCA (simulated M3 fixture)** or any
   `fictional_*` fixture. The left-to-right graph renders with status colours and
   a legend; use **Fit to screen / + / −** and drag to pan.
2. Click the **M3 Corporate Structure & Management** node (or **Validator 7**) to
   open the inspector: identity, status, dependencies, evidence IDs, parsed
   output (generic hierarchical viewer), raw JSON, validator/review status and
   model/prompt provenance.
3. Use **Download JSON** (exact recorded bytes) and **Render PDF** (deterministic
   export) in the inspector. **Open manifest JSON** in the header shows the full
   `artifact-manifest-1.0` index.
4. Pick a non-existent case to see the **illustration-mode** graph (every node
   `absent`) — the explorer stays useful with no run.

Re-generate the M3 fixtures (offline, no model) with the recorded reproduce
command, e.g.:

```bash
DEBUG=false python -m app.cli \
  --output examples/bo_m3/structural_credit/demo_output/hca_simulated run-m3 NFH_BO \
  --corporate-structure "examples/bo_m3/Simplified HCA Corp Structure.json" \
  --responses examples/bo_m3/structural_credit/hca_simulated_single_pass.json
```

## 7. Final merge recommendation

**Recommend:** keep `feat/bo-fo-pipeline-integration` as a reviewable PR into
`main`; **do not auto-merge.** All three branches are integrated cleanly, the
suite is green (0 failures, +537 tests), contracts are documented, and the
read-only Explorer demonstrates the pipeline against real recorded artifacts. The
only open items are the pre-existing environmental test blocker and the
explicitly-documented provisional FO rating and illustration scaffolding — none
require a material architectural choice beyond those already resolved here.

## 8. Changed-file summary (vs `main`)

196 files changed, +50,372 / −14. By area:

* **M3 branch:** M3 services (`app/services/bo/m3_*.py`), schemas, examples
  (corp-structure fixtures + demo outputs), ~11 M3 test files.
* **Module 4 branch:** `app/schemas/module4.py`,
  `app/services/modules/module4/*`, `scripts/build_report_pdf.py`,
  `scripts/run_module4_live.py`, fixtures + 3 M4 test files.
* **FO branch:** `app/services/fo/*` (measures, scoring, triggers, runner,
  reporting, review), `app/api/financial_overview.py`, FO ORM tables +
  migration, `config/financial_overview/*`, ~35 FO test files.
* **Integration additions (this task):** `app/schemas/artifact_manifest.py`,
  `app/services/registry/*`, `app/api/pipeline_explorer.py`,
  `app/api/templates/pipeline_explorer.html.j2`, `app/main.py` (router wire-up),
  `app/api/templates/home.html.j2` (nav link), `app/cli.py` (conflict
  resolution), `tests/unit/test_artifact_registry.py`,
  `tests/integration/test_pipeline_explorer.py`, and the four reports/specs
  (`BO_FO_INTEGRATION_MERGE_REPORT.md`, `ARTIFACT_MANIFEST_SPEC.md`,
  `BO_FO_OUTPUT_CONTRACT.md`, this file).

---

## 9. PR #3 hardening follow-up

An independent review identified gaps between the documented architecture and
the implemented guarantees. All five were closed on the same branch
(`feat/bo-fo-pipeline-integration`); no architecture was redesigned and no
scoring methodology changed.

| # | Issue | Change made | Tests added | Verification |
|---|-------|-------------|-------------|--------------|
| P1 | Module 4 debt specialist ran only standalone; the BO roster registered only `m4_strategy_capital`, so "both specialists feed one governed M4 output" was not executable. | `app/services/bo/m4_debt.py` builds `Module4Inputs` from admitted snapshot evidence only; `_BoRun._module4_debt_phase` runs `Module4Specialist` via the run's `LLMClient`+`EscalationEngine`, folds it into `module_conclusions["M4"]` (consumed by synthesis/challenger/governance). Opt-in `enable_m4_debt_specialist` flag (default off preserves behaviour). Validator-8 failure → mandatory escalation + M4 incomplete → human review. `ScriptedBoBackend` routes a dedicated `m4_debt` key. | `tests/integration/test_bo_m4_dual_specialist.py` (4) | ✅ both specialists reach governed output, synthesis, challenger, governance; negative escalation stays unresolved; unavailable when no debt evidence; flag-off unchanged |
| P2 | Matching case id did not prove same canonical snapshot; FO snapshot provenance not populated. | `ProvenanceRecord` five-level distinction; BO = verified snapshot, FO = `unverified` (independent loader) with preserved filing id + signature. `cross_module_consistency` compares overlapping facts only within compatible units/periods, flags, never overwrites. | `tests/unit/test_bo_fo_provenance.py` (12) | ✅ verified vs unverified; conflicting/period/unit/entity cases flagged, values preserved |
| P3 | Agents appeared in the graph without an independently accessible recorded output. | Per-agent / synthesis / challenger entries indexed from the existing `audit_report.json` (`bo.agent.<id>.<run>`, `bo.synthesis.<run>`, `bo.challenger.<run>`); inspector returns the producer's own slice; graph `prefer_artifact_type` resolves nodes within the same run. | `tests/integration/test_registry_per_agent.py` (7) | ✅ each specialist discovered, same-run mapping, slice (not memo), absent debt node honest |
| P4 | Indexer could default an existing JSON to `validated`. | Removed the `validated` default; absent authoritative signal → `incomplete`; pending review → `qualified`; M3 derived files inherit the run_manifest `validation` block; handoff pending → `qualified`. | added to `tests/unit/test_artifact_registry.py` (4) | ✅ no-metadata→incomplete, pending→qualified, explicit→validated |
| P5 | `download.json` reconstructed canonical JSON, not original bytes. | `original.json` (exact bytes + `X-Original-SHA256` + ETag) vs `normalized.json` (canonical + `X-Canonical-SHA256`, labelled); `download.json` backwards-compatible (original for whole files, normalized for slices). | `tests/integration/test_json_export_integrity.py` (6) | ✅ byte-for-byte original; canonical distinct; slice handling |

### Completion-criteria status

* Both M4 capabilities participate in the intended BO path (opt-in flag) and
  reach synthesis/governance — ✅.
* Mandatory escalations cannot be silently bypassed (M4 debt failure → escalation
  + incomplete module + forced human review) — ✅.
* BO/FO evidence compatibility is explicitly verified or marked unresolved — ✅.
* Recorded individual-agent outputs are inspectable; the graph reflects recorded
  status; file existence cannot produce a false `validated` — ✅.
* JSON exports have defined integrity guarantees (original vs normalized) — ✅.
* Existing BO/FO functionality intact (1892 passed / 0 failed) — ✅.

### Remaining limitations (operational vs analytical)

*Operational blockers:* the 12 `test_bo_recorded_completion.py` errors still
require the git-ignored HCA R5 artifact (unchanged, environmental). No live model
was invoked; a true live HCA run remains pending behind the explicit opt-in paths
(prepare the package/filing, run `run-bo-case ... --bo-roster v3` with
`enable_m4_debt_specialist`, then `run-fo-case`; assert combined M4 output + FO
overview + consistency report). *Analytical limitations:* FO rating methodology
is provisional; FO↔BO canonical-snapshot equivalence is unverified by
construction; cross-module synthesis V2 and the SO-handoff node remain labelled
illustration scaffolding until an end-to-end cross-module run is recorded.

### Final merge recommendation (updated)

**Recommend proceeding to final merge review of PR #3; do not auto-merge.** All
five hardening gaps are closed with executable code and tests, the full offline
suite is green (1892 passed, 0 failed), and the documentation now matches the
implemented guarantees. The only open items are the documented environmental
blocker and the explicitly-labelled provisional/illustration items — none require
a new architectural decision.
