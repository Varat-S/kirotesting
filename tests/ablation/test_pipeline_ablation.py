from app.services.evaluation.manifest import load_manifest
from app.services.pipeline.evaluation import run_pipeline_ablations
from app.services.pipeline.package import SourcePackage
from tests.integration.test_pipeline_acceptance import ROOT, fresh_run


def test_all_eight_ablation_variants_execute_the_actual_pipeline(tmp_path):
    package = SourcePackage.load(ROOT / "package.json")
    manifest = load_manifest(ROOT / "ground_truth.json")
    artifacts = {}

    def execute(variant, changed):
        engine, session, runner, result = fresh_run(tmp_path / variant, changed)
        try:
            artifacts[variant] = result
            return result
        finally:
            session.close()
            engine.dispose()

    report = run_pipeline_ablations(package, manifest=manifest, execute=execute)
    assert len(report.variants) == 8
    assert all(
        score.graceful and not score.invented_values
        for score in report.variants.values()
    )
    assert report.variants["FULL"].metric_availability == 1
    assert report.variants["FULL"].field_accuracy == 1
    assert report.variants["FULL"].risk_recall == 1
    assert report.variants["FULL"].correct_escalation_rate == 1
    assert (
        report.variants["PDF_ONLY"].field_completeness
        < report.variants["FULL"].field_completeness
    )
    assert report.performance_drop["PDF_ONLY"] > 0
    assert artifacts["PDF_ONLY"].metrics["operating_margin"].result == 0.2
    assert all(
        f["extraction_method"] == "pdf_table"
        for f in artifacts["PDF_ONLY"].evidence_snapshot.facts
    )
    assert artifacts["NO_XLSX"].metrics["load_factor"].result is None
    assert (
        artifacts["NO_XBRL"].metrics["net_debt_to_ebitda"].evidence_quality
        == "unverified"
    )
    assert (
        artifacts["NO_INDUSTRY_DATA"].benchmarks["net_debt_to_ebitda"]["sample_size"]
        == 0
    )
    for variant, tag in [
        ("NO_INDUSTRY_DATA", "industry"),
        ("NO_FUEL_DATA", "fuel"),
        ("NO_DEBT_MATURITY_TABLE", "debt_maturity"),
        ("NO_INTERIM_STATEMENTS", "interim"),
    ]:
        assert (
            tag
            in artifacts[variant].evaluation_artifacts["coverage"]["missing_important"]
        )
        assert any(tag in e["reason"] for e in artifacts[variant].escalations)
    full_hashes = set(artifacts["FULL"].admitted_source_hashes)
    assert set(artifacts["PDF_ONLY"].admitted_source_hashes) < full_hashes
