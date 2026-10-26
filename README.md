# AI-Assisted Credit Memo Generator (PoC)

A staged, evidence-processing pipeline that turns a mixed source package into a
`FinalCaseSnapshot` JSON and a PDF credit memo rendered from that snapshot.
Deterministic components anchor numerical reliability, AI accelerates
extraction and interpretation under tight constraints, rule-based engines make
escalation decisions explainable, and humans resolve material judgment calls,
all over an append-only audit substrate with versioned prompts, models, and
configuration.

The default browser test case uses the three supplied Delta PDFs (earnings
release, non-GAAP supplement and the PDF supplied under the 10-K filename), with
the Excel alternative excluded. Open `/inspect` to prepare or rerun it without
choosing files. See [the package notes](examples/delta-2025/README.md) for the
image-only supplement and the corrected FY2025 10-K.

The local browser includes **Upload files and preview parsed evidence** at
`/inspect`: SEC filing HTML/bundles, PDF, CSV, XLSX and standalone XBRL can be
processed through deterministic evidence review. The screen exposes facts,
provenance, mapping issues, metrics and the exact prepared canonical-evidence
LLM input. Processing stops for review; browser controls then support evidence
decisions, draft generation, exception resolution, approval and finalization.
OpenAI Responses and compatible local/remote providers are implemented; live
execution requires local provider/model settings and credentials. See [TESTING_GUIDE.md](TESTING_GUIDE.md) for actual-file examples,
required metadata and parser limits.

See the full spec in [`.kiro/specs/credit-memo-poc/`](.kiro/specs/credit-memo-poc/):
`requirements.md`, `design.md`, and `tasks.md`.

## Agentic credit analysis

An optional **agentic analysis** architecture extends the downstream analytical
portion of the pipeline with a bounded, concurrent multi-agent layer over a
deterministic parameter + scoring substrate. It runs after the same
human-reviewed `CanonicalEvidenceSnapshot` boundary and keeps the deterministic-
first philosophy: code computes all numbers, ratios, scores and policy tests;
LLMs only interpret, classify, challenge and synthesize. Select it with
`analysis_mode="agentic"` on `CreditMemoPipeline`; the default `legacy` path is
unchanged. A Google Vertex AI (Gemini) provider is available behind the existing
provider abstraction (optional `vertex` extra; credentials via ADC/service
account, never committed; no live calls in tests).

See [`AGENTIC_ANALYSIS_REPORT.md`](AGENTIC_ANALYSIS_REPORT.md) for the overview
and the live-Vertex run instructions, and the full spec in
[`.kiro/specs/credit-memo-agentic-analysis/`](.kiro/specs/credit-memo-agentic-analysis/).

## Business Overview module (BO-only)

The Business Overview runs on its own, without the Financial, Structuring or
Credit Orchestrator agents. Six narrow specialists (BO6 analyses interactions
across the validated output of BO1-BO5) return evidence-based hypotheses;
deterministic code verifies quotes, numbers and known unsupported inferences
before anything is accepted.

| Command | Runs | Produces |
|---|---|---|
| `run-bo-specialists` | six specialists + validators | executive summary, six-specialist comparison (HTML/PDF), validation audit, old-versus-new benchmark, JSON |
| `run-bo-case` | specialists, Business Orchestrator, Business Challenger, one bounded rerun | analyst memo, audit report, handoff pending human review |

```powershell
python -m app.cli run-bo-specialists <CASE_ID> --package <package.json> --live
python -m app.cli run-bo-case <CASE_ID> --package <package.json>
```

Each command takes `--live` (configured provider, incurs cost), `--responses`
(scripted offline simulation) and, for the specialists, `--replay` (re-validate
recorded responses with no model call). `run-bo-case` with none of these asserts
nothing and produces no conclusion. Inspect `run-bo-case` runs at `/bo` in the
local browser.

Where to read next:

* [`app/services/bo/README.md`](app/services/bo/README.md) - working guide:
  module map, validation rules, how to extend, pitfalls. **Start here if you are
  changing the module.**
* [`BO_QUALITY_REMEDIATION_REPORT.md`](BO_QUALITY_REMEDIATION_REPORT.md) -
  current status, the HCA live demonstration, old-versus-new results, limits.
