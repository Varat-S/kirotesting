# AI-Assisted Credit Memo Generator (PoC)

A staged, evidence-processing pipeline that turns a mixed source package into a
`FinalCaseSnapshot` JSON and a PDF credit memo rendered from that snapshot.
Deterministic components anchor numerical reliability, AI accelerates
extraction and interpretation under tight constraints, rule-based engines make
escalation decisions explainable, and humans resolve material judgment calls,
all over an append-only audit substrate with versioned prompts, models, and
configuration.

See the full spec in [`.kiro/specs/credit-memo-poc/`](.kiro/specs/credit-memo-poc/):
`requirements.md`, `design.md`, and `tasks.md`.

## Technology stack

- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 + `pydantic-settings` (and JSON Schema for canonical snapshots)
- pandas (tabular parsing and metric computation)
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

Requires Python 3.11 or newer.

```bash
# Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install the project with development extras
pip install -e ".[dev]"

# Configure environment (copy and edit; never commit the real .env)
cp .env.example .env

# Run the API locally
uvicorn app.main:app --reload
# Health check: http://127.0.0.1:8000/health

# Run the tests
pytest
```

## Configuration and secrets

Settings and secrets are read from environment variables, optionally sourced
from a local `.env` file for development. **Secrets are never hardcoded and
never logged** (Requirement 25.2). Secret-bearing fields use Pydantic's
`SecretStr` so they are masked in any `repr`/log output. Only `.env.example`,
containing placeholder keys, is tracked in git.

## Status

Milestone 0 (project scaffold) is in place: package structure, tooling,
FastAPI entrypoint with a health endpoint, an environment-driven config loader,
and a smoke test. Subsequent milestones build the core evidence model,
ingestion and entity controls, deterministic parsers, reconciliation, metrics
and rules, LLM extraction/analysis/challenge, human review, outputs, and the
evaluation harness.
