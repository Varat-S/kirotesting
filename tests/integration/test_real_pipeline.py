"""These tests feed actual source bytes into the real pipeline."""


def test_pipeline_maps_parses_calculates_and_renders(db_session, tmp_path):
    from app.services.pipeline.runner import CreditMemoPipeline
    from app.services.pipeline.package import SourcePackage

    package = SourcePackage.model_validate(
        {
            "as_of_date": "2024-12-31",
            "evidence_cutoff_timestamp": "2025-01-15T00:00:00Z",
            "borrower_entity_id": "DAL",
            "entities": [
                {
                    "entity_id": "DAL",
                    "legal_name": "Delta Synthetic",
                    "entity_type": "borrower",
                    "borrower_flag": True,
                }
            ],
            "sources": [
                {
                    "filename": "financials.csv",
                    "data": b"Line,2024\nOperating Revenue,1000\nOperating Income,200\n",
                    "parser": "csv",
                    "tags": ["financial_statements"],
                    "available_at": "2025-01-10T00:00:00Z",
                    "entity_id": "DAL",
                    "scope": "consolidated",
                    "accounting_basis": "GAAP",
                    "parse_options": {
                        "scale": "millions",
                        "currency": "USD",
                        "period_headers": {"2024": {"fiscal_year": 2024}},
                    },
                }
            ],
        }
    )
    result = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    ).run_case("DAL_2024", package=package)
    assert result.metrics["operating_margin"].result == 0.2
    assert result.metrics["operating_margin"].review_required
    assert result.evidence_snapshot.config_versions["financial_mappings"] == 1
    assert result.draft_snapshot.payload["recommendation"]["status"] == "draft"
    assert result.final_snapshot is None
    assert result.output_paths["json"].exists()
    assert result.output_paths["html"].exists()
