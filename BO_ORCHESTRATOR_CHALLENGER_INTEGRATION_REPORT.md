# BO Orchestrator + Challenger Integration Report

Date: 2026-10-19 (Asia/Singapore)

Branch: `feat/bo-orchestrator-challenger-integration`

Source run: `ar_fb0bba6709e94a14`
Source SHA-256: `2b491c30e630f98fe0c12b81dcd8218bfcd3d3c8b38b9f3c4074e6b99211dbb5`

## 1. Outcome

The genuine recorded HCA R5 specialists were converted into a validated cross-module synthesis, independently challenged, deterministically rescored, and emitted as a schema-valid `bo-handoff-1.2` packet. The source specialist artifact was not changed and none of the nine specialists was replayed.

The resulting BO is **human-review-required**, not final. Human review remains `pending`. The reporting company was not promoted into a proposed transaction borrower; the transaction obligor remains unknown. The illustrative BusinessRiskScore is **provisional band 3**, not an authoritative rating.

BO is technically integrated but is **not ready to be accepted by SO** until the unresolved BO qualifications receive real human review and transaction-specific borrower/cash-access evidence is supplied. The existing SO adapter correctly blocks a `human_review_required` conclusion.

## 2. Architecture and components reused

The implementation adds an import/completion bridge, not a second general BO orchestrator. It reuses:

- `HypothesisRecord`, `AcceptedBusinessOverview`, and the existing handoff 1.2 contract.
- `reconcile()` for duplicates, contradictions, overlapping evidence, and counter-evidence.
- `validate_orchestrator_output()` and `validate_challenger_output()` for deterministic semantic/provenance controls.
- The existing BO orchestrator and independent challenger prompts and schemas.
- `validate_m3_assessment()` (Validator 7) for offline governance of the recorded M3 proposal.
- `rubric_signals()`, `ScoringEngine`, and the versioned illustrative scoring configuration.
- `handoff_content_hash()` for tamper-evident downstream handoff integrity.
- Existing Vertex provider/model-tier routing for live execution.

The audit found that the full BO runner already executes specialists, synthesis, challenge, bounded reruns, scoring, reporting, and approval gating. The missing connection was an entry point that could consume an already completed specialists-only artifact without rerunning all specialist calls.

## 3. Code changes

- Added `app/services/bo/completion.py`:
  - immutable fixture loading and source SHA-256 provenance;
  - historical count and evidence-ID reconciliation;
  - governed M1-M4 module construction;
  - recorded M3 Validator 7 replay with no assumed transaction borrower;
  - synthesis and challenger execution using existing contracts;
  - bounded deterministic correction of claim metadata/provenance only;
  - at-most-two targeted-remediation planning;
  - post-challenge claim state, scoring, handoff generation, and stable hashing;
  - concise JSON/Markdown artifact generation.
- Added `scripts/complete_bo_recorded.py` with explicit `offline`, `replay`, and `live` modes.
- Strengthened the existing challenger prompt to explicitly test obligor identity, group-versus-obligor cash access, management/governance overstatement, missing evidence, and unproven mitigants.
- Added 13 R5 acceptance tests in `tests/integration/test_bo_recorded_completion.py`.

## 4. Execution flow

1. Read and hash the recorded specialists artifact.
2. Validate every recorded hypothesis and its evidence IDs; reconcile reported metrics from actual contents.
3. Rebuild governed M1-M4 module views and replay recorded M3 through current Validator 7.
4. Compute the preliminary illustrative score from accepted governed parameters.
5. Run one cross-module synthesis call.
6. Apply only deterministic metadata/provenance canonicalization; claim text is never rewritten.
7. Run one independent challenge call over the validated synthesis.
8. Plan at most one remediation round and at most two direct specialist calls. No remediation was required by the live challenger because both findings were medium severity and not reanalysis requests.
9. Qualify challenged claims, recompute the post-challenge score, validate handoff 1.2, and hash it.

## 5. Verified R5 source summary

The implementation recomputed these values from the artifact and compared them with its reported metrics:

| Measure | Verified value |
|---|---:|
| Validated specialists | 9 |
| Historical specialist calls | 13 |
| Recorded hypotheses | 28 |
| Usable hypotheses | 26 |
| Accepted parameters | 19 |
| Parameters held back for company-specific evidence | 3 |
| Coverage checks addressed | 9 / 10 |
| Historical tokens | 256,359 |

M3 Validator 7 produced 11 governance issues and downgraded the proposed-borrower identification to `unavailable`. Audit-only `M3-H005` is retained for audit but excluded from authoritative findings.

## 6. Offline acceptance results

