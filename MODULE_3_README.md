# Module 3 — teammate review

**Work in progress — technically functional but analytically incomplete.**

This branch preserves the accumulated standalone Module 3 work: corporate structure, senior-management stability and continuity, Gemini/Vertex integration, deterministic draft assembly, evidence checks, Validator 7, and a read-only website. It is not a completed credit-analysis module or a lending recommendation system.

## Working

- One combined Module 3 specialist, using the existing provider abstraction and Vertex ADC configuration.
- The live-accepted `vertex-m3-1.6.2` generation contract: typed compact JSON, lossless property-label decoding, and explicit deterministic assembly.
- Model-owned fields remain mandatory; assembly supplies only the actual configured management review window. It does not invent citations, dates, confidence, materiality, investigations or findings.
- Canonical validation is staged: strict model-owned draft validation before the evidence gate, then full governed-result validation after actual Validator 7 outcomes.
- Specialist evidence verification, case-bound hypothetical-structure provenance, graph-reference checks and Validator 7 execution.
- Deterministic corporate-structure JSON/SVG generation and senior-management fact/date verification.
- Existing final JSON files and downstream interfaces, plus a read-only localhost preview that never calls Gemini on refresh.
- Offline positive/adversarial regressions, with mocked SDK transports and private-recording tests that skip when their local artifacts are absent.

## Still under development

- Gemini's corporate-structure credit reasoning remains weak. It often describes limitations rather than conditional lender-risk implications under the supplied scenario.
- Senior-management coverage is incomplete. The latest full HCA run proposed only Hazen out of seven disclosed executives, with no management findings.
- **The latest live run admitted zero substantive credit findings.** Its single structural finding was downgraded to unverified/audit-only; no management findings were submitted.
- The bare executive-table quotation failed the existing role-context verification rule. Table-context handling needs review without weakening evidence requirements.
- Parameter `value_type` domain metadata needs review; unsupported governance/litigation/material-event classifications were withheld by the existing gates.
- Full Business Overview orchestration integration has not been verified. The aligned generation workflow is currently scoped to standalone recorded Vertex M3 execution.

The latest saved case was `HCA_DANIEL_M3_ALIGNED_20261026_02`: HTTP 200/STOP, valid draft and deterministic assembly, canonical validation passed, evidence gate passed and Validator 7 ran. Reported usage was 38,164 input + 2,679 output = 40,843 tokens. Earlier attempts in that task returned HTTP 400. These are historical results, not a claim that another live run will succeed analytically.

## Review map

- `app/services/bo/m3_contract.py`: authoritative-derived draft, compact codec, ownership audit and assembly.
- `app/services/llm/providers_vertex.py`, `providers_vertex_m3.py`, `vertex_m3_diagnostics.py`: provider integration, legacy compatibility baselines, bounded transport and sanitized diagnostics.
- `app/schemas/bo_m3.py`, `bo_m3_management.py`: governed contracts and leadership facts.
- `app/services/bo/m3_case_evidence.py`, `m3_scenario_validation.py`, `m3_grounding.py`, `m3_management.py`: case provenance and evidence safeguards.
- `app/services/bo/m3_roster.py`, `m3_selection.py`, `roster_v3.py`: bounded source selection and disclosed-roster coverage.
- `app/services/bo/m3_standalone.py`, `comparison.py`, `m3_diagnostics.py`: execution and original-response/assembly recording.
- `app/services/bo/corporate_structure.py`, `corporate_structure_render.py`, `m3_structure_context.py`: existing deterministic structure dependencies, retained from the base branch.
- `app/services/bo/m3_output.py`, `m3_artifacts.py`, `m3_demo.py`: six-file output interface.
- `app/api/m3_preview.py`, `app/api/templates/m3_preview.html.j2`, `app/preview.py`: saved-results website.
- `examples/bo_m3/contract_alignment/FIELD_OWNERSHIP_AUDIT.md`: all 68 required-field gaps in the former v1.5 provider contract and their ownership/resolution.

## Setup and offline checks

Use Python 3.11+ and install the existing declared extras in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,vertex]'
```

The latest local verification used `google-genai` 2.29.0. The committed test fixtures are explicitly authored or schema-only; original model recordings and real request bundles are excluded.

From the repository root, run the relevant regression suite without live calls:

```bash
BO_LIVE_TEST=0 PYTHONPATH="$PWD" .venv/bin/python -m pytest \
  tests/unit/test_bo_*.py tests/integration/test_bo_*.py \
  tests/unit/test_vertex_provider.py tests/unit/test_vertex_m3_schema.py \
  tests/unit/test_vertex_m3_400_diagnostics.py tests/unit/test_vertex_m3_typed_core.py \
  tests/unit/test_m3_contract_alignment.py tests/unit/test_real_provider.py \
  tests/unit/test_sampling_configuration.py tests/unit/test_llm_client.py \
  tests/unit/test_agent_validation.py tests/integration/test_structural_overview_thin_slice.py \
  tests/integration/test_m3_saved_preview.py tests/integration/test_browser_preview.py \
  -q --tb=short
```

Publication verification on 27 October 2026: the isolated staged-tree suite passed **802 tests, with 34 skips** and one existing Starlette/httpx deprecation warning. No paid API calls were made.

Private live-response replays and some historical filing tests skip when their artifacts are absent. No paid call is substituted. The simulated source fixture at `data/test_inputs/hca_simulated_corporate_structure.json` is deliberately versioned; its entities, stakes and guarantor assignments are hypothetical, not verified HCA legal information.

## Website and output contract

After an expressly authorised execution has written a saved output directory:

```bash
PYTHONPATH="$PWD" .venv/bin/python -m app.preview \
  --m3-output output/bo_m3/YOUR_SAVED_CASE --port 8000
```

Open `http://127.0.0.1:8000/`. The preview shows the diagram, governed findings, original proposals, evidence references, limitations and actual validation stages.

The six standard files remain `m3_analysis.json`, `m3_corporate_structure.json`, `m3_structure_provenance.json`, `validation_report.json`, `evaluation_scorecard.json` and `run_manifest.json`.

Live execution requires separately configured Vertex/ADC credentials and explicit approval for its request budget. Existing diagnostic execution flags are not permission to spend credits. This publication task performs no live Gemini/Vertex calls.

## Excluded from this branch

Credentials, `.env` files, SQLite databases/sidecars, raw model responses, case-specific live exports, temporary outputs/logs/caches, local agent notes and Google Cloud SDK installation files remain local. Historical root-level investigation reports containing machine-specific paths are superseded for teammate review by this portable note and the schema/ownership fixtures.
