# Agent 6 evidence-retrieval feedback

Module 1 Agent 6 (`m1_resilience_integration`, BO v3) now completes a bounded evidence-feedback cycle before downstream synthesis. Legacy BO v2 and Agents 1–5 keep their existing retrieval behaviour.

## Execution and eligibility

1. Deterministic preprocessing, primary specialists, their retrieval follow-up and coverage follow-up run as before.
2. Agent 6 receives the original validated upstream findings, indicators and initial admitted packet. Citation repair, deterministic/schema validation, hypothesis validation and parameter gating all run as before. Agent 6 hypothesis validation is always enforced, including when other agents use advisory validation.
3. The validated first response is held in memory as a provisional candidate. Its raw response remains in its AgentRun; an append-only `agent6_retrieval_feedback` audit event records the provisional response, validated classifications and adjustments. Provisional hypotheses and parameters are **not** inserted into the canonical hypothesis/parameter stores.
4. The deterministic missing-information bridge merges explicit requests (highest priority), material hypothesis-level gaps, and global response-level gaps. Its accepted requests are sent to the existing `BoEvidenceRetriever.follow_up` against the same canonical snapshot's admitted narrative corpus. No new documents, external searches or indicators are introduced.
5. If evidence IDs new to Agent 6's sealed packet are returned, the expanded packet is resealed and persisted. Its source provenance, existing evidence IDs and inherited primary citation IDs remain available. The existing corpus-to-text registry already contains those passages for citation validation.
6. Agent 6 runs once more, with the same upstream findings/indicators, original evidence, additional admitted passages and a narrowly scoped `retrieval_reconsideration` runtime context. This labels the first response as provisional analysis and asks the agent to consider support and contradiction, revise or reject earlier hypotheses, and record unresolved gaps. The V5 prompt is untouched.
7. The final response passes the existing validator/gating pipeline against the final sealed packet. Only final eligible findings/parameters are promoted. The orchestrator and credit memo consume these canonical outputs. Pending-cycle guards also prevent `_usable()` and `_business_parameters()` from exposing Agent 6 during the cycle.

The reconsideration AgentRun links to both the primary runs and its initial AgentRun, with rerun reason `resilience_retrieval_follow_up`. Normal final hypothesis/parameter classifications and validator adjustments remain in the existing stores and reports. First-pass outputs remain separately queryable through their AgentRun and immutable audit events.

## Request handling

`eligible_requests` is specific to Agent 6; it does not change the shared retriever. It requires a supported purpose (`support`, `contradict`, `mitigant`, `mitigant_challenge`), a nonempty list of nonblank string terms and, when supplied, an originating hypothesis ID present in the validated first response.

Each original request receives an audit outcome. Duplicate terms and requests are collapsed, including case/whitespace variations and term sets with at least 80% overlap. Requests concerning different hypotheses or different purposes are kept distinct. Regex terms retain their execution semantics; escaped regexes are compared conservatively. Request and term counts use the existing `RetrievalBudget` limits. Ranking, passage selection, source classification, instruction-like text flags, evidence deduplication and provenance remain the shared retriever's responsibility. Follow-up passages use its existing bounded additive budget rather than displacing the initial packet.

Retrieval attempts retain purpose, originating hypothesis ID, source references, search terms, hits/gaps and round index. A retrieval hit remains evidence to assess; it does not change a hypothesis's truth status or classification automatically.

## Bounds, failures and gaps

- No actionable requests: release the initial validated candidate without another model call.
- No new evidence (including already-admitted IDs): record unresolved requests and search outcomes, then release the initial eligible candidate.
- Follow-up rounds disabled: record `retrieval_disabled` and outstanding gaps; release the initial candidate without claiming reconsideration.
- Retrieval failure: record diagnostics and mark Agent 6 failed; no provisional hypothesis or parameter is promoted. Other specialists retain their outputs and normal incomplete-module/human-review policies apply.
- Failed/rejected reconsideration: retain both AgentRuns and retrieval diagnostics; withhold provisional outputs. Validation-rejected final claims remain subject to existing audit-only classifications.
- Further second-pass requests: record them as outstanding, but do not retrieve again. Downstream data limitations identify outstanding searches or cycle failures without presenting provisional claims as findings.

There is at most one retrieval/reconsideration cycle per Agent 6 execution, even when `max_followup_rounds` is configured above one. Existing runtime retry/error handling remains in place; this bound concerns reasoning/retrieval stages, not provider transport retries. A later challenger-directed execution has its own bounded cycle and preserves normal append-only supersession. Follow-up attempt round indices distinguish the initial cycle from challenger remediation.