- New recorded-completion acceptance: **13 passed**.
- Targeted BO, validator, SO adapter, and credit-memo regression: **90 passed, 2 skipped**. Both skips require historical local handoff fixtures not present at their hard-coded paths.
- Full repository suite: **1,450 passed, 16 skipped, 0 failed** in 99.90 seconds.
- The initial targeted invocation failed before test execution because local `.env` contains `DEBUG=release`, which is not a valid boolean. Tests passed with a process-local `DEBUG=false`; `.env` was not modified or committed.
- Existing skips cover unavailable OCR, external filing fixtures, live opt-in, a historical HCA handoff, an offline PDF backend, and (before installation) the optional Vertex SDK.

Acceptance coverage proves:

- invalid or absent evidence fails import;
- audit-only/high-confidence findings cannot be promoted by synthesis;
- contradictions remain explicit;
- material challenges select only direct affected specialists and never exceed two calls;
- unresolved challenges survive into the handoff;
- rejected/audit-only M3 content cannot enter authoritative output;
- the transaction borrower remains unknown;
- identical recorded model outputs reproduce identical validation, scoring, and handoff hashes.

## 7. Live R5 results

The successful packet contains exactly two incremental live calls:

| Stage | Model | Input tokens | Output tokens | Total | Latency |
|---|---|---:|---:|---:|---:|
| Synthesis | `gemini-3.1-flash-lite` | 41,642 | 2,151 | 43,793 | 6,601 ms |
| Challenge | `gemini-3.1-flash-lite` | 21,083 | 524 | 21,607 | 2,765 ms |
| **Successful packet** |  | **62,725** | **2,675** | **65,400** | **9,366 ms** |

Pricing was not configured, so cost is unavailable.

Across the whole implementation session, **four paid provider calls** were made: three synthesis attempts and one challenge. The first two synthesis outputs were deterministically rejected before challenge (first: invented/rounded numeric provenance and overstated fact type; second: promotion of audit-only M3 material). Their token usage was not persisted by the pre-fix failure path, so total paid usage can only be reported as **at least 65,400 tokens**. No claim is made that the successful-packet usage represents those rejected attempts.

The final successful synthesis required four deterministic metadata adjustments: three evidence-ID projections from its cited governed hypotheses and one downgrade from `fact` to `inference`; its claim text was unchanged.

The challenger produced two medium findings:

1. `OA-001` overstated demand resilience relative to qualified evidence.
2. `ST-001` treated a management belief about centralized operational efficiency as fact.

Both claims were changed from `established_or_qualified` to `qualified` in the post-challenge record. No specialist rerun was requested or executed.

## 8. Post-challenge governance and unresolved issues

| Item | Result |
|---|---|
| Conclusion status | `human_review_required` |
| Human review | `pending` |
| Score | provisional band 3 (illustrative, not a rating) |
| Score limitation | `customer_hhi` unavailable; 70% weighted coverage |
| Transaction obligor | unknown |
| Targeted remediation | 0 rounds / 0 calls |
| Unresolved challenges | 2 medium findings retained |
| Module limitation | M3 incomplete because one operating-entities coverage check remains unresolved |
| Evidence gaps | comparative market share/ranking; supplier concentration/key vendors; transaction borrower/cash access |

The handoff is contract-valid and tamper-evident. It is not approved, does not contain a final credit decision, and does not claim a facility borrower or authoritative rating.

## 9. Artifacts

Live:

- `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_live/bo_completion_packet.json`
- `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_live/handoff_v1_2.json`
- `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_live/bo_completion_report.md`
- Packet hash: `553a0e76888d15ba4f878f081beafd914add96bb18180011f9295cba37f85226`
- Handoff content hash: `b69ac91826ad8399cf15846e2badce010d4aa6d30f6e1b9e3815ce188c01d3f8`

Offline contract run:

- `output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_offline/`

## 10. Reproduction

```bash
DEBUG=false .venv/bin/pytest -q tests/integration/test_bo_recorded_completion.py

DEBUG=false .venv/bin/python -m scripts.complete_bo_recorded \
  --source output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/specialists_run.json \
  --mode offline \
  --output-dir output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_offline

DEBUG=false .venv/bin/python -m scripts.complete_bo_recorded \
  --source output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/specialists_run.json \
  --mode replay \
  --replay output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_live/bo_completion_packet.json \
  --output-dir output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_replay

# Paid; requires configured Vertex credentials and the `vertex` optional dependency.
DEBUG=false .venv/bin/python -m scripts.complete_bo_recorded \
  --source output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/ar_fb0bba6709e94a14/specialists_run.json \
  --mode live \
  --output-dir output/bo/HCA_M3_REFINEMENT_LIVE_20261018_R5/bo_completion_live

DEBUG=false .venv/bin/pytest -q
```
