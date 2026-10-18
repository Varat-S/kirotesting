# Module 3 refinement architecture assessment

Base commit: `ae8ec91c185c4107e8caa9fa6aa02afb3a2a7a44` (`feat/bo-investigation-integration`).

## Existing execution path

Roster v3 assigns corporate structure and senior management to the single
`m3_corporate_management` specialist. `BusinessOverviewRunner` supplies its
sealed evidence packet, runs either the single-pass or existing investigation
path, applies the common deterministic output validator and then applies
`HypothesisValidator` under the logical identity `validator_7`. Governed M3
hypotheses enter `module_conclusions["M3"]`; the existing Business Orchestrator,
independent Challenger, reporting and handoff paths consume those governed
findings. No parallel orchestration path is needed.

## Extension point and constraints

The current hypothesis contract cannot represent legal-entity relationships,
cash locations or management-role observations without flattening them into
narrative. The refinement therefore adds an optional, strictly typed,
independently versioned `bo-m3-corporate-assessment-1.0` object to the M3 module
conclusion. Validator 7 validates this object against the M3 sealed evidence IDs
and preserves the model proposal plus machine-readable issues and rejected
items in its audit data. Existing M1, M2, M4 and legacy payloads remain valid
when the extension is absent.

The extension preserves the existing `Disposition` and `Admissibility`
vocabularies. Only established or appropriately qualified findings may be used
downstream; rejected and unverified findings remain audit-only. Unknown borrower
identity is represented explicitly and borrower-specific conclusions are
unavailable. The graph is selective by materiality rather than an exhaustive
subsidiary register.

## Downstream compatibility

The Structural Overview thin-slice branch accepts `bo-handoff-1.2`, preserves
M3 finding qualifications and rejects broken hypothesis/evidence references.
This branch therefore adopts the already-defined additive 1.2 handoff fields
and exposes the M3 extension through `module_conclusions["M3"]`; it does not
change accepted finding semantics. The Credit Orchestrator's same-run,
accepted-state, completeness and score-reference guards remain untouched.
Legacy `bo-handoff-1.1` payloads remain readable by the Pydantic contract, while
newly generated handoffs use 1.2. Historical stored artifacts are not migrated
or rewritten.
