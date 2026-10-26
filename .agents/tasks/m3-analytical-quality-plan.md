# Module 3 — Analytical Quality, Validator 7 & Real-Model Readiness — Implementation Plan

Targeted enhancement (not a rebuild) of the EXISTING standalone Module 3 specialist
`m3_corporate_management` and Validator 7. All work happens inside the worktree at
`.worktrees/m3-analytical-quality` on branch `feat/m3-analytical-quality`.

## Investigation summary — what exists today

- **Conclusion assembly (Priority 1).** `app/services/bo/m3_output.py::_section_c`
  builds `integrated_commentary` as a generic two/three-bullet list: a structure-
  availability sentence (entity/link/guarantor counts) plus a sentence listing the
  `credit_direction` values seen in management signals. It never reads the material
  **accepted structural credit findings** (`ai_structural_credit_findings`) or the
  content of management signals. Confirmed by the committed demos: `fictional_mixed`
  has two material qualified findings (`SCF-MIX-POS` guarantor support, `SCF-MIX-NEG`
  cash restriction) yet its `consolidated_conclusion.integrated_commentary` says only
  "A validated corporate-structure diagram is available (5 entities…)" and "observed
  credit directions: neutral". The five scenarios' commentaries are materially
  indistinguishable. `app/services/bo/m3_artifacts.py::build_m3_analysis_artifact`
  already wraps section C with `admitted_*_ids` trace lists and already holds the
  `_is_admitted` gate — so the admitted-finding set and traceability plumbing exist;
  only the prose is generic.
- **`_is_admitted` lives in `m3_artifacts.py`** (not `m3_validation.py` as the brief
  hinted): excludes `rejected`, `insufficient_evidence`, and material-level
  (`high`/`medium`) `unverified`. Reuse it verbatim for the conclusion.
- **Validator 7 numeric gate (Priority 2).** `app/services/bo/m3_validation.py::`
  `_deterministic_claim_checks` tokenises numbers (`_NUMBER_RE`) and fiscal periods
  (`_PERIOD_RE`) from a claim and from the concatenated cited-evidence corpus. Its
  defect: a claimed token **absent** from the corpus is emitted with
  `contradiction=True`, and `_govern_finding` then maps any `contradiction` failure to
  disposition `rejected`. Absence is thus treated as contradiction. It also matches a
  bare number without checking metric identity, entity, period, accounting basis or
  units; and there is no use of canonical financial data. It never calls a model.
- **Canonical financial-data infrastructure to reuse: `app/services/metrics/`**
  (`definitions.py` = versioned `ResolvedMetricDefinition` registry with
  `metric_definition_id`/`version`/`components` incl. `units`, `inputs`,
  `included`/`excluded`; `engine.py`; `trends.py`). Deterministic results reach a
  specialist as `app.schemas.agentic.ParameterResult` (fields: `value`, `value_type`,
  `method`, `evidence_ids`, `formula_id`/`formula_version`, `materiality`,
  `source_fact_ids`) carried on `prep.indicators` and built by `build_indicators(
  evidence, metrics, …)` in `prepare_specialist`. **Do NOT build a competing financial
  engine** — read canonical metric values + their definition (units/period/basis) from
  these.
- **Standalone + live path (Priority 3).** `app/services/bo/m3_standalone.py::run_m3`
  builds + validates the corporate structure, injects `build_structural_facts(...)`
  into `prep.structural_facts` **before** the single pass (ordering already correct),
  runs `run_single_pass` with a **scripted** `FakeLLMBackend` (never a provider), then
  Validator 7, then assembles outputs + the manifest. `run_single_pass` in
  `app/services/bo/comparison.py` **already accepts a `backend=` kwarg** for a live
  provider (`if backend is None: FakeLLMBackend()`), and `_single_pass_inputs` already
  injects `prep.structural_facts` under `corporate_structure`. The CLI (`app/cli.py::`
  `_run_m3`) **hard-blocks `--live`** with a `ValueError`; `run_m3` has no live
  parameter at all. So the live capability is genuinely not wired.
- **LLM provider infra to reuse:** `app/services/llm/client.py` (`LLMClient`,
  `LLMBackend`, `FakeLLMBackend`, `LLMValidationError`, schema-validates every response
  before use), `providers.py`, `providers_vertex.py` (Vertex/Gemini behind the
  abstraction, ADC/service-account creds, never committed), `sampling.py`,
  `app/schemas/llm.py`. Live transcript precedent: `app/services/bo/m1s2_live.py`.
  Settings/secrets use Pydantic `SecretStr` (masked in logs). Fake backend tests:
  `tests/unit/test_llm_client.py`.