* [`BUSINESS_OVERVIEW_V2_REPORT.md`](BUSINESS_OVERVIEW_V2_REPORT.md) - the
  original build of the module.
* [`examples/healthcare/hca-2025/`](examples/healthcare/hca-2025/) - the HCA
  demonstration case; [`examples/healthcare/stryker-bo/`](examples/healthcare/stryker-bo/)
  - the Stryker offline regression case.
* Spec: [`.kiro/specs/credit-memo-business-overview-v2/`](.kiro/specs/credit-memo-business-overview-v2/).

Not validated: model quality beyond one model and one filing, the logical
correctness of accepted findings (checks are lexical and structural), and the
full BO path (orchestrator, challenger, memo) on HCA with a live model.

### Module 4 debt-structure specialist (debt structure, refinancing & debt utilisation)

Module 4 has **two agents** with non-overlapping lanes: the Strategy & Capital
Allocation agent (`m4_strategy_capital`) owns acquisition strategy, integration
risk, capex strategy and the qualitative capital-allocation / stewardship
judgement; the **debt-structure specialist** below owns the debt — structure,
creditor priority, refinancing, maturity, and how capital deployment was funded
and affected leverage.

The debt-structure specialist is an **independent specialist** (Business
Overview Architecture V6): Debt Structure, Refinancing History & Debt
Utilisation. It reads the shared SEC 10-K and structured financial data and
produces its own four deliverables. It does **not** consume the outputs of
Modules 1/2/3 (cross-module synthesis is a separate downstream concern) and it
does **not** draft loan term sheets, covenants or security agreements (that
belongs to the downstream Risk Mitigation & Structuring agent).

The four schema-validated deliverables are:

1. **Existing Debt & Creditor Priority Map** — itemized senior-notes and
   non-note obligation schedules plus a creditor-priority / structural-
   subordination framework (Tier 1 direct OpCo secured, Tier 2 senior unsecured
   with upstream guarantees, Tier 3 unguaranteed HoldCo), the Structural
   Subordination Drag Ratio, and prospective bank-loan placement under both a
   secured and a senior-unsecured assumption.
2. **Historical Refinancing & Debt Maturity Analysis** — an itemized refinancing
   log plus the refinancing metrics (weighted-average lead time τ_ref, cost
   spread delta ΔS, maturity-extension horizon ΔT, distress-event count).
3. **Debt Utilisation & Leverage Impact Assessment** — a macro capital-
   deployment reconciliation (how each stream was funded and its leverage
   impact) plus a debt-utilisation matrix (debt-funded vs internally-funded
   deployment; distributions vs leverage balance). Capex-efficiency and
   M&A-integration *quality* are deliberately excluded — those are the Strategy
   & Capital Allocation agent's remit, so the two Module 4 agents do not overlap.
4. **Module 4 Credit Conclusion** — an evidence-driven synthesis matrix and an
   executive credit summary box.

Deliverables and columns are Pydantic + JSON Schema contracts in
`app/schemas/module4.py`; the versioned agent prompt lives in
`app/services/modules/module4/prompt.py` (registered in the prompt catalogue and
driven via the existing `analyze` method, like the other BO specialist prompts);
and `app/services/modules/module4/specialist.py` orchestrates the LLM call with
fixed-schema validation before use.

**Validator 8** (`app/services/modules/module4/validator8.py`) is a pure,
deterministic re-check of the agent's output — it never calls an LLM. It covers
five checks: (1) debt-tranche reconciliation to reported total indebtedness,
(2) subordination-ratio recomputation, (3) chronological maturity ordering,
(4) legal-entity / borrower-vs-guarantor attribution (e.g. an OpCo issuer
distinct from its HoldCo guarantor), and (5) double-counting prevention (undrawn
revolver limits and intercompany borrowings are excluded from totals). A
Validator 8 failure raises a mandatory escalation rather than producing silent
prose. Tests exercise the agent and validator against the HCA Healthcare FY2025
10-K fixture (`tests/fixtures/hca_module4.py`), which reconciles to the reported
$46,492M total indebtedness.

## Sector benchmarking (medical devices)

