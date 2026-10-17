# BO Analytical Evaluation Specification

**Phase:** B (analytical evaluation framework)
**Date:** 18 October 2026
**Branch:** `feat/bo-validation-release`
**Status of evaluation data:** developer-authored, **awaiting independent credit-analyst review**

## 0. Purpose and boundary

Passing software tests does not establish credit-analysis quality. This document
defines a **defensible, reproducible method** for measuring the quality of the
BO analysis a *live model* produces, so that a future authorized live run (Phase
C/D) can be scored against a frozen key rather than judged ad hoc.

It does **not** itself contain an independent analyst assessment, and it does not
authorize any paid model call. It builds on the evaluation assets already in the
repository rather than replacing them:

- `examples/bo_m3/evaluation_inventory.json` — provenance inventory of existing sets.
- `examples/bo_m3/independent_evaluation/` — 18 frozen cases (HCA, Delta, NVIDIA),
  `manifest.json` with primary-document SHA-256 hashes, status
  `developer_authored_awaiting_external_review`.
- `examples/healthcare/hca-2025/review_manifest.json` — HCA answer-key **template**,
  status `unverified_pending_filing`.
- `app/services/bo/semantic_entailment.py`, `judge_eval.py`, `m3_semantic_eval.py`,
  `evaluation.py` — the scoring plumbing.

**Freeze rule.** All evaluation criteria, cases, and the answer key must be frozen
(committed) *before* any model output is compared against them. A frozen key is
never silently rewritten; external review is recorded in a **separate adjudication
artifact**.

## 1. Evaluation cases

### Case 1 — HCA Healthcare FY2025 (primary healthcare case)

| Field | Value / requirement |
|---|---|
| Borrower identity | HCA Healthcare, Inc. (ticker HCA) |
| Fiscal period | FY2025 (10-K) |
| As-of / cutoff | As-of 2025-12-31; evidence cutoff per `package.json` |
| Source document | HCA FY2025 Form 10-K — **not committed** (~10 MB); fetch via `sec-fetch` per `examples/healthcare/hca-2025/README.md` |
| Source version/hash | Must be recorded in the review manifest `filing.primary_document_sha256` **before** evaluation; currently `null` (filing not fetched here) |
| Evidence cutoff | Single cutoff; do not compare runs with different cutoffs without controlling for it |
| Quantitative indicators | Deterministic indicators from the shared pipeline (payer-mix categories, revenue per equivalent admission, etc.); recomputed, never model-asserted |
| Known gaps / limits | `review_manifest.json` is a **template** (all `verified_quote: null`); must be completed from the fetched filing before it is usable as a key |

### Case 2 — Cross-sector borrower (airline or similar, complete annual filing)

| Field | Value / requirement |
|---|---|
| Preferred borrower | Delta Air Lines FY2025 (already a frozen set in `independent_evaluation/`) with NVIDIA FY2026 as the committed cross-sector alternative |
| Rationale | Verifies the pipeline is not healthcare-specific and that an incomplete sector profile (only `healthcare_provider` exists) degrades gracefully |
| Requirements | Same fields as Case 1; record borrower, period, as-of, source hash, cutoff, indicators, and known missing data |

### Case 3 — Contradictory / incomplete evidence (controlled)

| Field | Value / requirement |
|---|---|
| Purpose | Verify uncertainty handling, adverse-finding retention, and escalation |
| Source | The controlled fixtures (`examples/bo_m3/offline_comparison.json`, the NFH synthetic case) plus planted contradictions/omissions |
| Requirements | Each planted defect records the expected disposition (held back / qualified / escalated) and which control should catch it |

For every case, the following must be recorded before evaluation: exact borrower
identity, fiscal period and as-of date, source document version and SHA-256,
evidence cutoff, source availability, relevant quantitative indicators, and the
known missing data / benchmark limitations.

## 2. Answer-key schema

The answer key is a structured review template. The existing
`review_manifest.json` (`bo-review-manifest-1.0`) is the base format; this spec
fixes its required fields and the fact-type taxonomy.

```jsonc
{
  "manifest_version": "bo-review-manifest-1.1",
  "borrower": "<legal name>",
  "filing": { "form": "...", "fiscal_year": 0,
              "accession": "...", "filing_date": "...",
              "primary_document_sha256": "<hash>" },   // REQUIRED before use
  "verification_status": "developer_authored_awaiting_external_review",
  "reviewer": null, "reviewer_note": "...",
  "evidence_cutoff": "<iso8601>",

  "material_strengths":      [ { "id": "...", "concept": "...", "fact_type": "...",
                                 "verified_quote": null, "materiality": "high|medium|low" } ],
  "material_vulnerabilities":[ { ... same shape ... } ],
  "contradictory_or_mitigating_evidence": [ { "id": "...", "concept": "...", "relates_to": "<id>" } ],
  "expected_analytical_mechanisms": [ { "id": "...", "mechanism": "...", "credit_impact": "..." } ],
  "unresolved_factual_uncertainties": [ { "id": "...", "question": "..." } ],
  "required_module_coverage": ["M1","M2","M3","M4"],
  "common_unsupported_inferences": [ { "id": "...", "pattern": "...", "why_wrong": "..." } ],
  "recomputed_values": [ { "parameter_id": "...", "value": 0, "basis": "deterministic_indicator" } ]
}
```

### Fact-type taxonomy (every answer-key item carries exactly one)

