"""Explicit, pinned input definitions; derived values retain their components."""

from collections import defaultdict

from app.core.hashing import canonical_json, content_hash
from app.schemas.enums import FactStatus


def derived_observations(facts, definitions):
    pools = defaultdict(lambda: defaultdict(list))
    for fact in facts:
        if (
            fact.mapping_status != "mapped"
            or fact.accounting_basis != "GAAP"
            or fact.dimensions
        ):
            continue
        for did in {r.document_id for r in fact.source_refs}:
            pools[
                (
                    fact.entity_id,
                    fact.fiscal_year,
                    fact.period_type,
                    fact.period_start,
                    fact.period_end,
                    did,
                )
            ][fact.name].append(fact)
    result = []
    for fields in pools.values():
        for name, definition in definitions.items():
            components = []
            for field in definition["inputs"]:
                candidates = fields.get(field, [])
                values = {f.normalized_value for f in candidates}
                if (
                    not candidates
                    or len(values) != 1
                    or None in values
                    or any(
                        f.status not in {FactStatus.VERIFIED, FactStatus.UNVERIFIED}
                        for f in candidates
                    )
                ):
                    break
                components.append(sorted(candidates, key=lambda f: f.fact_id)[0])
            else:
                unit = {f.normalized_unit for f in components}
                if len(unit) != 1:
                    continue
                value = sum(
                    f.normalized_value * factor
                    for f, factor in zip(
                        components, definition["coefficients"], strict=True
                    )
                )
                if definition.get("require_cash_reconciliation"):
                    cash = fields.get("cash", [])
                    if not cash or any(
                        abs(f.normalized_value - value) > 0.001 for f in cash
                    ):
                        continue
                fact = components[0].model_copy(
                    update={
                        "name": name,
                        "original_name": definition["description"],
                        "source_label": name,
                        "raw_value": canonical_json(
                            {
                                "definition": definition,
                                "components": [
                                    {"fact_id": f.fact_id, "value": f.normalized_value}
                                    for f in components
                                ],
                            }
                        ),
                        "normalized_value": value,
                        "source_refs": [r for f in components for r in f.source_refs],
                        "created_by": "deterministic_derived",
                        "normalization_method": "explicit-input-definition:v1",
                        "definition_version": content_hash(definition),
                        "mapping_candidates": [name],
                        "status": FactStatus.UNVERIFIED,
                    }
                )
                fact.fact_id = content_hash(
                    fact.model_dump(mode="json", exclude={"fact_id"})
                )
                result.append(fact)
    return result
