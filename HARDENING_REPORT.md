# Credit memo hardening

Working branch: `fix/credit-memo-hardening`.
Starting commit: `99cb906fd824a2aa232aa6251c09e1200999ff0d`.

## Changes

1. Reconciliation uses explicit ratio tolerances and conservative unknown-field
   defaults, preserves all semantic states, compares every source pair, checks
   consolidation scope, and records the selected fact/value/reason and selection
   configuration version. A `fact_reconciled` event records every resolved state.
2. Metrics expose calculation state, evidence quality and review requirements,
   preserve input fact IDs, and persist those quality fields. Engine version is
   `metric-engine-1.1.0`.
3. Finalization queries persisted approvals matching the signer, case, version and
   draft content hash. It finalizes an existing draft row. Superseding a frozen
   snapshot creates a new draft requiring fresh approval.
4. Global legal entities can participate in multiple cases through `CaseEntity`.
   Case-specific borrower/guarantor roles and expected consolidation scope live
   in that association. Entity resolution uses case membership.
5. Canonical financial mappings preserve original labels/concepts and provenance,
   record version/hash and explicitly return mapped/unmapped/ambiguous results.
   All nine material configuration artifacts have labelled files in `config/poc`.
6. `CreditMemoPipeline` connects ingestion, parsing, mapping, metadata validation,
   reconciliation, evidence snapshots, metrics, trends, peers, rules, fake AI
   extraction/analysis/challenge, grounding, escalation and draft JSON/HTML.
   Finalization is a separate action consuming persisted human approval.
7. Historical cutoffs reject/log future evidence. Stable source/fact/escalation
   identities avoid hidden random ordering in canonical artifacts. Operational
   model-run IDs/timestamps remain in their audit tables. Pinned configuration
   and historical metric/rule/prompt versions can be reused.
8. Numeric grounding handles one unambiguous number with compatible percentage,
   currency or ratio units. Multi-number/multi-evidence cases remain
   `not_verifiable`. Invalid AI responses are logged and produce a draft blocked
   for review; invalid narrative is not copied into canonical output.
9. Optional PDF detection checks importability, including native-library failures.
   Added a portable `uv.lock`, Python 3.11/3.12/3.13 GitHub Actions configuration,
   runnable CLI, labelled synthetic package, additive SQLite migration and
   real-data readiness notes.

## Validation

Baseline: Python 3.13.3, 321 tests, **320 passed / 1 failed / 0 skipped**.
The failure was WeasyPrint installed without native Windows libraries.
The inherited `DEBUG=release` was overridden with `DEBUG=false`. An accessible,
unique temporary test directory avoided a sandbox permission problem in the
default pytest temp directory.

Baseline direct packages: FastAPI 0.142.2, Uvicorn 0.54.0, Pydantic 2.13.5,
pydantic-settings 2.15.0, SQLAlchemy 2.1.3, pandas 3.0.6, Jinja2 3.1.6,
jsonschema 4.26.0, python-multipart 0.0.32, openpyxl 3.1.5, pdfplumber 0.11.10,
pytest 9.1.1, httpx 0.28.1 and reportlab 5.0.1. Installed optional packages were
pytesseract 0.3.13, Pillow 12.3.0 and WeasyPrint 70.0. The new lock records full
transitive versions and artifact hashes across supported Python versions.

Final verification on 4 October 2026:

| Environment | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Baseline, Python 3.13.3 | 320 | 1 | 0 |
| Clean locked install, Python 3.11.15 (`.venv-verify`) | 373 | 0 | 1 |
| Existing environment, Python 3.13.3 (`.venv`) | 373 | 0 | 1 |

The subsequent browser-preview pass added a case index, actual draft data in
the workbench HTML, metric and source tables, read-only memo HTML/JSON routes,
and a local preview launcher with full/missing/conflicting synthetic cases.
Source completeness in that view is derived from saved document tags and its
pinned profile version. Drafts are explicitly unapproved, and memo headings
distinguish draft from final. Two additional integration tests verify that
viewing these results creates no approvals/audit events and leaves snapshot
content unchanged. Final verification including this preview: **375 passed /
0 failed / 1 skipped** on both Python 3.11.15 and 3.13.3. See
`TESTING_GUIDE.md` for browser checks and commands.

Both final runs reported the same Starlette/httpx deprecation warning. The
baseline PDF failure is now detected as an unavailable optional native backend,
rather than an import-time rendering crash. The skipped test is
`tests/unit/test_memo_output.py::test_pdf_production_or_skip`;
its HTML artifact is verified before the skip.