| `fact_type` | Meaning | Expected admissibility ceiling |
|---|---|---|
| `reported_fact` | Directly stated in the filing | may reach `established` with a verbatim quote |
| `calculated_fact` | Derived from a deterministic indicator | `established` only if bound to the approved metric |
| `management_claim` | Asserted by management (belief/intent) | `attributed` — never an unqualified fact |
| `supported_inference` | Analyst inference with a declared step | `qualified`/`established` only with the step declared |
| `hypothesis` | Requires further investigation | `audit_only`/`on_demand` open question |

The key also records the appropriate **verification label** (`supported`,
`qualified`, `rejected`, `unverified`) and **materiality** for each item, so the
model's labels can be compared, not just its wording.

**Provisional-key handling.** Where no independent credit analyst is available, the
key is produced as a *provisional candidate* and marked
`developer_authored_awaiting_external_review`; reviewer and adjudication fields stay
empty. Such a key may be used to compute metrics for *internal* comparison only,
and every report built on it must carry that caveat.

## 3. Evaluation metrics

Freeze these before comparing any model output. Report **per-case error analysis**,
not only aggregate scores. Each metric separates the **model's analytical quality**
from the **software control** that accepted or rejected the model's output.

| Dimension | Measurement | Separates |
|---|---|---|
| Factual accuracy | Correct claims & numeric values vs key | model |
| Evidence grounding | Citations that resolve + actually entail the claim | model + control |
| Material-risk recall | Material risks found / material risks in key | model |
| Unsupported conclusions | Unsupported material claims accepted as usable | control |
| Mitigant validity | Claimed protection with an outcome in evidence | model + control |
| Analytical completeness | Required module domains adequately covered | model |
| Causal reasoning | Credit mechanisms explained correctly | model |
| Contradiction handling | Conflicts surfaced and appropriately qualified | model + control |
| Cross-module synthesis | Relevant compound risks identified | model |
| Challenge effectiveness | Material errors detected and corrected | control |
| Redundancy | Duplicate / low-value findings | model |
| Practical usefulness | Reviewer rating of decision relevance (1–5) | reviewer |

**Scoring conventions.**
- Recall/precision reported as fractions with the numerator and denominator shown
  (e.g. "material-risk recall 10/12"), as in `BO_M3_READINESS_REPORT.md`.
- Any dimension without evidence is reported `not_measured` — never estimated.
- "False support" (an unsupported material claim accepted as usable) is a **hard
  gate**: target 0; any occurrence is a case-level finding.

## 4. Sampling plan

- **Repetition:** because live output varies materially (the M2 report recorded
  14–24 usable hypotheses across same-day HCA runs), each configuration is run
  **≥3 times**; report the distribution and the median ("re-validated") outcome,
  not a single run. No production-quality claim from one run.
- **Configurations:** single-pass (production candidate) is primary; experimental
  modes only where the implemented runner supports them, labelled experimental.
- **Controls held constant:** same evidence packet, same cutoff, same approved
  configuration across compared arms (input fingerprint recorded).
- **Case mix:** Case 1 (HCA) first; Case 2 (cross-sector) and Case 3
  (contradictory/incomplete) to probe breadth and uncertainty handling.

## 5. Reviewer instructions (for the independent credit analyst)

1. Work only from the committed primary document identified by its SHA-256; do not
   use any secondary summary as a source.
2. For each answer-key item, paste a `verified_quote` copied verbatim from the
   filing (or mark it unverifiable), assign the `fact_type`, verification label,
   and materiality.
3. Add `recomputed_values` only from the deterministic indicators, never from model
   output.
4. List the material strengths and vulnerabilities you would expect a competent
   analyst to surface, and the common unsupported inferences you would expect a
   weak model to make.
5. Rate practical usefulness (1–5) with a one-line justification.
6. Record your identity, date, and sign-off in the adjudication artifact; set
   `verification_status` to `reviewer_verified` only when every item is checked.
7. Never edit a frozen expectation to match a model output.

## 6. Provenance requirements

Every evaluation artifact must record:

- The set's **role**: `development`, `held_out_with_caveat`, or
  `independent_validation` (the last requires a different author/source family than
  any development use).
- Whether the set was used to tune heuristics (then it is `development`, not held
  out), per the honest classification already in `evaluation_inventory.json`.
- The primary-document path and SHA-256.
- The freeze commit SHA.

A set used to revise a guard or heuristic is permanently `development`; it cannot
be re-labelled held-out. No current set qualifies as `independent_validation`; that
gate remains open pending an external reviewer with a distinct source family.

## 7. What is reproducible today (offline)

```bash
# Provenance inventory and frozen multi-borrower cases:
DEBUG=false .venv/bin/python -m app.services.bo.m3_semantic_eval \
  examples/bo_m3/independent_evaluation/cases.json
# Controlled three-way policy/metric regression (scripted; tokens/cost are null):
DEBUG=false .venv/bin/python -m app.services.bo.m3_evaluation \
  examples/bo_m3/offline_comparison.json
```

These exercise the **contract and metric calculation** with scripted responses.
They demonstrate the framework, not a live model's semantic accuracy, which stays
`not_measured` until an authorized live run (Phase C/D).

## 8. Outstanding gates (require human / external input)

1. Independent credit-analyst completion and sign-off of the HCA FY2025 answer key
   (currently a template, filing not fetched here).
2. An `independent_validation` set authored by someone other than the developer,
   from a source family not already used to tune heuristics.
3. Authorized live-model runs to populate the `not_measured` quality metrics.