## Files in this implementation

| File | Change |
| --- | --- |
| `app/services/bo/runner.py` | Provisional hold, one-cycle orchestration, runtime reconsideration context, final promotion, failure handling, audit events, downstream limitations and challenger integration. |
| `app/services/bo/resilience_retrieval.py` | Agent 6 request validation, hypothesis association, deduplication and budget eligibility. |
| `app/services/bo/reporting.py` | Exposes the complete feedback trace in the existing audit report. |
| `tests/unit/test_bo_agent6_requests.py` | Eligibility/overlap/purpose/regex/budget tests and V5 prompt/validator invariants. |
| `tests/integration/test_bo_agent6_feedback.py` | Synthetic end-to-end retrieval, revised validated output, final-only downstream delivery, failure cases, bounds, provenance and challenger history. |
| `docs/agent6-retrieval-feedback.md` | Execution contract, audit semantics, limits and verification notes. |

Pre-existing local prompt packaging and validator 2.2.1 changes were preserved. This feature does not change the response schema, parameters/ownership, indicators, primary specialist prompts, or shared retrieval behaviour.

## Verification

All feature tests use an isolated in-memory database, synthetic Northfield Health Systems passages and `ScriptedBoBackend`. No live Vertex requests, production company-record updates or model-comparison experiments are performed.

The controlled trace records one real deterministic search, admission of `ev_feedback`, the expanded second-pass inputs, revised final hypothesis `M1A6-H002`, and downstream orchestration containing that final hypothesis rather than provisional `M1A6-H001`.

Verified configuration: provider `vertex`, narrow/orchestrator model `gemini-3.7-flash`; `.env` and inference settings were not changed. Validator version remains `bo-hypothesis-validator-2.2.1`. The V5 specialist prompt plus existing provider guard retains assembled SHA-256 `759759b716fde841386b42e31863c4264939690c2c75ff725d6bf37a22b1c4f9`.

Tests run from a byte-matched temporary source mirror with the existing pinned dependency runtime because an unrelated local filing-fixture read stalled in the Documents checkout. The HCA package workflow test is excluded from this synthetic verification. Initial feedback-loop regression result (before the bridge): **238 passed, 1 skipped (opt-in live-model test), 1 deselected (HCA package workflow)** in 26.84 seconds. The new feature coverage comprises 19 unit/integration cases. `git diff --check` passed. Results and the controlled trace are saved in `../Agent6_Feedback_Implementation_20261030/`.

The existing deterministic retriever searches only the admitted snapshot and uses its existing regex/lexical ranking; relevant evidence absent from that snapshot remains an unresolved gap. Request deduplication is lexical rather than semantic. Offline tests prove workflow, safety and lineage, not a live model's analytical quality. No commit, push or pull request was made.


## Missing-information bridge (a6-gap-bridge-1)

`bridge_requests` runs after initial schema/semantic validation and parameter gating, before the provisional audit checkpoint and existing follow-up retrieval. It reads validated hypotheses' `missing_information` and the response-level field without changing hypotheses, qualifications, confidence, values or indicators.

Priority is deterministic: existing eligible explicit requests, hypothesis-level gaps in validated output order, then response-level gaps. Explicit requests keep the existing eligibility/deduplication semantics. All sources share the existing request/term budget; derived needs can only append within the remaining capacity.

A derived need must have at least three meaningful concepts, including at least two concrete subject concepts, fit a 2,000-character bound, and concern a medium/high-materiality validated interaction that is not rejected, unverified or audit-only. Hypothesis gaps retain their originating ID. For a global gap, materiality and purpose are assessed using the strongest distinct-word overlap with validated material contexts (claim, mechanism, capability and recorded gaps). Different purposes tied for strongest overlap cause rejection. Global requests always keep a null hypothesis ID.

Explicit confidentiality, non-public/undisclosed information and predictive wording cause rejection. Causal savings attribution is not assumed impossible merely because it is missing: a cheap disclosure search is permitted unless the need expressly states that it is unavailable. The bridge does not require advance proof that a corpus answer exists.

Query text is Unicode/whitespace-normalized. Generic question wording is removed from concept selection. Capitalized multiword phrases preserve named subjects; adjacent source phrases are selected, prioritising operational/financial concepts, with source words as fallback. All generated phrases are regex-escaped and capped by `max_followup_terms`. No synonyms or company facts are generated. This changes query construction only; shared retrieval ranking is unchanged.