Ruff's undefined-name/unused-import checks and formatting passed for all changed
Python files. `git diff --check`, `pip check`, and offline `uv lock --check`
also passed. The original `.venv` is retained; `.venv-verify` independently
validated installation from the lock without the optional native extras.

A fresh CLI run against `data/hardening_final_demo.db` admitted all eight
synthetic files, produced evidence version 1 and draft version 1, and reported
zero rejected sources and zero mandatory escalations. Its artifacts are
`output/hardening_final_demo/SYNTHETIC_2024/draft_v1.json` and
`output/hardening_final_demo/SYNTHETIC_2024/draft_v1.html`. These are synthetic
drafts, not approved credit decisions.

The optional PDF production test skips when no usable backend exists. OCR fixture
tests exercise their controlled fake backend; they do not establish that the
Tesseract executable is installed. CI configuration is added locally; its remote
GitHub Actions jobs have not been executed in this session.

New tests:

- `tests/unit/test_hardening.py`: tolerance, states, ordering, selection, scope,
  metric quality, content-bound approval, draft lifecycle and multi-case entities.
- `tests/unit/test_financial_mapping.py`: source-specific names, qualifiers,
  ambiguity, provenance and configuration versions.
- `tests/unit/test_grounding_units.py`: ratios, percentages, currency scaling,
  commas and ambiguous assertions/evidence.
- `tests/integration/test_real_pipeline.py`: actual CSV to canonical outputs.
- `tests/integration/test_pipeline_acceptance.py`: full actual source package,
  frozen outputs, seeded conflict and reproducibility including memo hash.
- `tests/ablation/test_pipeline_ablation.py`: eight actual source-removal runs,
  measured completeness/accuracy/metric availability/coverage and no invented values.
- `tests/leakage/test_pipeline_replay.py`: two historical cases, rejected future
  peer/financial/market sources, contemporaneous manifests and future-outcome separation.
- `tests/integration/test_hardening_boundaries.py`: scope expectations, mandatory
  resolution, semantic audit, pinned definitions, rejected AI and CLI workflow.
- `tests/integration/test_hardening_migration.py`: additive migration, idempotence,
  case-role backfill and preservation of snapshot payloads.

Existing finalization, memo, workbench and audit tests were updated for actual
persisted approvals, fresh successor approval and the added audit event types.

## Database compatibility

New databases are created with the updated schema. For an existing SQLite PoC
database, make a backup and run:

```powershell
Copy-Item -LiteralPath data/credit_memo.db -Destination data/credit_memo.pre-hardening.db
.\.venv\Scripts\python.exe -m app.migrate --database data/credit_memo.db
```

The migration adds mapping columns to facts, selection columns to reconciliation
records, evidence-quality columns to metrics and the case/entity association.
Existing metrics default to unverified/review-required rather than claiming
retrospective verification. Existing snapshot/audit/review JSON is untouched.
Legacy entity role/case columns are retained for migration compatibility; new
code uses `CaseEntity` for case membership and roles. PostgreSQL needs an
equivalent reviewed migration before deployment there.

## Local commands

Create a synthetic draft (no LLM keys or native PDF/OCR dependencies). This is
the exact demo command verified in this session; another run adds new snapshot
versions to the same demo database:

```powershell
.\.venv\Scripts\python.exe -m app.cli --database data/hardening_final_demo.db --output output/hardening_final_demo run-case SYNTHETIC_2024 --package examples/synthetic-case/package.json
```

Review the emitted draft and resolve mandatory escalations before approval. The
approval/finalization commands are documented in README; this implementation
session generated drafts, and did not approve a real case on the user's behalf.

Run the complete test suite:

```powershell
$env:DEBUG = 'false'
$testTemp = Join-Path $env:TEMP ('credit-memo-tests-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest -q --basetemp $testTemp
```

## Deferred work and readiness

The deterministic offline runner is working and is ready for development of a
real LLM adapter. The existing real-provider class remains a stub. No live AI
dependency or external model call was added. Fake responses are deliberately
empty and are not evidence of generative-analysis accuracy. A real Delta filing
reality check, provider credentials/retries/cost controls, real AI evaluation,
production identity controls, and native OCR/PDF installation remain separate
steps. See `REAL_DATA_READINESS.md` for parser and storage limits.