A case can opt into contextual industry benchmarking by adding
`sector_benchmark` to its source package. A validated benchmark workbook supplies
published industry aggregates (not peer medians or percentiles); borrower metrics
are compared only where their definitions are compatible, and the result informs
the memo narrative without touching official risk scores. Cases that do not opt
in are unaffected.

See [`HEALTHCARE_BENCHMARKING_REPORT.md`](HEALTHCARE_BENCHMARKING_REPORT.md) for
what is and is not verified, [`examples/healthcare/`](examples/healthcare/README.md)
for the offline pilot (synthetic fixture values), and the spec in
[`.kiro/specs/credit-memo-healthcare-benchmarking/`](.kiro/specs/credit-memo-healthcare-benchmarking/).

## Technology stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 + `pydantic-settings` (and JSON Schema for canonical snapshots)
- pandas (tabular parsing and metric computation)
- lxml (bounded SEC Inline-XBRL, context/dimension and local linkbase parsing)
- SQLAlchemy over SQLite (PoC), migratable to PostgreSQL
- Jinja2 (deterministic memo rendering)
- pytest

## Project layout

```text
.
├── pyproject.toml
├── .env.example
├── app/
│   ├── api/          # FastAPI routes
│   ├── core/         # config loading/versioning, hashing, cutoff, security helpers
│   ├── models/       # SQLAlchemy ORM entities
│   ├── schemas/      # Pydantic + JSON Schema for canonical snapshots
│   ├── prompts/      # versioned prompt templates + registry
│   ├── config/       # versioned/hashed config (paired with top-level config/)
│   ├── main.py       # FastAPI entrypoint (app factory + /health)
│   └── services/     # one subpackage per pipeline stage:
│       ├── ingestion/   entity/        extraction/
│       ├── reconciliation/  metrics/   benchmarking/
│       ├── analysis/   escalation/     reporting/
│       └── audit/      evaluation/
├── config/   # tolerances/ policy/ peers/ metric_defs/ rules/ source_profiles/
├── data/     # raw/ parsed/ normalized/ derived/ snapshots/
├── tests/    # unit/ integration/ golden/ ablation/ prompts/ end_to_end/ leakage/
├── evals/    # cases/ manifests/ annotations/ results/ reports/
└── output/   # json/ pdf/
```

## Getting started

Python 3.11+ is supported. The preferred reproducible setup uses the checked-in
`uv.lock` (including development/test dependencies):

```powershell
python -m pip install uv==0.12.23
uv sync --locked --python 3.11 --extra dev
.\.venv\Scripts\Activate.ps1
$env:DEBUG = 'false'
python -m pytest -q
```

`uv` creates `.venv` and can provision the requested Python version. On an
existing pip environment, `pip install -e ".[dev]"` remains available but resolves
the declared version ranges rather than the lock. Optional `ocr` and `pdf`
extras require system dependencies; the default setup produces JSON and HTML.

Create an offline draft from the labelled synthetic evidence package:

```powershell
python -m app.cli run-case SYNTHETIC_2024 --package examples/synthetic-case/package.json
```

This executes the actual parsers, mapping, reconciliation, deterministic metrics,
trends, peer benchmarking, rules, fake extraction/analysis/challenge and grounding.
The CLI prints output paths and outstanding mandatory escalations. The bundled
sources and truth manifest are synthetic. Regenerate them with development extras
using `python examples/generate_synthetic_case.py`.

Approval is a separate human action. After reviewing the exact draft and
resolving mandatory escalations, record approval and finalize that version:

```powershell
python -m app.cli approve SYNTHETIC_2024 --version 1 --reviewer credit.officer --reason "Reviewed the synthetic draft"
python -m app.cli finalize SYNTHETIC_2024 --version 1 --reviewer credit.officer
```

Approval is bound to the reviewer, case, snapshot version and draft content.
Changing the draft requires a new approval. Finalization freezes the draft row;
a subsequent revision creates a new draft that supersedes the frozen snapshot.
Add `--pdf` to `finalize` only with a working native PDF renderer.

To choose local storage, put global options before the command:

```powershell
python -m app.cli --database data/demo.db --output output/demo run-case SYNTHETIC_2024
```

