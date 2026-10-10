"""Build a normal source package from a declared SEC filing bundle."""

from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord
from .package import SourcePackage, SecBundleInput


def sec_source_package(
    bundle, *, cutoff, entity_id=None, legal_name=None, as_of_date=None,
    sector_benchmark=None,
):
    entity_id = entity_id or bundle.ticker or bundle.cik
    entity = EntityRecord(
        entity_id=entity_id,
        legal_name=legal_name or bundle.ticker or bundle.cik,
        aliases=[bundle.cik, f"{int(bundle.cik):010d}"],
        tickers=[bundle.ticker] if bundle.ticker else [],
        entity_type=EntityType.BORROWER,
        borrower_flag=True,
        expected_consolidation_scope="consolidated",
    )
    return SourcePackage(
        as_of_date=as_of_date or bundle.report_date,
        evidence_cutoff_timestamp=cutoff,
        borrower_entity_id=entity_id,
        entities=[entity],
        sources=[],
        sec_bundles=[SecBundleInput(bundle=bundle, entity_id=entity_id)],
        # Optional sector benchmarking (reference data, not borrower evidence).
        sector_benchmark=sector_benchmark,
    )
