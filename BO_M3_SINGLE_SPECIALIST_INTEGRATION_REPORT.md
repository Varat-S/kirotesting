# BO M3 Single-Specialist Integration Report

**Date:** 17 October 2026  
**Branch:** `feat/bo-investigation-integration`  
**Starting commit:** `5486fce81396ebf965b0da9a7770449544491d59`  
**Implementation commits:** `cb68870` and `60cc896`  
**Scope:** M1S2 only; offline scripted execution; no paid model calls

## Decision

Keep `single_pass` as the default. Keep `hybrid_selective` experimental and do
not start the five-specialist rollout yet. The controlled runner result shows
that the integrated path is auditable and fails safely, but it does not show an
efficiency advantage or reliable superiority: hybrid selected all seven
candidates, used the same four scripted calls as full investigation, and retained
one known defective claim. Full investigation rejected all five defects in this
fixture but remains incomplete and has no measured live cost or latency.

## Baseline and environment

The working tree was clean on the requested branch at the starting SHA. Remote
references were fetched before changes. The existing suite was reproduced with
Python 3.13.7:

```text
1,339 passed, 13 skipped, 1 warning in 71.86s
```

The repository `.env` contains `DEBUG=release`, which is invalid for the current
boolean setting. Tests therefore use a process-only `DEBUG=false` override; the
file was not changed. The 13 skips cover unavailable OCR/PDF tooling, an absent
Stryker filing, fixture-state conditions, and an intentionally disabled paid
live-model test.

## Changes and architecture decisions

- `BusinessOverviewRunner.run_m1s2_mode` provides a controlled M1S2 path. Its
  default mode is `single_pass`; full and hybrid execution require an explicit
  mode.
- Each execution creates a distinct analysis run and persists its sealed packet,
  mode, roster, policy versions, packet hash, hypotheses, parameter results,
  validation audit, completeness result, usage, failure state, and Agent 6 gate.
- Hybrid first validates the broad candidates, applies ownership and versioned
  selection, investigates selected candidates only, and merges results by the
  authoritative hypothesis ID. Selection and deduplication decisions remain in
  lineage metadata.
- A failed selective investigation cannot fall back to usable broad findings.
  Selected records become `unverified`, `audit_only`, review-required records;
  the failed stage and reason remain in their trace and run audit.
- Completeness uses the mandatory case checks and the investigation's stage-12
  result. Completing selected candidates alone is insufficient. A single-pass
  result has no stage-12 proof and remains a reviewable draft in this controlled
  path.
- Agent 6 receives an empty payload unless validation is valid, completeness is
  satisfied, at least one usable finding exists, and the specialist has no
  execution failure. Qualified usable findings may pass once those conditions
  are met.
- The normal six-agent runner and scoring rules were not changed. No autonomous
  agent or second authoritative hypothesis store was added.

The principal implementation files are `app/services/bo/single_specialist.py`,
`app/services/bo/m3_runner_evaluation.py`, `app/services/bo/comparison.py`,
`app/services/bo/investigation.py`, `app/services/bo/hybrid.py`, and
`app/services/bo/runner.py`.

## Evaluation-set provenance

`examples/bo_m3/independent_evaluation/` contains 18 frozen cases: six each for
HCA Healthcare FY2025, Delta Air Lines FY2025, and NVIDIA FY2026. The committed
primary disclosures are identified by path and SHA-256 hash. The cases cover
reported facts, paraphrases, reversed negation, changed values, denominators,
wrong periods and entities, causal overreach, intention-as-outcome errors,
mitigation actions and unproven mitigants, contradictory disclosures, qualified
risks, unsupported positive conclusions, and specialist remit.

The labels were authored by the developer and frozen before this contract run.
They are **not independently reviewed ground truth**. The manifest status is
`developer_authored_awaiting_external_review`; reviewer and adjudication fields
remain empty. Stryker was not used because its primary filing is absent locally.
NVIDIA supplies a third primary-source borrower and cross-sector coverage.

## Semantic-entailment contract result

The scripted judge returns each frozen expected label. This tests deterministic
preconditions, response parsing, fail-closed behavior, and metric calculation;
actual LLM semantic accuracy is `not_measured`.