- **Schema.** `app/schemas/bo_m3.py` is versioned and additive: `M3_ASSESSMENT_VERSION`
  = `bo-m3-corporate-assessment-1.1`, `M3_VALIDATOR_VERSION` = `validator-7-1.1.0`,
  with 1.0/1.1 both accepted (`M3_ASSESSMENT_VERSIONS`, `M3_VALIDATOR_VERSIONS`).
  `StructuralCreditFinding` and `ManagementObservation` already carry `credit_direction`
  (`Direction`), `credibility_vs_direction_note`, `assessment_availability`,
  `qualifications`, `missing_information`, `analytical_dimension`, `referenced_node_ids`/
  `referenced_link_ids`. Enums reused from `app/schemas/bo.py`
  (`Disposition`, `Direction`, `Admissibility`, `Confidence`) and
  `app/schemas/agentic.py` (`Materiality`). **No competing enums.**
- **Demos.** `examples/bo_m3/structural_credit/generate_demo.py` drives five cases
  (`hca_simulated`, `fictional_positive`, `fictional_negative`, `fictional_mixed`,
  `fictional_unsupported_guarantee`) through `run_m3` + `m3_demo.write_m3_demo` (six
  files each) into `demo_output/<case>/`. Committed scripted `*_single_pass.json`
  fixtures + `EXPECTED_LABELS`. `m3_demo.py` writes byte-stable JSON via
  `canonical_json`. HCA is simulated (`examples/bo_m3/Simplified HCA Corp Structure
  .json`, 14 entities / 13 links / 5 guarantors).
- **Tests.** `tests/unit/test_bo_m3_*` and `tests/integration/test_bo_m3_*`
  (`test_bo_m3_standalone.py`, `test_bo_m3_structural_credit.py`,
  `test_bo_m3_senior_management.py`, `test_bo_m3_structure_context.py`,
  `test_llm_client.py`). Helpers: `tests/bo_helpers.py` (`h.CASE`, `h.metrics()`,
  `h.evidence_snapshot([passages])`). `conftest.py` forbids live SEC network and
  disables `.env`. **Baseline on `feat/m3-structural-credit`: 1426 passed / 131 skipped
  / 12 errors; the 12 errors are a KNOWN pre-existing env issue (missing untracked
  `output/specialists_run.json`), NOT regressions.** Do not chase them.

## Design decisions (made here; one-line rationale each)

1. **Priority 1 fix lives in `m3_output.py::_section_c`** (the brief names it), reading
   the admitted findings via the same `_is_admitted` predicate `m3_artifacts.py` uses.
   Rationale: section C is the single source of `integrated_commentary`; `m3_artifacts`
   already spreads `**section_c` and adds the `admitted_*_ids` trace, so enriching C
   automatically flows into Deliverable B with traceability intact.
2. **Deterministic, schema-driven conclusion — no LLM call** (explicit brief
   constraint). A per-finding sentence builder keyed on `analytical_dimension` +
   `credit_direction` composes: core observation → mechanism → materiality →
   evidence refs → qualifications → gaps → why-it-matters-to-a-lender, emitting
   structured statement objects each carrying the `finding_id`/`signal_id`/
   `observation_id` they rest on (direct trace). Rationale: meets the "explain, don't
   list IDs/counts" and "direct trace" requirements without adding model nondeterminism.
3. **Mixed = preserve both; never net to a score.** Positive and negative statements
   are emitted side by side with an explicit "potentially offsetting, not netted"
   framing. Rationale: brief forbids arbitrary netting / numeric management scores.
4. **Simulation distinction preserved in prose.** When `structure.is_simulated`, every
   structure-derived statement is prefixed/qualified as hypothetical and must not assert
   real HCA 10-K debts/guarantees/restrictions. Rationale: explicit simulation rule.
5. **Priority 2: four-state verification reusing existing `Disposition`.** Map
   VERIFIED→`supported`/`qualified` (unchanged), UNSUPPORTED/UNVERIFIED→`unverified`,
   CONTRADICTED→`rejected`, REQUIRES_HUMAN_REVIEW→`unverified` **plus** a recorded gap
   + `requires_human_review` reason (no new enum). Rationale: brief says reuse the
   existing disposition classifications and not create competing enums; the four states
   map cleanly onto existing dispositions + validation-issue actions.