A compound-risk evidence need uses `support`; explicit counterevidence/challenge wording uses `contradict`. Protective-capability existence uses `mitigant`, while outcomes, savings, effectiveness or attribution use `mitigant_challenge`. These are search intents, not conclusions that the retrieved text supports the hypothesis.

Derived searches are deduplicated against earlier queued needs with the same purpose using equal concept sets or at least three shared concepts covering 60% of the smaller set. The existing 80% term-set rule continues to apply through `eligible_requests`. Different purposes are kept separate. Explicit requests are never displaced. A narrow initial-packet check can suppress a derived search when all original gap concepts and any requested numeric marker are co-located in an unnegated admitted passage. Causal-attribution needs are never suppressed by this co-mention check. Suppression does not certify resolution.

The existing loop then searches the same admitted snapshot, preserves provenance and reseals new evidence, and invokes its one reconsideration pass only if new evidence IDs are admitted. No second pass follows an unsuccessful search alone. Final-only promotion and failure withholding are unchanged.

Each `information_gaps` audit entry retains source/original text, originating ID (or null), normalized text, materiality basis, decision/reason, purpose/terms, deduplication outcome, query index, attempt IDs, retrieved IDs, new admitted IDs and reconsideration status. Request audits and search entries use `request_origin=agent_explicit` or `missing_information_bridge`. The shared attempt channel remains `origin=agent_request` for schema compatibility; the Agent 6 audit explicitly distinguishes the true request origin. The existing report serializer includes these fields without a reporting-schema change.

Every gap remains unresolved from the deterministic layer's perspective: a lexical match, model omission or packet co-mention cannot prove an answer. The separately recorded `final_reported_information_gaps` preserves the final model's own missing-information fields. Original unresolved needs reach downstream `data_limitations` as questions, explicitly labelled as information needs rather than evidence; provisional analytical findings are still withheld.

The bridge adds no model calls beyond the already permitted reconsideration and never recursively examines second-pass gaps for another search. Subsequent challenger-directed executions retain their existing independent one-cycle allowance.

## Bridge offline verification and limitations

The HCA FY2024 Run 2 verification considered three original entries: a hypothesis-level Galen savings attribution gap, a global contract-renewal schedule/expiry-share gap, and a global Galen savings gap. Two searches were accepted; the latter Galen entry shared the hypothesis-level search without gaining an invented hypothesis association. Both searches used the original 488-passage canonical corpus and initial sealed citation IDs.

The bounded retrieval returned five passage references: four were already admitted to Agent 6; one was genuinely new. Existing Galen passages discuss training, education investment and nursing pipelines, not quantified net savings causally attributable to graduate hires. Contractual context states typical one-to-three-year terms, but does not give the requested renewal schedule or 12–24-month expiry share. Other matches discuss payer revenue or system risks. The one new information-systems-risk passage is a lexical false positive for this information need. None of the returned passages resolves either gap.

Under the existing ID-based loop rule the new passage would make reconsideration eligible. The offline verification does not invoke a model. It does not certify semantic relevance or resolution merely because admission returned a new ID, and does not prove that no answer exists elsewhere in the corpus. Full passages and a separate evidence-by-evidence review are retained in the offline verification artifacts.

Remaining limitations: lexical materiality matching and 60% topic overlap can miss paraphrases or collapse distinct related needs; conservative specificity/availability rules can reject a useful disclosure search; capitalized phrases are a lexical heuristic, not verified entities; initial co-location checks may suppress a search without settling its question; a bounded corpus search can miss evidence; and a newly admitted but irrelevant lexical hit can still trigger the existing reconsideration rule. No semantic ranking, automatic gap resolution or causal-attribution determination is introduced.

Bridge-specific unit tests cover extraction/malformed entries, global associations, materiality, vague/unavailable gaps, ambiguous purposes, explicit priority/shared budgets, lexical deduplication, entities/regex safety, initial-packet suppression, causal co-mentions, HCA Run 2 fixtures and audit links. Added integration cases cover gap-only triggering from both levels, admission/packet expansion, final-only handoff, downstream unresolved gaps, no-hit single-pass behaviour, duplicate searches and both failure paths. Agents 1–5 continue to use their original retrieval path.

Bridge regression result: **262 passed, 1 skipped (opt-in live model), 1 deselected (full HCA package workflow)** in 27.59 seconds. The requested HCA corpus verification is offline and preserves source model outputs and indicators. Detailed current-task artifacts and completion report: `../Agent6_Gap_Bridge_20261030/`.
