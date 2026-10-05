from datetime import date, datetime, timezone

from sqlalchemy import select

from app.models.orm import AuditEvent, CaseEntity
from app.services.evaluation.manifest import GroundTruthManifest
from app.services.pipeline.evaluation import variant_outcome
from app.services.pipeline.package import SourceInput, SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from tests.integration.test_pipeline_acceptance import ROOT


def test_temporal_replay_runs_real_pipeline_with_contemporaneous_manifests(
    db_session, tmp_path
):
    full = SourcePackage.load(ROOT / "package.json")
    old = SourceInput.model_validate(
        {
            "filename": "historical.csv",
            "parser": "csv",
            "data": b"Line,2022,2023\nOperating Revenue,800,900\nOperating Income,160,180\nTotal Debt,500,600\nUnrestricted Cash,100,100\nEBITDA,200,200\n",
            "tags": ["financial_statements"],
            "available_at": "2024-01-10T00:00:00Z",
            "entity_id": "DAL",
            "scope": "consolidated",
            "accounting_basis": "GAAP",
            "parse_options": {
                "scale": "millions",
                "currency": "USD",
                "period_headers": {
                    "2022": {"fiscal_year": 2022},
                    "2023": {"fiscal_year": 2023},
                },
            },
        }
    )
    past = full.model_copy(
        update={
            "as_of_date": date(2023, 12, 31),
            "evidence_cutoff_timestamp": datetime(2024, 1, 15, tzinfo=timezone.utc),
            "sources": [old, *full.sources],
        }
    )
    runner = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    )
    early = runner.run_case("DAL_2023", package=past)
    assert early.metrics["operating_margin"].result == 0.2
    assert len(early.rejected_sources) == len(full.sources)
    assert len(early.admitted_source_hashes) == 1
    assert early.benchmarks["net_debt_to_ebitda"]["sample_size"] == 0
    assert all(f.get("fiscal_year", 0) <= 2023 for f in early.evidence_snapshot.facts)
    events = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "evidence_rejected")
        )
    )
    assert len(events) == len(full.sources)
    truth = GroundTruthManifest(
        case_id="DAL_2023",
        as_of_date=past.as_of_date,
        evidence_cutoff_timestamp=past.evidence_cutoff_timestamp,
        manifest_version="2023-1",
        verified_facts=[
            {"name": "revenue", "field_type": "financial", "expected_value": 900}
        ],
        expected_metric_outputs=[
            {"metric_id": "operating_margin", "expected_result": 0.2}
        ],
        future_outcome={"rating_downgrade": True, "observed_at": "2025-12-31"},
    )
    outcome = variant_outcome(early, truth, "DAL")
    assert outcome.produced_fields["revenue"] == 900
    assert not outcome.invented_values
    # Changing future outcome cannot alter contemporaneous scoring inputs.
    alternative = truth.model_copy(update={"future_outcome": None})
    assert variant_outcome(early, alternative, "DAL") == outcome
    late = runner.run_case("DAL_2024", package=full)
    assert late.metrics["net_debt_to_ebitda"].result == 3
    assert not late.rejected_sources
    assert db_session.get(CaseEntity, ("DAL_2023", "DAL")) is not None
    assert db_session.get(CaseEntity, ("DAL_2024", "DAL")) is not None