6. **Absence ≠ contradiction.** `_deterministic_claim_checks` stops emitting
   `contradiction=True` for a merely-absent token; absence yields a non-contradiction
   failure (→ `unverified`). Only a cited passage asserting a *different* value for the
   *same* metric/entity/period/basis yields `contradiction=True` (→ `rejected`).
   Rationale: the central Priority-2 correctness fix.
7. **Canonical-data checks gated on availability.** When a claim resolves to a canonical
   metric (via `prep.indicators`/`ParameterResult` + its `ResolvedMetricDefinition`),
   verify metric identity, entity, period, basis, currency/units, value, and
   ratio/growth/pp-vs-% distinctions within rounding tolerance; when canonical data is
   absent, fall back to the corrected textual gate (never fabricate). Rationale: brief's
   nine financial checks, reusing `app/services/metrics`.
8. **Causal claims are never deterministically "proven".** A finding whose mechanism is
   causal/legal (heuristic: contains causal/legal markers) is capped at
   REQUIRES_HUMAN_REVIEW even when numbers agree. Rationale: "financial agreement does
   not establish management's causal explanation."
9. **Priority 3: add an opt-in `live_backend`/`live_config` parameter to `run_m3`** and
   wire the CLI `--live` to construct the configured provider backend, passing it to
   `run_single_pass(..., backend=...)`. Disabled by default; offline path untouched.
   Rationale: reuse the existing `backend=` seam; smallest coherent change, no new
   framework. Capability is implemented but **no paid call is made in this task**; if
   creds/provider are unavailable the blocker is reported, never a simulated success.
10. **Schema versioning: additive bump to 1.2 only if new fields are required.** The
    conclusion uses existing fields, so Priority 1 needs no schema change. Priority 2's
    four-state result is recorded in existing `validation_reasons`/`validation_issues`;
    if a structured `verification_state` field is added to findings it is OPTIONAL with a
    safe default and the version becomes `bo-m3-corporate-assessment-1.2` /
    `validator-7-1.2.0`, with 1.0/1.1/1.2 all accepted. Rationale: additive + backward
    compatible per brief; prefer no bump unless a field is genuinely needed.

## Preserve (regression guard for every step)

Corporate ingestion, deterministic transform, structural observations, structural
credit findings, senior-management assessment, Validator 7, standalone execution, BOTH
JSON deliverables (Output A = de-wrapped v2.0 presentation artifact with NO free-form
credit commentary; Output B = Section A/B/C), offline demos, stable finding ids. The
conclusion must NOT become a lending recommendation; keep `requires_human_review: true`
and `forced_outcome: false`. No downstream/cross-module agents, no synthesizer/
challenger/scoring/renderer. No paid LLM calls. Commit only on `feat/m3-analytical-
quality`; no push/PR.

---

# Implementation Plan

- [ ] 1. Baseline the pre-change test state so regressions are attributable.
      Run the M3 suite and record counts; confirm the 12 known errors are the
      `output/specialists_run.json` env issue, not M3 logic.
      Files: none (measurement only).
      Verify: `.venv/bin/python -m pytest -q tests/unit/test_bo_m3_*.py tests/integration/test_bo_m3_*.py tests/unit/test_llm_client.py` — record pass/skip/error counts as the baseline.

- [ ] 2. (Priority 1) Add a deterministic per-finding conclusion builder in
      `app/services/bo/m3_output.py`. Introduce a private helper
      `_finding_statement(finding, *, pillar, is_simulated) -> dict` that composes one
      structured statement from an ADMITTED finding: `core_observation`
      (`structural_observation`/`credit_hypothesis` or management `hypothesis`),
      `credit_direction`, `credit_risk_mechanism` (`causal_mechanism`/`credit_mechanism`),
      `materiality`, `supporting_evidence_ids`, `qualifications`, `missing_information`,
      a `why_it_matters_to_a_lender` sentence keyed on `analytical_dimension` +
      `credit_direction` (guarantor_coverage+positive → recourse improvement subject to
      enforceability/limits; cash_flow_access+negative → reduced flexibility/constrained
      repayment; mixed → both preserved, "potentially offsetting, not netted"), and the
      source id (`finding_id`/`observation_id`/`signal_id`) for trace. Reuse the
      `_is_admitted` predicate by importing it from `app/services/bo/m3_artifacts.py`
      (single source of truth) rather than duplicating it.
      Files: app/services/bo/m3_output.py
      Verify: step 10 runs the new `_section_c` tests; this step compiles via
      `.venv/bin/python -c "import app.services.bo.m3_output"`.

