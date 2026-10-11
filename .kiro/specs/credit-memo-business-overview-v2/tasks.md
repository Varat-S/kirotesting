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
  - [x] HCA FY2025 Form 10-K acquired and ingested (accession
        `0001193125-26-044769`; the bundle is local and untracked).
  - [x] Six specialists run live on HCA (`gemini-3.1-flash-lite`).
  - [ ] HCA manifest verified by a reviewer against the filing.
  - [ ] Full BO path (orchestrator, challenger, memo) run live on HCA.
  - [ ] Acceptance H against a reviewer-verified manifest.
- [x] **BO-M8 Verification** — full regression (acceptance J), implementation
  report `BUSINESS_OVERVIEW_V2_REPORT.md`, limitations, remaining FO/SO
  integration. Open items are those listed under BO-M7.

## Quality remediation (branch `feat/bo-quality-remediation`)

- [x] **QR-1 Prompts and schema** — specialist block + shared rules with no
  parameter names; permitted categorical values for every parameter.
- [x] **QR-2 Validation layers** — source integrity with bounded quote
  recovery; numeric binding per hypothesis; nine inferential rules; evidence
  basis and sufficiency; parameter gating.
- [x] **QR-3 Retrieval and indicators** — sector checklist, case coverage
  checks, coverage follow-up; curated per-specialist indicators.
- [x] **QR-4 BO6** — mechanism required; certainty and materiality capped by
  components; FO handoffs; BO/FO boundary.
- [x] **QR-5 Demonstration** — `run-bo-specialists`, three reports, benchmark,
  live HCA run.

Open:

- [ ] Pass `evidence_basis`, `review_flags` and `fo_handoffs` to the
  orchestrator and challenger and show them in the `run-bo-case` memo.
- [ ] A consumer for FO handoffs.
- [ ] Entailment check (quote actually states the claim) and indicator
  relevance check; both are currently outside deterministic validation.
- [ ] Decide scoring coverage now that rubric parameters are often
  `not_established`.
- [ ] More sector profiles; non-keyword retrieval.
- [ ] Switch the full agentic runner to the v2 roster.