All nine illustrative artifacts in `config/poc` are versioned and hashed through
`ConfigRegistry`. Input packages declare availability timestamps, entities,
scope, accounting basis and extraction options. Future sources are rejected and
logged. Missing or conflicting evidence produces explicit states and review
requirements. Generic cash is distinct from unrestricted cash.

For a browser preview with three synthetic cases (full, missing financials,
conflicting revenue), run:

```powershell
.\.venv\Scripts\python.exe -m app.preview --seed-demo
```

Open `http://127.0.0.1:8000/`. The workbench shows saved draft/final data,
metrics and provenance, with memo HTML/JSON links. It uses a separate
`data/browser_demo.db` and does not approve cases. See [TESTING_GUIDE.md](TESTING_GUIDE.md)
for manual checks, upload testing, approval commands, and remaining work.

For the API, optionally copy `.env.example` to `.env`, supply any required local
settings, and run `uvicorn app.main:app --reload`. The health endpoint is
`http://127.0.0.1:8000/health`.

Existing SQLite databases require the additive migration described in
[HARDENING_REPORT.md](HARDENING_REPORT.md#database-compatibility). Back up the
database before running `python -m app.migrate --database data/credit_memo.db`.

## Configuration and secrets

Settings and secrets are read from environment variables, optionally sourced
from a local `.env` file for development. **Secrets are never hardcoded and
never logged** (Requirement 25.2). Secret-bearing fields use Pydantic's
`SecretStr` so they are masked in any `repr`/log output. Only `.env.example`,
containing placeholder keys, is tracked in git.

## SEC filing bundles

The supplied real NVIDIA filing can run offline with no SEC or LLM credentials:

```powershell
$env:DEBUG = 'false'
.\.venv\Scripts\python.exe -m app.cli --database data/sec_demo.db --output output/sec_demo run-sec-case NVDA_2026 --bundle examples/sec/nvda-2026/bundle.json --entity-name 'NVIDIA Corporation' --cutoff 2026-03-01T00:00:00Z
.\.venv\Scripts\python.exe -m app.preview --database data/sec_demo.db --output output/sec_demo --port 8001
```

Open <http://127.0.0.1:8001/>. The saved draft includes SEC diagnostics, facts
and candidate passages. The later proxy is rejected at this cutoff. Available
financial metrics require evidence review; unknown mappings remain visible.

Optional public SEC acquisition uses `sec-list`, `sec-fetch`, or `run-sec-case
--ticker`. Set `SEC_USER_AGENT` to your name and real contact email; no SEC API
key is required. Acquisition passes every file through the normal cutoff gate
before parsing. See [SEC_INTEGRATION_REPORT.md](SEC_INTEGRATION_REPORT.md) for
exact commands, migration, tests, ported behavior and known limits.

## Status

The offline end-to-end pipeline is wired and tested with actual synthetic
XBRL/XLSX/CSV/PDF sources, eight source-removal ablations, historical replay and
canonical memo reproducibility. Regression tests cover reconciliation integrity,
metric evidence quality, case-specific entity roles and content-bound human approval.
The real NVIDIA Inline-XBRL fixture also runs through the pipeline, with
dimension-safe reconciliation, 52/53-week annual periods, conservative SEC
availability, narrative retrieval and independent companion admission.

`RealProviderBackend` supports OpenAI Responses and compatible Chat Completions,
with structured outputs, input limits and recorded failure handling. Mocked HTTP
tests validate integration; live narrative accuracy still requires a configured
provider and evaluation. Binary PDF export needs the optional `pdf` dependency
and native libraries. OCR uses Tesseract when installed, or Windows English OCR. Immutability is enforced by the application
and ORM in this PoC rather than hardware/WORM storage.

See [HARDENING_REPORT.md](HARDENING_REPORT.md) for changes, tests, validation and
migration details, and [REAL_DATA_READINESS.md](REAL_DATA_READINESS.md) for parser
limits and the next real-filing validation step.

See [DELTA_VALIDATION_REPORT.md](DELTA_VALIDATION_REPORT.md) for the expanded
real-document reference checks, OCR coverage and remaining review requirements.