- [ ] 3. (Priority 1) Rewrite `_section_c` in `app/services/bo/m3_output.py` to build a
      MEANINGFUL `integrated_commentary`. Gather admitted Pillar-A findings
      (`section_a["ai_structural_credit_findings"]` filtered by `_is_admitted`) and
      admitted material structural signals, and admitted Pillar-B management/governance
      signals; emit one statement per admitted material finding via
      `_finding_statement`. Keep a short human-readable `summary` line per pillar that
      references WHY (not ID lists/counts), and when a pillar has no admitted material
      finding say so explicitly (insufficient-evidence → state what can/cannot be
      established). Prefix simulated-structure statements as hypothetical; never assert
      real debts/guarantees from the simulated hierarchy. Keep returning
      `requires_human_review: True`, `forced_outcome: False`, and the existing
      `completion_status`/`title`/`note` keys so `m3_artifacts` and existing consumers
      stay compatible. Keep output deterministic (stable ordering by
      pillar then finding_id) for byte-stable re-assembly.
      Files: app/services/bo/m3_output.py
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_standalone.py tests/integration/test_bo_m3_structural_credit.py` — existing assertions still pass (adjust only assertions that asserted the OLD generic text, documenting the change).

- [ ] 4. (Priority 1) Confirm `m3_artifacts.py` traceability still holds and extend only
      if needed. The `admitted_*_ids` lists must equal the set of source ids present in
      the new statements; if the enriched statements change shape, ensure
      `consolidated_conclusion` still spreads `**section_c` cleanly and the
      `excluded_from_conclusion_ids` audit list still captures every non-admitted item.
      No behavioural change to `_is_admitted`.
      Files: app/services/bo/m3_artifacts.py (only if a shape mismatch is found)
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_standalone.py` — Deliverable B assertions pass; each statement's source id appears in the matching `admitted_*_ids`.

- [ ] 5. (Priority 2) Fix absence-vs-contradiction in
      `app/services/bo/m3_validation.py::_deterministic_claim_checks`. A claimed
      number/period that is merely ABSENT from the cited corpus must produce a failed
      check with `contradiction=False` (→ caller downgrades to `unverified`). Reserve
      `contradiction=True` for a cited passage that asserts a DIFFERENT value for the
      same token class. Update `_govern_finding`'s mapping so `contradiction` →
      `rejected`, non-contradiction failure → `unverified`, and a reason string
      distinguishes "unsupported (absent)" from "contradicted (conflicting value)".
      Files: app/services/bo/m3_validation.py
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_structural_credit.py` plus the new Group-B tests from step 11.

- [ ] 6. (Priority 2) Add canonical-financial verification to
      `app/services/bo/m3_validation.py`. Add a helper that, given the admitted canonical
      metrics (passed in as a resolved map derived from `prep.indicators`/
      `ParameterResult` + their `ResolvedMetricDefinition` from
      `app/services/metrics/definitions.py`), checks a numeric claim for: metric
      identity, reporting entity, fiscal period, accounting basis, currency+units,
      numerical value, percentage/ratio/growth computation, rounding/tolerance, and
      whether the cited evidence supports the asserted RELATIONSHIP. Guard the
      pp-vs-%, dollars-vs-millions/billions, quarterly-vs-annual, consolidated-vs-
      subsidiary, and revenue-growth-vs-EBITDA-growth / margin%-vs-%-change distinctions.
      When no canonical datum resolves for the claim, skip these checks (fall back to the
      corrected textual gate) — never fabricate. Thread a new OPTIONAL
      `canonical_metrics` parameter through `validate_m3_assessment` and `_govern_finding`
      with a safe default of `None` so existing callers are unaffected.
      Files: app/services/bo/m3_validation.py
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_structural_credit.py` and new Group-B tests (step 11).

