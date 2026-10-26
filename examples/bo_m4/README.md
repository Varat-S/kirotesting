# Module 4 (BO) — output samples

Illustrative **sample outputs** of Business Overview Module 4 (Debt Structure,
Refinancing & Strategy / Capital Allocation), so a downstream consumer can build
against the exact output shape **without running the pipeline**. Generated
deterministically from the HCA FY2025 fixture + a representative strategy-finding
set; figures are illustrative, not a live analysis.

> These committed files are a reference of the **contract/shape**. The real file
> an app run produces (`m4_analysis.json`) is written under the git-ignored
> `output/` directory and is regenerated every run — it is never committed.

## Files

| File | What it is |
|---|---|
| `agent_a_strategy_findings.sample.json` | **Agent A** (`m4_strategy_capital`) — governed strategy hypotheses (acquisition / capex / integration *merit*). Does NOT draw leverage/debt conclusions. |
| `agent_b_debt_output.sample.json` | **Agent B** (`module4_debt_structure_specialist`) — the four deliverables D1–D4 in one schema-validated call. |
| `debt_structure_validator_report.sample.json` | Deterministic re-check of Agent B (5 checks: reconciliation, subordination ratio, maturity order, legal-entity, double-counting). |
| `m4_analysis.sample.json` | **The combined Module 4 output** — Agent B's four deliverables with Agent A's findings folded into Deliverable 4 by a deterministic assembler (`app/services/bo/m4_output.py`). This is what downstream consumes. |
| `m4_segmented_report.sample.pdf` | Human-readable PDF: one segment per file above, ending with the combined output. |

## Two agents, no overlap

- **Agent B** owns the debt: structure, creditor priority, refinancing, maturity,
  and how capital deployment was funded / affected leverage (D1–D4).
- **Agent A** owns strategy/capital-allocation *merit* (acquisition, integration,
  capex strategy) — it does not draw debt/leverage conclusions.
- A **deterministic assembler** (no LLM) folds A's governed findings into B's D4
  to produce `m4_analysis`. No new judgement is introduced at that layer.

## `m4_analysis` shape (top level)

```
{
  "schema_version": "bo-m4-analysis-1.0",
  "artifact_kind": "m4_analysis",
  "module": "M4",
  "status": "complete" | "incomplete",
  "contributors": { agent_b_debt_structure{...}, agent_a_strategy_capital{...} },
  "deliverable_1..4": { ... },          // D4 = credit conclusion (B + folded A)
  "strategy_context": [ ...Agent A findings... ],
  "requires_human_review": true,
  "forced_outcome": false
}
```

Consumer notes:
- Money fields ending `_m` are US$ millions.
- Enums: `overall_credit_rating_verdict` ∈ {strong, satisfactory, adequate, weak};
  `recommended_loan_placement` ∈ {secured_tier_1, senior_unsecured_tier_2};
  `priority_ranking_tier` ∈ {tier_1_direct_opco_secured,
  tier_2_senior_unsecured_guaranteed, tier_3_unguaranteed_holdco}.
- `status` is `incomplete` when Agent B produced no usable deliverables (then
  `deliverable_1..4` are `null`); nothing is fabricated.
- The combined `m4_analysis` is a **superset** of Agent B's strict
  `Module4Output`: Deliverable 4 additionally carries `strategy_capital_supplement`
  (Agent A). Validate B's raw output against `app/schemas/module4.py`
  (`Module4Output`); treat `m4_analysis` as the assembled governed artifact.

## How to consume

- **In the pipeline (code):** read `module_conclusions["M4"]["m4_analysis"]` from a
  Business Overview run (produced in-memory; no file I/O).
- **As a file:** run a BO case with `--enable-m4-debt-specialist` (v3 roster; needs
  `.env` credentials + an admitted filing); `m4_analysis.json` lands in the run's
  output directory.
- **Just to see the shape:** read the `*.sample.json` files here.

Canonical schema: `app/schemas/module4.py` (`Module4Output`); assembler:
`app/services/bo/m4_output.py`. Regenerate these samples if the schema changes.
