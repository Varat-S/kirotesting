# Business Overview Module 3 corporate refinement report

## Outcome

Module 3 remains one specialist (`m3_corporate_management`) with the existing
Validator 7 identity, shared Business Overview runner, cross-module synthesis
and independent challenge. It now has two explicit internal dimensions:
corporate/cash-flow structure and senior-management competence/credibility.
The change adds structured evidence without adding agents or an orchestration
path.

This implementation demonstrates stronger *control behaviour*: invalid edges,
unsupported subsidiary cash allocations, unevidenced borrower identity,
unrecorded contradiction searches and management-proxy reasoning are rejected
or downgraded deterministically. It does not demonstrate that a live model has
better factual recall or credit judgement.

## Architecture and contracts

The architecture assessment is in `BO_M3_ARCHITECTURE_ASSESSMENT.md`. The new
strict contract is `bo-m3-corporate-assessment-1.0`, governed by
`validator_7` / `validator-7-1.0.0`. It contains:

- borrower identification with identified, unavailable and disputed states;
- material legal entities, ownership relationships and effective control;
- entity/period/currency/unit/basis-qualified cash-flow locations;
- evidenced cash-transfer relationships and restrictions/dependencies;
- structural credit findings with causal mechanisms and downstream checks;
- separate competence and credibility/integrity management observations;
- governance and related-party screening, information gaps and FO/M4/SO
  dependencies;
- machine-readable validation issues, rejected items and the unchanged model
  proposal for audit.

The object is additive at `module_conclusions["M3"]["m3_assessment"]`. If a
legacy or replayed M3 answer omits it, the module records
`m3_assessment_status="unavailable"` and an information limitation rather than
inventing content. Existing hypothesis dispositions and admissibility continue
to govern downstream use.

New handoffs use `bo-handoff-1.2`, matching the Structural Overview thin-slice:
borrower name/id, as-of and evidence-cutoff dates, and FO handoffs are additive.
Payloads explicitly tagged `bo-handoff-1.1` remain parseable; stored historical
artifacts are not migrated or rewritten.

## Validator 7 checks

Validator 7 now checks stable/unique entity and item IDs; relationship
referential integrity; ownership bounds, ISO dates, evidence and contradictory
control disclosures; borrower evidence; cash-flow metadata and provenance;
unsupported allocation of consolidated cash to a subsidiary; transfer evidence;
management identity, role and effective dates; sealed-packet citation integrity;
supporting and contradictory investigation records; causal-mechanism sufficiency;
disposition/admissibility consistency; and required information-gap declarations.

Invalid relationships and cash claims are removed from the governed graph and
preserved in `rejected_items`. Dependent findings are downgraded to unverified /
audit-only. Qualified, disputed and unavailable states are retained. The rules
do not decide legal enforceability, definitive creditor ranking, governance
effectiveness or whether management is competent.

## Retrieval and analysis

M3 retrieval now separately seeks parents/subsidiaries/JVs, cash location and
distributions, transfer restrictions, management outcomes, control failures,
restatements, enforcement matters, related parties and owner influence. The
prompt requires separation of cash generation, legal control, transfer capacity,
actual distributions and debt-service obligation; forbids borrower assumptions
and consolidated-to-subsidiary allocation; and routes quantified finance work to
FO, stewardship to M4, and definitive obligor/facility/legal work to SO.

Management instructions distinguish demonstrated execution from tenure,
credentials or company growth, and allegations/investigations from established
findings and remediation. Empty governance screening creates an evidence
limitation, not a claim that governance risk is absent.

## Offline acceptance evidence

Invented fixtures cover all requested cases A-J: bounded material group graph;
holding-company distribution dependency; unknown borrower; misleading
consolidated allocation; experienced but poorly supported management;
insufficient management evidence; material related-party concern; contradictory
ownership; citation-without-sufficiency; and legacy handoff compatibility.

Successful commands:

```text
/Users/pearn/NTU/BU5203/kirotesting/.venv/bin/pytest -q tests/unit/test_bo_m3_corporate_refinement.py
10 passed

DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/pytest -q tests/unit/test_bo_m3_corporate_refinement.py tests/unit/test_bo_roster_v3.py tests/unit/test_bo_validation.py tests/unit/test_bo_m3_services.py tests/unit/test_bo_m3_independent_evaluation.py tests/prompts/test_prompt_registry.py tests/integration/test_bo_v3_complete.py tests/integration/test_bo_m3_single_specialist.py
100 passed

DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/pytest -q
1365 passed, 13 skipped, 1 warning in 95.63s
```

The skips were pre-existing environment/data gates: unavailable OCR, an absent
Stryker bundle, HCA manifest state guards, disabled paid live-model execution,
and an unavailable HTML-to-PDF backend. The warning was a third-party Starlette
`httpx` deprecation. `DEBUG=false` was supplied because the ignored local
`.env` currently contains `DEBUG=release`, which is not a valid Boolean setting;
the `.env` file was not modified or committed.

The committed HCA filing was exercised by offline runner/integration tests and
the existing frozen semantic checks. Those are replay/developer-authored
evaluations, not a new real-model analysis. No genuine historical HCA M3
corporate-graph output exists in the repository for an apples-to-apples finding
comparison; claiming a live analytical improvement would therefore be
unsupported.

## Downstream completeness and limitations

M3 can now state a qualified cash-access dependency and carry its provenance to
SO, but it cannot establish an obligor, legal restriction, creditor priority,
guarantee, collateral, covenant or facility protection. It does not allocate
consolidated financials, calculate debt-service capacity or issue a rating.
Graph visualization is deferred. Live-model adherence to the richer structured
response and external reviewer accuracy remain unmeasured. Legal-name resolution
across independently sourced documents remains conservative and may require
human review.

## Integration into the combined credit-memo branch

1. Rebase or cherry-pick this feature commit after the latest BO base.
2. Resolve the expected overlap with the Structural Overview thin-slice by
   retaining its `bo-handoff-1.2` adapter and this branch's identical additive
   handoff fields; do not create `bo-handoff-1.3` solely for M3.
3. Teach the SO adapter to consume optional
   `module_conclusions.M3.m3_assessment`; fail closed when status is unavailable,
   and preserve every confirmation status, disposition, admissibility,
   validation issue and downstream verification requirement.
4. Map only validated entity and transfer edges. SO must independently verify
   obligor identity, legal ranking and restriction enforceability.
5. Run the BO A-J tests, the SO adapter suite, cross-topic acceptance-guard tests
   and the full regression before merging. Keep historical handoffs immutable.
