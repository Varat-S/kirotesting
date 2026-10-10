from dataclasses import dataclass

from app.core.config_registry import ConfigRegistry
from app.schemas.evidence import CanonicalFact


@dataclass(frozen=True)
class MappingResult:
    fact: CanonicalFact
    status: str
    candidates: tuple[str, ...]


class FinancialMapper:
    def __init__(self, content: dict, version: int, content_hash: str):
        self.rules = content["rules"]
        self.version = version
        self.content_hash = content_hash

    @classmethod
    def from_registry(cls, registry: ConfigRegistry, version: int):
        row = registry.get("financial_mappings", version)
        if row is None:
            raise ValueError(f"Financial mapping version {version} is unavailable.")
        return cls(row.content, version, row.content_hash)

    def extended(self, extra_rules: list[dict], extension_hash: str) -> "FinancialMapper":
        """A mapper with additional rules from a versioned sector configuration.

        The base ``financial_mappings`` artifact is not modified or re-versioned.
        The returned mapper keeps the base version and records a combined hash,
        so every mapped fact stays attributable to both the base mapping and the
        exact sector configuration that extended it.
        """
        if not extra_rules:
            return self
        from app.core.hashing import content_hash

        return FinancialMapper(
            {"rules": [*self.rules, *extra_rules]},
            self.version,
            content_hash({"base": self.content_hash, "extension": extension_hash}),
        )

    def map_fact(self, fact: CanonicalFact, source_type: str) -> MappingResult:
        key = fact.taxonomy_concept or fact.source_label or fact.name
        candidates = set()
        for rule in self.rules:
            if (
                rule["source_type"] != source_type
                or rule["source_key"].strip().casefold() != key.strip().casefold()
            ):
                continue
            qualifiers = {
                "entity_scope": fact.consolidation_scope,
                "accounting_basis": fact.accounting_basis,
            }
            if any(
                rule.get(k) is not None and rule[k] != observed
                for k, observed in qualifiers.items()
            ):
                continue
            candidates.add(rule["canonical_name"])
        status = (
            "mapped"
            if len(candidates) == 1
            else "ambiguous"
            if candidates
            else "unmapped"
        )
        mapped = fact.model_copy(
            update={
                "name": next(iter(candidates)) if status == "mapped" else fact.name,
                "original_name": fact.original_name or fact.name,
                "mapping_version": self.version,
                "mapping_hash": self.content_hash,
                "mapping_status": status,
                "mapping_candidates": sorted(candidates),
            }
        )
        return MappingResult(mapped, status, tuple(sorted(candidates)))
