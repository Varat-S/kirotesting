from app.core.config_registry import ConfigRegistry
from app.schemas.enums import FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef


def test_mapping_registry_maps_without_losing_provenance(db_session):
    from app.services.mapping.mapper import FinancialMapper
    from app.core.bootstrap import bootstrap_config

    registry = ConfigRegistry(db_session)
    versions = bootstrap_config(registry)
    mapper = FinancialMapper.from_registry(registry, versions["financial_mappings"])
    for label, source_type in [
        ("us-gaap:Revenues", "xbrl"),
        ("Operating Revenue", "table_label"),
    ]:
        fact = CanonicalFact(
            fact_id=label,
            name=label,
            normalized_value=1000,
            status=FactStatus.UNVERIFIED,
            source_refs=[SourceRef(document_id="source", row_label=label)],
        )
        result = mapper.map_fact(fact, source_type)
        assert result.status == "mapped"
        assert result.fact.name == "revenue"
        assert result.fact.original_name == label
        assert result.fact.source_refs == fact.source_refs
        assert result.fact.mapping_version == versions["financial_mappings"]
        assert mapper.map_fact(fact, source_type) == result


def test_unknown_and_ambiguous_mapping_never_guess(db_session):
    from app.services.mapping.mapper import FinancialMapper

    registry = ConfigRegistry(db_session)
    row = registry.register(
        "financial_mappings",
        {
            "rules": [
                {
                    "source_type": "table_label",
                    "source_key": "Cash",
                    "canonical_name": "cash",
                },
                {
                    "source_type": "table_label",
                    "source_key": "Cash",
                    "canonical_name": "unrestricted_cash",
                },
            ]
        },
    )
    mapper = FinancialMapper.from_registry(registry, row.version)
    for label, expected in [("Unknown", "unmapped"), ("Cash", "ambiguous")]:
        fact = CanonicalFact(
            fact_id=label,
            name=label,
            status="missing",
            source_refs=[SourceRef(document_id="source")],
        )
        result = mapper.map_fact(fact, "table_label")
        assert result.status == expected
        assert result.fact.name == label
        assert result.fact.mapping_status == expected


def test_mapping_qualifiers_require_matching_metadata(db_session):
    from app.services.mapping.mapper import FinancialMapper

    registry = ConfigRegistry(db_session)
    registry.register(
        "financial_mappings",
        {
            "rules": [
                {
                    "source_type": "xbrl",
                    "source_key": "x:Revenue",
                    "canonical_name": "revenue",
                    "entity_scope": "consolidated",
                    "accounting_basis": "GAAP",
                }
            ]
        },
    )
    mapper = FinancialMapper.from_registry(registry, 1)
    fact = CanonicalFact(
        fact_id="a",
        name="x:Revenue",
        status="missing",
        source_refs=[SourceRef(document_id="source")],
    )
    assert mapper.map_fact(fact, "xbrl").status == "unmapped"
    fact = fact.model_copy(
        update={"consolidation_scope": "consolidated", "accounting_basis": "GAAP"}
    )
    assert mapper.map_fact(fact, "xbrl").status == "mapped"


def test_config_bootstrap_is_versioned_and_preserves_old_mapping(db_session):
    from app.core.bootstrap import bootstrap_config
    from app.services.mapping.mapper import FinancialMapper

    registry = ConfigRegistry(db_session)
    first = bootstrap_config(registry)
    assert bootstrap_config(registry) == first
    old = registry.get("financial_mappings", first["financial_mappings"])
    content = {
        **old.content,
        "rules": old.content["rules"]
        + [
            {
                "source_type": "table_label",
                "source_key": "New",
                "canonical_name": "revenue",
            }
        ],
    }
    revised = registry.register("financial_mappings", content)
    assert revised.version == old.version + 1
    assert revised.content_hash != old.content_hash
    assert FinancialMapper.from_registry(registry, old.version).version == old.version
