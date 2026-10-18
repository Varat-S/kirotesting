# M3 Live-Run Analytical Remediation

Date: 2026-10-18  
Branch: `feat/bo-m3-corporate-refinement`  
Diagnostic run: `HCA_M3_REFINEMENT_LIVE_20261018_R5` / `ar_fb0bba6709e94a14`

## Outcome

Validator 7 now treats the reporting company, a proposed facility borrower, a
historical debt issuer, a collective grouping, and an individually identified
legal person as separate concepts. Specialist-only runs export the exact
runner-owned `module_conclusions.M3.m3_assessment` to
`m3_governed_assessment.json` and render the same object in
`m3_governed_assessment.html`.

The export separates the model proposal, validator findings, final governed
content, excluded material, and unresolved information. If no governed M3
assessment exists, both formats say `unavailable` and retain the reason. No
second M3 state is maintained by reporting code.

## HCA offline deterministic replay

The final accepted M3 attempt and its sealed evidence IDs remain in
`data/credit_memo.db`, so the final proposal can be replayed through the current
Validator 7 without a provider call. This is an offline validator replay, not a
new live-model evaluation.

Current-validator result for recorded attempt
`10d34a4a-0f88-40f8-8c14-221d816e76b1`:

- Proposed borrower: `unavailable`; SO must confirm the facility obligor from
  transaction-specific evidence.
- Reporting entity: `HCA_HEALTHCARE_INC` remains in the graph as a verified
  reporting legal entity.
- `HCA_AFFILIATES`: excluded as an unresolved/collective identity rather than
  promoted to a subsidiary legal person.
- Ownership edges: 0 accepted; the edge to the collective node is excluded.
- Cash-transfer edges: 0 accepted; the edge from the collective node is
  excluded.
- Cash-flow locations: 0 accepted; the untyped consolidated amount cannot be
  treated as entity-level operating cash flow.
- Management observation: audit-only because the proposal did not record a
  contradictory-evidence investigation, supplied an invalid partial effective
  date, and relied on reported experience rather than demonstrated outcomes.
- Governance observation: audit-only because it was borrower-specific while no
  transaction borrower was established and did not record a contradictory
  evidence investigation.

The historical specialist report did not persist
`module_conclusions.M3.m3_assessment`, which is the omission fixed here.
Accordingly, the original runtime object cannot be recovered byte-for-byte and
is not fabricated from transcript data. The recorded model proposal, validation
adjustments, sealed packet, and current deterministic replay remain separately
inspectable.

## Operating-entity evidence diagnosis

The FY2025 Exhibit 21 document was admitted into the snapshot as a document but
its paragraph-form list was not converted into narrative evidence. It contains
named legal entities (for example, `HCA Holdco, LLC`) but the old M3 packet did
not contain those names. The parser now supports paragraph-form Exhibit 21
lists, and M3 retrieval has a targeted named-legal-entity query.

On the local HCA Exhibit 21, the updated offline parser extracts 2,506 named
rows and finds `HCA Holdco, LLC` in Delaware. This proves that names were
available in an admitted source. It does **not** establish which entities are
material operating entities, their facility roles, proposed-obligor status, or
cash-transfer paths. `hc.operating_entities` must therefore remain unresolved
unless admitted evidence supports both identity and the relevant operating
role. The facility count of 190 hospitals remains separate from legal-entity
identification. Quote verification was not weakened.

## Performance findings

The three recorded M3 calls used:

| Call | Reason | Input | Output | Evidence | New evidence versus prior call |
|---|---|---:|---:|---:|---:|
| 1 | Initial | 19,832 | 4,534 | 17 | 17 |
| 2 | Retrieval follow-up | 21,065 | 4,293 | 19 | 2 |
| 3 | Coverage follow-up | 23,248 | 4,808 | 19 | 0 |

Total M3 usage was 77,780 tokens. The retrieval follow-up introduced two new
passages. The coverage follow-up repeated all 19 passages and introduced no new
evidence. The runner now stops that specific retry when every cited coverage
hypothesis is unusable because the bounded quote-recovery search already
exhausted the admitted corpus. Other correctable coverage failures retain the
existing single bounded retry.

No token saving is claimed: no comparable new live-model run was performed.

## Compatibility and boundaries

- Existing `bo-handoff-1.1` payloads remain parseable.
- `bo-handoff-1.2` continues to carry additive module conclusions.
- Historical issuer facts can remain in the legal-entity graph without becoming
  the proposed borrower.
- Revenue, EBITDA/operating income, operating cash flow, free cash flow, cash
  balance, distributable cash, and actual distributions are typed separately.
- Auditor ICFR evidence stays scoped to financial reporting, the stated date,
  and the opinion's materiality language.
- The specialist-only summary is explicitly provisional and emits no final
  credit score, obligor rating, facility approval, or orchestrator conclusion.

## Remaining limitations and readiness

The workflow is ready for controlled SO interface integration with one hard
gate: SO must require an identified transaction borrower before consuming
borrower-specific graph paths. Exhibit 21 supplies names, not complete ownership,
materiality, guarantee, ranking, covenant, or cash-transfer evidence. Those gaps
remain explicit downstream dependencies rather than inferred mitigants or
automatically addressed coverage.
