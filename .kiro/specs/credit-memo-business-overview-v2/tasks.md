# Business Overview v2 — Tasks

- [x] **BO-M1 Audit and specs** — branch SHA, roster/dependency audit,
  requirements, design, ownership mapping (this folder).
- [x] **BO-M2 Contracts and prompts**
  - [x] v2 roster in `agents/registry.py` (`default_registry(bo_roster="v2")`),
        compatibility map, BO6 dependency.
  - [x] BO prompts and response schemas (`prompts/bo_prompts.py`), registered
        by the BO runner under new prompt names.
  - [x] Routing selectors and retrieval plans (`bo/plans.py`).
  - [x] Registry tests (acceptance A).
- [x] **BO-M3 Hypothesis, evidence, calculation layer**
  - [x] `schemas/bo.py`, ORM tables, migration report.
  - [x] `bo/retrieval.py` (plan + follow-up, attempts, gaps, budgets).
  - [x] `bo/indicators.py` (metric adapter + parameter engine + traces).
  - [x] `bo/hypotheses.py` (validator, store).
  - [x] `bo/reconcile.py` (duplicates, contradictions).
  - [x] Tests (acceptance C, D, E).
- [x] **BO-M4 Business Orchestrator** — input contract extension, execution,
  output validation, persisted conclusion (acceptance F).
- [x] **BO-M5 Challenger and rerun** — execution, outcome derivation, targeted
  rerun, supersession, escalation (acceptance G).
- [x] **BO-M6 Reporting** — memo, audit, handoff, CLI, workbench (acceptance B, I).
- [ ] **BO-M7 HCA pilot**
  - [x] Evaluation harness and manifest format (`bo/evaluation.py`).
  - [x] HCA package and manifest TEMPLATE (unverified, no figures).
  - [x] Real-filing run on the secondary borrower (Stryker FY2024), offline
        simulation.
  - [ ] **HCA FY2025 Form 10-K acquired and ingested — NOT DONE** (download
        declined in the implementing session).
  - [ ] HCA manifest verified by a reviewer against the filing.
  - [ ] HCA BO run and acceptance H.
  - [ ] Live Vertex run (optional; no credentials configured).
- [x] **BO-M8 Verification** — full regression (acceptance J), implementation
  report `BUSINESS_OVERVIEW_V2_REPORT.md`, limitations, remaining FO/SO
  integration. Open items are those listed under BO-M7.