| Measure | Result |
|---|---:|
| False support | 0 / 11 |
| Correct support retention | 7 / 7 |
| Contradiction detection | 4 / 4 |
| Unsupported inference rejection | 5 / 5 |
| Incorrect numeric rejection | 3 / 3 |
| Uncertain results on well-formed frozen responses | 0 / 18 |
| Deterministic hard failures rejected | 6 / 6 |

There were no case-level mismatches because the semantic outputs are scripted
from the expected labels. Separate adversarial tests show that an evidence ID
outside the admitted packet returns `insufficient`, and that a judge which cites
an invented ID returns `uncertain` with `malformed_output=true`. Neither becomes
supported. The report therefore demonstrates the service contract, not real
model precision or recall. Remaining false positives and negatives for an actual
model are unknown.

## Runner integration and three-way result

All modes ran through `BusinessOverviewRunner` for the same M1S2 case, ownership,
source cutoff, coverage checks, and admitted evidence IDs. Packets are separately
sealed because their payloads contain distinct analysis-run and parameter IDs;
the admitted evidence set is identical in all three. The recorded responses are
the pre-registered, deliberately flawed M2 scripts.

| Result | Single pass | Full investigation | Hybrid selective |
|---|---:|---:|---:|
| Candidate records | 7 | 7 | 7 |
| Known defects held back | 2 / 5 | 5 / 5 | 4 / 5 |
| Valid controls retained | 2 / 2 | 2 / 2 | 2 / 2 |
| Scenarios with expected disposition | 4 / 7 | 7 / 7 | 6 / 7 |
| Usable findings | 5 | 2 | 3 |
| Candidates selected for investigation | 0 | n/a | 7 |
| Scripted model/stage calls | 1 | 4 | 4 |
| Request characters | 53,212 | 279,363 | 284,830 |
| Complete | no | no | no |
| Agent 6 eligible | no | no | no |

Hybrid held back the causal/contract, absence-as-safety, unproven-mitigant, and
ignored-renewal-risk defects but retained the correct-metric/irrelevant-claim
defect as a usable qualified finding. Because all candidates met the conservative
selection policy, this fixture shows no selectivity or call-count saving. These
are regression outcomes from scripted responses and cannot prove comparative
live-model performance.

All three modes were blocked by unresolved mandatory coverage checks
`hc.payer_mix` and `hc.contract_rates_vs_volumes`. The persisted hypotheses stay
available for review, including useful adverse findings, while the Agent 6
authoritative payload remains empty. A forced invalid investigation plan also
proves that the hybrid run records failure, downgrades selected claims, remains
incomplete, and does not silently reuse the broad result.

Input/output/thinking token fields in the scripted backend are zero and do not
represent measured usage. Token usage, cost, and latency are therefore reported
as `not_measured`. Character counts and call counts are direct harness
measurements only.

## Verification

Exact reproduction commands from the repository root:

```bash
DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python -m pytest -q

DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python \
  -m pytest -q tests/integration/test_bo_m3_single_specialist.py \
  tests/unit/test_bo_m3_independent_evaluation.py

DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python \
  -m app.services.bo.m3_semantic_eval \
  examples/bo_m3/independent_evaluation/cases.json
```

The focused integration and M2 regression set passed 48 tests. The new semantic
evaluation tests passed 3 tests. The final suite passed 1,348 tests with 13
documented skips and one third-party deprecation warning in 85.79 seconds.

## Remaining blockers and rollout recommendation

External review of the frozen labels is still required. Real semantic-model
accuracy, token use, monetary cost, and latency remain unmeasured. The hybrid
policy needs evaluation on independently adjudicated cases where it selects a
proper subset, and the retained irrelevant claim needs analysis before scaling.
Completeness policy also needs evidence that it covers omissions across more
borrowers and the other four specialists.

The gates for controlled one-specialist integration, persistence, failure
propagation, and Agent 6 restriction are met. The credibility and architecture
gates for a broader rollout are not. Retain the default, continue hybrid only as
an experimental explicit mode, retain full investigation as a comparison mode,
and stop before five-specialist M3 integration pending review.

**Final verification:** 1,348 passed, 13 skipped, 1 warning (85.79 seconds).  
**Final commit SHA:** recorded in the pushed branch summary; Git commits cannot
self-reference their own SHA in tracked content.