- [ ] 7. (Priority 2) Cap causal/legal claims at REQUIRES_HUMAN_REVIEW in
      `app/services/bo/m3_validation.py`. When a finding's mechanism is causal or legally
      significant (reuse existing markers; keep the detection conservative and
      documented), numeric agreement must NOT promote it to `supported`; record a
      `requires_human_review` reason and leave/downgrade to `unverified` (or `qualified`
      where already supported with explicit qualification). Preserve the existing
      `credit_direction` vs `credibility_vs_direction_note` separation for
      `management_assessments` (do not regress `_govern_finding`'s credibility gate).
      Files: app/services/bo/m3_validation.py
      Verify: new Group-B causal-attribution test (step 11) and `.venv/bin/python -m pytest -q tests/unit/test_bo_m3_senior_management.py`.

- [ ] 8. (Priority 2, optional schema) If steps 6–7 need to record a structured
      verification state on a finding, add an OPTIONAL `verification_state` field
      (Literal reusing the four concepts, default mapping to current behaviour) to
      `StructuralCreditFinding`/`ManagementObservation` in `app/schemas/bo_m3.py`,
      and bump `M3_ASSESSMENT_VERSION`→`bo-m3-corporate-assessment-1.2`,
      `M3_VALIDATOR_VERSION`→`validator-7-1.2.0`, appending both to
      `M3_ASSESSMENT_VERSIONS`/`M3_VALIDATOR_VERSIONS` and the mirror `Literal`s in
      `M3CorporateAssessment` so 1.0/1.1 proposals still validate. Also mirror the
      optional field in `_M3_FINDING`/management item schema in
      `app/prompts/bo_prompts.py` only if the specialist may emit it. SKIP this step
      entirely if the four states are adequately carried in `validation_reasons`/
      `validation_issues` (preferred — no schema change).
      Files: app/schemas/bo_m3.py, app/prompts/bo_prompts.py (only if a field is added)
      Verify: `.venv/bin/python -m pytest -q tests/unit/test_bo_m3_services.py tests/integration/test_bo_m3_structural_credit.py` — 1.0/1.1 fixtures still validate; version strings resolve.

- [ ] 9. (Priority 3) Wire canonical financial data + the live backend into
      `app/services/bo/m3_standalone.py::run_m3`. (a) Build the `canonical_metrics` map
      from `prep.indicators` and pass it to `validate_m3_assessment` (benefits offline
      AND live). (b) Add OPTIONAL `live_backend=None` and `live_config=None` parameters;
      when `live_backend` is set, call `run_single_pass(session, prep, backend=
      live_backend)` instead of the scripted `m3_responses["single_pass"]`, set
      `execution_mode="live"`, and record model id + prompt version + source-snapshot
      metadata in the manifest. Enforce safeguards: bounded to one M3 specialist
      execution (retries only if explicitly configured and bounded); honour configured
      timeout + token limits via the provider config; `model_called` is derived from the
      actual (non-fake) call; on live failure FAIL SAFELY (partially_failed, no
      fabricated findings) with **NO silent fallback to scripted**; the manifest's
      `execution.model_called` and `execution_mode` MUST state whether a real model ran.
      Never log/persist credentials (rely on `SecretStr`). Do NOT invoke any other
      module/agent (assert via the existing manifest `cross_module_or_downstream_invoked:
      False`).
      Files: app/services/bo/m3_standalone.py
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_standalone.py` and the new Group-C tests (step 12).

- [ ] 10. (Priority 3) Enable the opt-in `--live` path in `app/cli.py::_run_m3`. Replace
      the hard `ValueError` with: when `--live` is set, construct the configured provider
      backend through the existing provider abstraction (`app/services/llm/providers*`),
      read model id/timeout/token limits/creds from Settings (`SecretStr`), pass it as
      `run_m3(..., live_backend=backend, live_config=...)`, and keep `--responses`
      required unless `--live`. If no provider/credentials are configured, raise a clear,
      credential-free error REPORTING the blocker (do not invent a run). Default remains
      offline. No paid call is executed in this task.
      Files: app/cli.py
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_standalone.py tests/unit/test_llm_client.py`; manually confirm `run-m3 --live` with no creds reports a clear blocker (no network, documented in the step report).

- [ ] 11. (Tests Groups A, B, D, E) Add/extend offline tests (no paid calls).
      Group A — conclusion differentiation: in
      `tests/integration/test_bo_m3_structural_credit.py`, assert the five scenarios'
      `consolidated_conclusion.integrated_commentary` are materially DISTINCT (not just
      finding-id lists), that positive/negative/mixed/insufficient prose contains the
      expected mechanism language, that mixed preserves BOTH findings without netting,
      that every statement's source id appears in the matching `admitted_*_ids`, and that
      re-assembling identical governed findings is byte-identical.
      Group B — numeric validation: in a new `tests/unit/test_bo_m3_validation_numeric.py`,
      cover UNSUPPORTED-vs-CONTRADICTED, period mismatch, currency/units, pp-vs-%,
      consolidated-vs-subsidiary, revenue-vs-EBITDA growth, unsupported causal
      attribution capped at human-review, rounding/tolerance, and missing-canonical-data
      fallback.
      Group D — senior-management regression: extend `tests/unit/test_bo_m3_senior_
      management.py` to confirm the credibility-vs-direction separation and management
      proxy/ICFR gates are unchanged.
      Group E — structural integrity: confirm HCA 14 entities / 13 links / 5 guarantors /
      accurate percentages / provenance / simulation handling / deterministic output
      still hold (extend `tests/integration/test_bo_m3_standalone.py` /
      `test_bo_m3_corporate_structure_integration.py`).
      Files: tests/integration/test_bo_m3_structural_credit.py, tests/unit/test_bo_m3_validation_numeric.py (new), tests/unit/test_bo_m3_senior_management.py, tests/integration/test_bo_m3_standalone.py
      Verify: `.venv/bin/python -m pytest -q tests/unit/test_bo_m3_validation_numeric.py tests/integration/test_bo_m3_structural_credit.py tests/unit/test_bo_m3_senior_management.py` — all pass.

- [ ] 12. (Tests Group C) Real-model readiness using the repo's FAKE/MOCK backend only.
      In a new `tests/integration/test_bo_m3_live_readiness.py` (offline), assert:
      offline scripted path still works; live cannot activate accidentally (default
      stays offline); invalid/missing creds fail safe with a clear blocker and NO
      fabricated output; passing a `FakeLLMBackend` as `live_backend` exercises the live
      code path with `execution_mode="live"` and `model_called` set from the call;
      ONLY `m3_corporate_management` is invoked (one analysis run / one packet; forbidden
      audit events absent); admitted evidence AND `structural_facts` reach the specialist
      (`execution.structure_in_specialist_context` True); an invalid/schema-failing
      response fails safe (`LLMValidationError` / partially_failed, no promoted findings);
      no downstream/cross-module agent runs (`cross_module_or_downstream_invoked: False`);
      the manifest identifies sim vs real; and token/timeout/retry safeguards are
      honoured (bounded to one execution unless bounded retries configured).
      Files: tests/integration/test_bo_m3_live_readiness.py (new)
      Verify: `.venv/bin/python -m pytest -q tests/integration/test_bo_m3_live_readiness.py` — all pass; confirm no network via conftest guards.

- [ ] 13. Regenerate the committed demos and confirm byte-stability + differentiation.
      Run the offline demo generator; confirm each case still writes six files, the HCA
      counts (14/13/5) are unchanged, and the five `m3_analysis.json`
      `consolidated_conclusion` blocks are now materially differentiated and trace to
      admitted findings. Re-run once more to confirm byte-identical output.
      Files: examples/bo_m3/structural_credit/demo_output/** (regenerated artifacts only)
      Verify: `DEBUG=false .venv/bin/python examples/bo_m3/structural_credit/generate_demo.py` twice; `git diff --stat` stable on the second run; inspect two scenarios' `consolidated_conclusion.integrated_commentary` to confirm they differ.

- [ ] 14. Full offline regression. Run the complete suite; confirm no NEW failures beyond
      the 12 known pre-existing `specialists_run.json` env errors recorded in step 1.
      Files: none (measurement only).
      Verify: `.venv/bin/python -m pytest -q` — result is ≥ baseline passed, skips
      unchanged, and the only errors are the 12 known env errors from step 1.

## Notes / assumptions

- The brief placed `_is_admitted` in `m3_validation.py`; it actually lives in
  `m3_artifacts.py`. The plan imports it from there (single source of truth) instead of
  duplicating logic.
- Schema bump (step 8) is conditional and preferred-skipped; the four verification
  states map onto existing dispositions + validation issues, satisfying "reuse existing
  enums, no competing enums."
- No paid/live model call is executed. Step 10's live wiring is verified only against the
  fake backend and a credential-free failure path; if a provider/creds are unavailable at
  implementation time, that is reported as a blocker, never simulated as success.
- Software correctness here is NOT a claim of credit-analytical correctness; every
  finding and the consolidated conclusion remain drafts requiring human review.
