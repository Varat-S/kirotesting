# Governed M3 to Structural Overview Integration

## Delivery

- Integration branch: `feat/bo-m3-so-integration`
- BO input contract: `bo-handoff-1.2` (unchanged)
- M3 input contract: `bo-m3-corporate-assessment-1.0`
- SO output contract: `structural-overview-assessment-1.1`
- Transformation: `bo-to-so-1.1`

This branch combines the completed M3 corporate refinement with the existing
Structural Overview thin slice. The integration reads only the accepted BO
handoff boundary and does not reach into agent transcripts or reconstruct an
M3 result after the fact.

## Borrower hard gate

`AcceptedBusinessOverview.borrower_name` and `borrower_entity_id` describe the
BO reporting context and are not treated as proof of the proposed facility
obligor. The SO borrower fields are populated only when the authoritative
`module_conclusions.M3.m3_assessment.borrower_identification` is `identified`
and resolves to a validator-admitted legal entity.

When M3 is absent, unavailable, disputed, or unable to identify the borrower,
the SO assessment keeps the reporting entity for case context but emits no
transaction borrower name or entity ID. It carries the M3 limitation and SO
dependency forward. This prevents a reporting parent or historical issuer from
being silently promoted to the proposed borrower.

## Governed graph boundary

The consumable `corporate_structure` fields contain only validator-admitted:

- individually identified legal entities;
- ownership relationships;
- cash-flow locations and their measure types;
- cash-transfer relationships;
- structural credit findings;
- management and governance observations; and
- information gaps and downstream dependencies.

Confirmation status, disposition, admissibility, validation reasons, and SO
verification requirements are preserved by the typed contract. SO must still
independently verify obligor identity, legal ranking, restriction
enforceability, and facility-specific legal effect.

Validator issues, rejected records, and the original model proposal are
retained beneath `corporate_structure.audit`. The JSON and HTML label these as
audit-only; they are not mixed into the legal-entity or relationship lists.

## Compatibility and fail-closed behavior

A valid `bo-handoff-1.2` produced before the M3 extension still maps
deterministically. Its corporate structure and transaction borrower are marked
unavailable. A handoff that marks M3 `validated` but omits or malforms the
assessment is rejected. A governed assessment whose identified borrower does
not resolve to an admitted legal entity is also rejected.

The SO schema and transformation were advanced to 1.1 because borrower meaning
and corporate-structure output changed materially. The BO handoff remains 1.2,
as required by the upstream compatibility decision.

## Validation scope

The targeted integration suite covers:

- pre-M3 `bo-handoff-1.2` compatibility;
- validated borrower and legal-entity mapping;
- preservation of qualified edges and findings;
- audit-only retention of rejected collective groupings;
- unknown-borrower fail-closed behavior;
- malformed governed M3 rejection;
- borrower-to-entity referential integrity;
- deterministic JSON and HTML hashing/rendering; and
- existing BO governance, downstream contract, and M3 validator cases.

The local R5 HCA artifact is a specialist-run transcript, not a complete
accepted BO handoff. It was therefore not converted directly into SO output;
doing so would require fabricating missing conclusion, acceptance, lineage, and
handoff-integrity fields. No live model calls are required by this integration.

Validation on 2026-10-18 completed with:

- targeted BO/M3/SO integration: 47 passed, 1 optional historical-artifact test skipped;
- complete repository regression: 1,400 passed, 15 skipped; and
- Python compilation and Git whitespace checks: passed.

The full run emitted one existing Starlette/httpx deprecation warning. Skips
were for unavailable local fixtures or optional dependencies and the explicitly
disabled live-model test.
