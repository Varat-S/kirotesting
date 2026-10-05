from datetime import date, datetime, timezone

from app.schemas.evidence import EntityRecord
from app.services.pipeline.package import SourceInput, SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline


def test_conflicting_non_gaap_values_do_not_poison_gaap_metrics(db_session, tmp_path):
    entity = EntityRecord(
        entity_id="TEST",
        legal_name="Test borrower",
        entity_type="borrower",
        borrower_flag=True,
    )

    def source(filename, data, basis):
        return SourceInput(
            filename=filename,
            data=data,
            parser="csv",
            available_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            entity_id="TEST",
            scope="consolidated",
            accounting_basis=basis,
            tags=["financial_statements"],
            parse_options={
                "currency": "USD",
                "scale": "millions",
                "fiscal_year_end_month": 12,
            },
        )

    package = SourcePackage(
        as_of_date=date(2024, 12, 31),
        evidence_cutoff_timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
        borrower_entity_id="TEST",
        entities=[entity],
        sources=[
            source(
                "gaap.csv",
                b"Line,2024\nOperating Revenue,1000\nOperating Income,200\n",
                "GAAP",
            ),
            source(
                "adjusted.csv",
                b"Line,2024\nOperating Revenue,900\nRevenue,1100\n",
                "non-GAAP",
            ),
        ],
    )
    result = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out"
    ).prepare_case("BASIS", package=package)
    assert result.metrics["operating_margin"].result == 0.2
    assert any(
        dq.state == "conflicting" and "non-GAAP" in field
        for field, dq in result.evidence_snapshot.data_quality.items()
    )
    # Reprocessing identical admitted inputs preserves raw extraction, mapping
    # and metric hashes. The new canonical version intentionally has a new hash.
    from app.services.pipeline.artifacts import verified_artifacts

    repeat = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out"
    ).prepare_case("BASIS", package=package)
    assert repeat.evidence_snapshot.snapshot_version == 2
    first = {a.name: a.sha256 for a in verified_artifacts(db_session, "BASIS", 1)}
    second = {a.name: a.sha256 for a in verified_artifacts(db_session, "BASIS", 2)}
    for name in ["extraction", "mapping", "metrics"]:
        assert first[name] == second[name]
    assert first["canonical_evidence"] != second["canonical_evidence"]
