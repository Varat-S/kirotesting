# BO M3 readiness report

Date: 17 October 2026

Branch: `feat/bo-investigation-integration`

Baseline commit: `d2e4b6814a9c90f19e0cf125477bdd85f1a1013b`
Control implementation commit: `e717347`

## Decision

**M3 integration is not ready.** The offline controls and execution plumbing are
strong enough to continue evaluation, but the evidence does not yet justify
making hybrid or full investigation the default across all five Module 1
specialists. The current evaluation data is developer-authored, the semantic
judge has no independent validation set, the hybrid path has not been exercised
through `BusinessOverviewRunner`, and live hybrid cost/latency is unmeasured.

The recommended default remains the current single-pass path. Hybrid selective
investigation is the preferred candidate for the next controlled trial because
its explicit policy investigates weak or contradicted material claims rather
than selecting only convincing findings. This is a trial recommendation, not a
claim that hybrid is superior.

No paid model calls were made in this cycle.

## Repository and baseline integrity

After `git fetch --prune origin`, local HEAD and
`origin/feat/bo-investigation-integration` both resolved to `d2e4b68`. The
worktree was clean and there were no local-only M3 commits.

Environment:

| Item | Result |
|---|---|
| Python | 3.13.7 (`/Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python`) |
| pytest | 9.1.1 |
| SQLAlchemy | 2.1.3 |
| Pydantic | 2.13.5 |
| Dependency source | Existing sibling-worktree virtual environment |

The first run produced 71 failures, 2 errors, 1,259 passes and 13 skips. Every
failure/error traced to the local `.env` value `DEBUG=release`, which cannot be
parsed as the boolean `Settings.debug`. The file was preserved. Re-running with
the process-only override `DEBUG=false` produced:

> **1,332 passed, 13 skipped, zero failed, zero errors in 72.50 seconds**

The historical report's 1,323 passes is stale: the current branch collects
1,345 tests. The 13 skips are explained by unavailable OCR for image-only Delta
fixtures (8), missing local Stryker filing data (1), HCA manifest state guards
(2), the explicitly live/provider-cost BO test (1), and the optional HTML-to-PDF
backend (1). The committed HCA filing itself was present. The M2 comparison tests
passed. Baseline integrity therefore passes.

Reproduction:

```bash
DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python -m pytest
```

## Evaluation-set provenance

The inventory is recorded in
`examples/bo_m3/evaluation_inventory.json`. It distinguishes five sources:

| Set | Classification | Limitation |
|---|---|---|
| HCA M1S2 comparison | development | Used repeatedly to change M2 heuristics |
| NFH M1S2 comparison | development | Developer-authored synthetic breadth case |
| HCA judge J01-J18 | development | Pre-registered, then used to revise the guard |
| HCA judge K01-K12 | held out with caveat | Not executed before registration, but same developer, borrower, filing and task family |
| M3 controlled comparison | development | Synthetic architecture and metric regression |

No set is represented as independent validation. The inventory covers literal
facts, paraphrases, inference, negation, numeric/denominator mismatch, entity and
period mismatch, intentions presented as outcomes, demonstrated and
undemonstrated mitigants, contradictions, ownership leakage and material
omissions. Evidence authenticity, factual corroboration, semantic entailment,
verification status and credit interpretation remain separate fields or gates.

## Optional semantic entailment

The existing R5 path already provided a batched premise judge and a deterministic
guard. This cycle adds `bo-semantic-entailment-1`, a reusable service contract
with these properties:

* the caller supplies the approved evidence allow-list; the service performs no
  retrieval;
* numeric, entity, period, denominator and source checks run first and cannot be
  overridden;
* results are `supported`, `contradicted`, `insufficient` or `uncertain`;
* malformed output and invented evidence fail closed to `uncertain`;
* model, prompt version, evidence IDs, reasoning, deterministic checks and human
  adjudication are retained;
* one judge result does not itself promote a BO finding. Existing hypothesis
  testing, baseline validation, presentation policy and human-review gates still
  apply.

The previously recorded R5 outputs, inspected without making a new call, show:

| Dataset | Runs | Guarded result |
|---|---:|---:|
| J01-J18 development set, guard v2 | 1 | 18/18 correct; 0 false support; 0 misses |
| K01-K12 held-out-with-caveat | 2 | 12/12 correct in each run; 0 false support; 0 misses |

Those results are encouraging but do not pass the independent-validation part of
Gate 2. The recorded artifact identifies only the “default narrow tier”; related
M2 transcripts identify Gemini 3.1 Flash Lite. This metadata ambiguity is itself
a reason not to treat the result as production calibration.

## Hybrid selective investigation

`bo-hybrid-selection-1` introduces the three configurable modes
`single_pass`, `full_investigation` and `hybrid_selective`. The executor accepts a
broad candidate set, selects candidates deterministically, calls the supplied
investigation function only for selected candidates, merges replacements into
one authoritative output, and records per-hypothesis lineage.

Selection includes material adverse risks, asserted demonstrated mitigants,
inferences and assumptions, conflicting evidence, weak provenance, material
coverage gaps, low-confidence material claims and material claims that failed
deterministic validation. Near duplicates are retained in the audit decision and
excluded from repeated investigation. Thus an unsupported material claim is
more likely to be investigated, not filtered out as unconvincing.

This service has not yet been connected to model orchestration or persistence in
`BusinessOverviewRunner`. Its present purpose is to make the policy independently
testable before changing the production DAG.

## Controlled three-way comparison

The reproducible fixture is `examples/bo_m3/offline_comparison.json`; run:

```bash
DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python \
  -m app.services.bo.m3_evaluation examples/bo_m3/offline_comparison.json
```

| Metric | A single pass | B full investigation | C hybrid selective |
|---|---:|---:|---:|
| Material-risk recall | 10/12 | 10/12 | 12/12 |
| Unsupported claims accepted | 5/8 | 0/8 | 0/8 |
| Correct findings retained | 2/4 | 2/4 | 4/4 |
| Evidence completeness | 6/12 | 10/12 | 12/12 |
| Contradiction coverage | 0/1 | 1/1 | 1/1 |
| Off-dimension leakage | 1 | 0 | 0 |
| Logical model calls | 1 | 4 | 3 |

Token usage and runtime are deliberately `null`: scripted callbacks do not
measure provider tokens or meaningful latency. Estimated monetary cost is zero
for the offline run. These values are regression expectations for the policy and
metrics, not evidence of model quality. C02 (supported paraphrase) and C12
(omitted adverse risk) are the material findings missed by A and B in the
fixture; A incorrectly accepts C03, C07, C09, C10 and C11. The fixture was
written by the developer, so it cannot establish that hybrid is superior.

## Ownership enforcement

`bo-semantic-ownership-1` screens conclusion text in addition to the supplied
dimension label. Clear violations remain in `InvestigationResult.out_of_scope`
with the claim, reason, policy version and suggested destination; they are never
silently forwarded or promoted. Current rules cover:

* M1S2 conclusions about industry-wide external events or geographic exposure → M2;
* M1S4 governance, board and senior-management conclusions → M3;
* M1S5 strategy, acquisitions and capital allocation → M4;
* M1S3 payer-mix claims without pricing or realization content → M1S2.

Tests preserve legitimate contextual use, including M1S2 geographic customer
context and M1S3 payer evidence tied to negotiated rates. This deterministic
screen only catches clear patterns. It reduces known leakage but is not a full
semantic classifier, so ambiguous ownership still requires review.

## Gate assessment

| Gate | Result | Evidence / blocker |
|---|---|---|
| 1 Baseline integrity | Pass | 1,332 passed, 13 explained skips |
| 2 Semantic validation | Partial / fail for rollout | Offline adversarial contract passes; prior K set is held out only with caveat, not independent |
| 3 Hybrid evaluation | Partial / fail for superiority claim | Three modes reproducible on identical synthetic cases; no independent or live-model comparison |
| 4 M3 readiness | Fail | Runner/DAG/persistence/Agent 6 integration and bounded live cost remain unverified |

## Exact integration plan

1. **`BusinessOverviewRunner`:** add an explicit mode setting defaulting to
   `single_pass`; construct one sealed packet per specialist; pass the broad
   validated records to the hybrid selector; invoke the existing investigation
   stages only for selected IDs; persist the merged authoritative result plus
   selection lineage. A failed selective stage must mark that specialist
   incomplete rather than fall back silently.
2. **v3 registry and DAG:** keep the five M1 specialists and Agent 6. Add mode and
   policy versions to each run definition/hash. Schedule selected investigations
   inside the owning specialist job; do not add an agent. Preserve separate
   validator identities.
3. **Per-specialist validators:** run current deterministic validation before
   selection and again after selective investigation. Add ownership decisions to
   validation audit. A semantic result may corroborate narrative wording only
   after deterministic source/entity/period/number checks pass.
4. **Completeness gates:** assess the entire required agenda, including
   unselected broad candidates and explicit omission probes. Do not mark a
   specialist complete merely because all selected investigations completed.
5. **Agent 6 contract:** accept only merged records that passed the specialist
   validator and step 12. Carry qualified/open questions explicitly. Exclude
   routed out-of-scope records while preserving their audit links.
6. **Audit/reporting:** persist mode, candidate set, deduplication decisions,
   selection reasons, policy/prompt/model versions, evidence IDs, judge result,
   human adjudication, model calls, tokens, cost and stage timings. Report missed
   material findings and incorrectly accepted claims by case, not only totals.
7. **Next evidence gate:** create an independently authored, borrower-diverse
   held-out set before changing defaults; then run A/B/C on identical packets.
   Request a paid-call budget only after offline scripts and expected outcomes are
   frozen. Require repeated runs because prior live outputs varied materially.

The next development cycle should stop at this gate and seek review of the
inventory, selection policy and proposed independent evaluation set before any
full M3 orchestration or paid model work.
