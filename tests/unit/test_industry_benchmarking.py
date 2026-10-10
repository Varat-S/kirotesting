"""Classification, comparability, industry-aggregate (Method A) and empirical
peer-cohort (Method B) tests, plus the sector financial formulas.

Handoff sections 3-7 and 14.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.models.orm import AuditEvent, SectorClassificationRow
from app.services.audit.log import AuditLog
from app.services.benchmarking.aggregates import (
    COMPARABLE,
    COMPARABLE_WITH_CAVEATS,
    INDUSTRY_AGGREGATE_METHOD,
    NOT_COMPARABLE,
    PEER_ONLY_FIELDS,
    UNAVAILABLE,
    USAGE,
    BorrowerMetric,
    IndustryAggregateBenchmarker,
    ReferenceDataError,
)
from app.services.benchmarking.classification import (
    ClassificationError,
    CompanyIdentity,
    SectorClassifier,
    benchmarking_permitted,
)
from app.services.benchmarking.peer_cohort import (
    INDUSTRY_AGGREGATE,
    PeerCohortError,
    PeerObservation,
    benchmark_against_cohort,
    build_peer_cohort,
)
from app.services.benchmarking.peers import BENCHMARK_METHOD, PeerBenchmarker
from app.services.metrics.engine import MetricState
from app.services.parameters.engine import ParameterEngine
from app.services.parameters.sector import sector_parameter_definitions
from tests.healthcare_helpers import (
    REPO,
    all_keys,
    by_id,
    imported,
    sector_config,
    sector_content,
    variant_config,
)


from tests.healthcare_helpers import requires_workbook  # noqa: E402

pytestmark = requires_workbook

@pytest.fixture(scope="module")
def dataset():
    return imported().dataset


@pytest.fixture(scope="module")
def config():
    return sector_config()


def _identity(**overrides) -> CompanyIdentity:
    base = dict(
        entity_id="SYK", legal_name="Stryker Corporation", cik="310764", ticker="SYK",
        reported_sic=3841, sic_source="fixture", sic_retrieved_at="2026-10-08",
    )
    base.update(overrides)
    return CompanyIdentity(**base)


def _metric(metric_id, value, *, state="ok", units="ratio", period="2024-12-31"):
    return BorrowerMetric(metric_id=metric_id, state=state, value=value, units=units,
                          period=period, fiscal_year=2024, fact_ids=["f1"],
                          definition={"source": "test"})


def _bench(config, dataset):
    return IndustryAggregateBenchmarker(config, dataset, dataset_version=1)


def _comparison(config, comparison_id):
    return next(c for c in config["comparisons"] if c["comparison_id"] == comparison_id)


# ---------------------------------------------------------------- classification


def test_stryker_is_classified_as_healthcare_products_with_provenance(config, dataset):
    result = SectorClassifier(config, dataset).classify(_identity())
    assert result["status"] == "classified"
    assert result["industry_label"] == "Healthcare Products"
    # Declared (not SEC-verified) identity caps confidence.
    assert result["confidence"] == "medium"
    assert {s["signal"] for s in result["signals"]} == {
        "sic_device_code", "workbook_company_list_industry_group"}
    assert {n["code"] for n in result["review_notes"]} == {
        "identity_not_sec_verified", "no_segment_description_evidence"}
    source = result["classification_source"]
    assert source["sic_retrieved_at"] == "2026-10-08"
    assert source["reference_workbook_sha256"] == dataset["source"]["sha256"]
    assert source["sector_config_hash"] == config.content_hash
    assert benchmarking_permitted(result)


def test_sec_verified_identity_with_device_description_is_high_confidence(config, dataset):
    identity = _identity(
        identity_verification="sec_verified",
        descriptions=[{"evidence_id": "ev1",
                       "text": "A medical technology company selling orthopaedic implants."}],
    )
    result = SectorClassifier(config, dataset).classify(identity)
    assert result["status"] == "classified" and result["confidence"] == "high"
    described = next(s for s in result["signals"]
                     if s["signal"] == "business_description_device_terms")
    assert described["evidence_ids"] == ["ev1"]


def test_sic_code_alone_never_determines_the_sector(config, dataset):
    identity = _identity(entity_id="X", legal_name="Unlisted Devices Inc", ticker="ZZZZ")
    result = SectorClassifier(config, dataset).classify(identity)
    assert result["status"] == "requires_review"
    assert result["industry_label"] is None
    assert "insufficient_corroboration" in {f["code"] for f in result["blocking_flags"]}
    assert not benchmarking_permitted(result)


@pytest.mark.parametrize(
    ("overrides", "flag"),
    [
        ({"cik": None}, "missing_cik"),
        ({"reported_sic": None}, "missing_sic"),
        ({"sic_source": None}, "sic_source_undocumented"),
        ({"reported_sic": 2834}, "conflicting_sic_category"),
        ({"reported_sic": 7372}, "sic_not_a_device_code"),
        ({"reported_sic": 3845}, "sic_mismatch_between_sources"),
    ],
)
def test_unreliable_classification_requires_review(config, dataset, overrides, flag):
    result = SectorClassifier(config, dataset).classify(_identity(**overrides))
    assert result["status"] == "requires_review" and result["industry_label"] is None
    assert flag in {f["code"] for f in result["blocking_flags"]}


def test_diversified_business_is_flagged_without_inventing_an_allocation(config, dataset):
    identity = _identity(descriptions=[
        {"evidence_id": "ev1", "text": "Surgical instruments and medical devices."},
        {"evidence_id": "ev2", "text": "The pharmaceuticals segment sells branded drugs."},
    ])
    result = SectorClassifier(config, dataset).classify(identity)
    assert result["status"] == "requires_review"
    flag = next(f for f in result["blocking_flags"] if f["code"] == "multi_category_activity")
    assert flag["evidence_ids"] == ["ev2"]
    keys = all_keys(result)
    assert not {k for k in keys if "weight" in k or "allocation" in k or "share" in k}


def test_human_override_requires_rationale_and_is_appended_and_audited(
    config, dataset, db_session
):
    classifier = SectorClassifier(config, dataset, session=db_session,
                                  audit=AuditLog(db_session))
    auto = classifier.classify(_identity(reported_sic=None))
    first = classifier.persist(auto, case_id="C1")
    with pytest.raises(ClassificationError, match="rationale"):
        classifier.override(auto, industry_label="Healthcare Products",
                            reviewer="analyst", rationale=" ")
    with pytest.raises(ClassificationError, match="reviewer"):
        classifier.override(auto, industry_label="Healthcare Products",
                            reviewer="", rationale="x")
    with pytest.raises(ClassificationError, match="not a benchmark industry"):
        classifier.override(auto, industry_label="Airlines",
                            reviewer="analyst", rationale="x")
    updated = classifier.override(
        auto, industry_label="Healthcare Products", reviewer="analyst",
        rationale="Confirmed against the 10-K segment note.")
    second = classifier.persist(updated, case_id="C1", supersedes_id=first.id)

    assert updated["status"] == "human_override" and benchmarking_permitted(updated)
    assert updated["human_override"]["previous_status"] == "requires_review"
    rows = db_session.query(SectorClassificationRow).order_by(
        SectorClassificationRow.created_at).all()
    assert {r.id: r.acceptance_state for r in rows} == {
        first.id: "superseded", second.id: "accepted"}
    # The superseded record's content is untouched.
    assert db_session.get(SectorClassificationRow, first.id).payload == auto
    assert second.supersedes_id == first.id
    event = db_session.query(AuditEvent).filter_by(
        event_type="sector_classification_overridden").one()
    assert event.actor_id == "analyst"
    assert event.reason == "Confirmed against the 10-K segment note."
    assert db_session.query(AuditEvent).filter_by(event_type="sector_classified").count() == 1


# ----------------------------------------------------------------- comparability


def test_matching_published_aggregate_is_comparable(config, dataset):
    result = _bench(config, dataset).compare(
        _comparison(config, "operating_margin"), _metric("operating_margin", 0.18))
    assert result["comparison_state"] == COMPARABLE
    assert result["reasons"] == [] and result["caveats"] == []
    assert result["industry_aggregate"]["value_status"] == "source_aggregate"
    assert result["industry_aggregate"]["source"]["cell"] == "E29"


def test_derived_aggregate_is_comparable_with_caveats(config, dataset):
    result = _bench(config, dataset).compare(
        _comparison(config, "net_debt_to_ebitda"),
        _metric("net_debt_to_ebitda", 1.5, units="x"))
    assert result["comparison_state"] == COMPARABLE_WITH_CAVEATS
    assert any("derived" in c for c in result["caveats"])
    assert any(c.startswith("lease_treatment") for c in result["caveats"])
    assert "W-HC-DERIVED" in result["warnings"]


def test_ebitda_coverage_is_not_compared_with_an_ebit_benchmark(config, dataset):
    wrong = {"comparison_id": "mismatched", "borrower_metric": "ebitda_interest_coverage",
             "benchmark_metric": "ebit_interest_coverage"}
    result = _bench(config, dataset).compare(
        wrong, _metric("ebitda_interest_coverage", 12.4, units="x"))
    assert result["comparison_state"] == NOT_COMPARABLE
    assert "numerator_mismatch:borrower=ebitda,benchmark=ebit" in result["reasons"]
    # The incompatible number is withheld, not shown as a benchmark.
    assert result["industry_aggregate"]["value"] is None
    assert result["industry_aggregate"]["value_withheld"] is True
    assert result["difference"] is None and result["position_vs_aggregate"] == "not_assessed"


def test_gross_debt_is_not_compared_with_a_net_debt_benchmark(config, dataset):
    wrong = {"comparison_id": "mismatched", "borrower_metric": "gross_debt_to_ebitda",
             "benchmark_metric": "net_debt_to_ebitda"}
    result = _bench(config, dataset).compare(
        wrong, _metric("gross_debt_to_ebitda", 1.9, units="x"))
    assert result["comparison_state"] == NOT_COMPARABLE
    assert "debt_basis_mismatch:borrower=gross,benchmark=net" in result["reasons"]


@pytest.mark.parametrize(
    ("section", "metric", "dimension", "value", "reason"),
    [
        ("benchmark_metrics", "net_debt_to_ebitda", "lease_treatment",
         "operating_leases_included",
         "lease_treatment_mismatch:borrower=operating_leases_excluded,"
         "benchmark=operating_leases_included"),
        ("borrower_metrics", "net_debt_to_ebitda", "period_basis", "quarterly",
         "period_basis_mismatch:borrower=quarterly,benchmark=annual"),
        ("borrower_metrics", "net_debt_to_ebitda", "ebitda_basis", "company_adjusted",
         "ebitda_basis_mismatch:borrower=company_adjusted,benchmark=unadjusted"),
    ],
)
def test_definitional_incompatibility_blocks_the_comparison(
    section, metric, dimension, value, reason
):
    content = variant_config(
        lambda c: c[section][metric]["descriptor"].update({dimension: value}))
    cfg = sector_config(content)
    data = imported(content=content).dataset
    result = _bench(cfg, data).compare(
        _comparison(cfg, "net_debt_to_ebitda"),
        _metric("net_debt_to_ebitda", 1.5, units="x"))
    assert result["comparison_state"] == NOT_COMPARABLE
    assert reason in result["reasons"]
    assert result["industry_aggregate"]["value"] is None


def test_units_mismatch_blocks_the_comparison():
    content = variant_config(
        lambda c: c["borrower_metrics"]["operating_margin"].update(units="x"))
    cfg = sector_config(content)
    result = _bench(cfg, imported(content=content).dataset).compare(
        _comparison(cfg, "operating_margin"), _metric("operating_margin", 0.18, units="x"))
    assert result["comparison_state"] == NOT_COMPARABLE
    assert "units_mismatch:borrower=x,benchmark=ratio" in result["reasons"]


def test_accounting_basis_difference_is_surfaced_as_a_caveat():
    content = variant_config(
        lambda c: c["borrower_metrics"]["operating_margin"]["descriptor"].update(
            accounting_basis="IFRS"))
    cfg = sector_config(content)
    result = _bench(cfg, imported(content=content).dataset).compare(
        _comparison(cfg, "operating_margin"), _metric("operating_margin", 0.18))
    assert result["comparison_state"] == COMPARABLE_WITH_CAVEATS
    assert any(c.startswith("accounting_basis") for c in result["caveats"])


def test_period_far_from_the_data_vintage_is_caveated(config, dataset):
    result = _bench(config, dataset).compare(
        _comparison(config, "operating_margin"),
        _metric("operating_margin", 0.18, period="2021-12-31"))
    assert result["comparison_state"] == COMPARABLE_WITH_CAVEATS
    assert "W-HC-PERIOD-GAP" in result["warnings"]
    gap = next(c for c in result["comparability_checks"]
               if c["dimension"] == "period_vs_vintage")
    assert gap["gap_months"] == 49 and gap["outcome"] == "caveat"


@pytest.mark.parametrize("state", ["missing_input", "requires_review", "not_meaningful"])
def test_unusable_borrower_metric_makes_the_comparison_unavailable(config, dataset, state):
    result = _bench(config, dataset).compare(
        _comparison(config, "operating_margin"),
        _metric("operating_margin", None, state=state))
    assert result["comparison_state"] == UNAVAILABLE
    assert f"borrower_metric_{state}" in result["reasons"]
    assert result["borrower"]["value"] is None  # never zero
    assert result["industry_aggregate"]["value"] is None
    assert result["difference"] is None


def test_metric_without_an_industry_benchmark_is_unavailable_with_a_reason(config, dataset):
    result = _bench(config, dataset).compare(
        _comparison(config, "revenue_growth"), _metric("revenue_growth", 0.1))
    assert result["comparison_state"] == UNAVAILABLE
    assert result["reasons"] == ["no_industry_benchmark_in_reference_data"]
    assert result["industry_aggregate"] is None


def test_workbook_debt_service_proxy_is_never_compared_with_borrower_dscr(config, dataset):
    for borrower in (_metric("dscr", None, state="missing_input", units="x"),
                     _metric("dscr", 1.8, units="x")):
        result = _bench(config, dataset).compare(_comparison(config, "dscr"), borrower)
        assert result["comparison_state"] == NOT_COMPARABLE
        assert "benchmark_value_assumed" in result["reasons"]
        assert "depends_on_placeholder_assumption:short_term_debt_share" in result["reasons"]
        assert "numerator_mismatch:borrower=cfads,benchmark=ebit_less_cash_taxes" in result["reasons"]
        assert result["industry_aggregate"]["value"] is None
        assert result["industry_aggregate"]["value_status"] == "assumed"
        assert "W-HC-ASSUMED-PRINCIPAL" in result["warnings"]


def test_invalid_benchmark_value_is_excluded_from_comparison():
    from tests.healthcare_helpers import edited_workbook

    data = imported(data=edited_workbook({("Benchmarks", "E29"): "n.m."})).dataset
    result = _bench(sector_config(), data).compare(
        _comparison(sector_config(), "operating_margin"), _metric("operating_margin", 0.18))
    assert result["comparison_state"] == UNAVAILABLE
    assert "benchmark_value_invalid" in result["reasons"]
    assert result["industry_aggregate"]["value"] is None


# ------------------------------------------------------------ Method A (aggregate)


def test_aggregate_result_carries_no_peer_statistics(config, dataset):
    metrics = {
        "operating_margin": _metric("operating_margin", 0.18),
        "net_debt_to_ebitda": _metric("net_debt_to_ebitda", 1.5, units="x"),
    }
    comparisons = _bench(config, dataset).compare_all(metrics)
    assert len(comparisons) == len(config["comparisons"])
    for comparison in comparisons:
        assert comparison["benchmark_method"] == INDUSTRY_AGGREGATE_METHOD
        assert comparison["usage"] == USAGE
        assert not (all_keys(comparison) & PEER_ONLY_FIELDS)
    assert INDUSTRY_AGGREGATE_METHOD != BENCHMARK_METHOD


def test_position_and_direction_follow_the_configured_adverse_direction(config, dataset):
    bench = _bench(config, dataset)
    leverage = _comparison(config, "net_debt_to_ebitda")
    aggregate = bench.compare(leverage, _metric("x", 1.0, units="x"))["industry_aggregate"]["value"]
    low = bench.compare(leverage, _metric("net_debt_to_ebitda", aggregate - 1, units="x"))
    high = bench.compare(leverage, _metric("net_debt_to_ebitda", aggregate + 1, units="x"))
    near = bench.compare(leverage, _metric("net_debt_to_ebitda", aggregate * 1.02, units="x"))
    assert (low["direction_vs_aggregate"], low["position_vs_aggregate"]) == (
        "below", "stronger_than_industry_aggregate")
    assert (high["direction_vs_aggregate"], high["position_vs_aggregate"]) == (
        "above", "weaker_than_industry_aggregate")
    assert near["position_vs_aggregate"] == "in_line_with_industry_aggregate"
    assert low["difference"] == pytest.approx(-1.0)
    margin = bench.compare(_comparison(config, "operating_margin"),
                           _metric("operating_margin", 0.30))
    assert margin["position_vs_aggregate"] == "stronger_than_industry_aggregate"


def test_every_comparison_retains_the_required_provenance(config, dataset):
    result = _bench(config, dataset).compare(
        _comparison(config, "operating_margin"), _metric("operating_margin", 0.18))
    provenance = result["provenance"]
    for key in ("source_workbook_sha256", "data_vintage", "importer_version",
                "benchmark_method", "comparison_engine_version",
                "reference_dataset_hash", "reference_dataset_version",
                "workbook_mapping_hash", "sector_config_version", "sector_config_hash"):
        assert provenance[key] not in (None, "")
    assert provenance["source_workbook_sha256"] == dataset["source"]["sha256"]
    assert result["industry_aggregate"]["source"] == {
        "sheet": "Benchmarks", "cell": "E29", "formula": None,
        "dependencies": {"cells": [], "assumptions": [], "external": []},
        "validation_reasons": []}
    assert result["borrower"]["fact_ids"] == ["f1"]
    assert result["comparison_state"] == COMPARABLE and result["input_hash"]


def test_comparison_identity_is_stable_and_changes_with_the_source(config, dataset):
    from tests.healthcare_helpers import edited_workbook

    comparison = _comparison(config, "operating_margin")
    metric = _metric("operating_margin", 0.18)
    first = _bench(config, dataset).compare(comparison, metric)
    again = _bench(config, dataset).compare(comparison, metric)
    assert first == again
    other = imported(data=edited_workbook({("Benchmarks", "E33"): 2.9})).dataset
    changed = _bench(config, other).compare(comparison, metric)
    assert changed["input_hash"] != first["input_hash"]
    assert changed["provenance"]["source_workbook_sha256"] != \
        first["provenance"]["source_workbook_sha256"]


def test_dataset_without_required_metadata_cannot_be_benchmarked(config, dataset):
    broken = json.loads(json.dumps(dataset))
    broken["vintage"]["data_as_of"] = None
    with pytest.raises(ReferenceDataError, match="vintage.data_as_of"):
        _bench(config, broken)
    other_method = sector_config(variant_config(
        lambda c: c["benchmark"].update(method="empirical_rank_v1")))
    with pytest.raises(ReferenceDataError, match="method"):
        _bench(other_method, dataset)


# ------------------------------------------------------------- Method B (peers)


def _observation(entity, value, **overrides):
    base = dict(
        entity_id=entity, legal_name=f"{entity} Inc", metric_definition_id="net_debt_to_ebitda",
        metric_definition_version=1, value=value, period_end=date(2024, 12, 31),
        period_basis="annual", source={"document_id": f"doc-{entity}", "fact_ids": ["f"]},
    )
    base.update(overrides)
    return PeerObservation(**base)


def _cohort(observations, **kwargs):
    return build_peer_cohort(
        observations, borrower_entity_id="SYK", metric_definition_id="net_debt_to_ebitda",
        metric_definition_version=1, cohort_name="devices", cohort_version=3,
        reference_period_end=date(2024, 12, 31), **kwargs)


def test_empirical_peer_benchmark_still_works_over_a_real_cohort():
    cohort = _cohort([_observation(f"P{i}", float(i)) for i in range(1, 10)])
    result = benchmark_against_cohort(
        PeerBenchmarker(min_sample_for_percentiles=8), cohort,
        metric_name="net_debt_to_ebitda", borrower_value=4.5,
        benchmark_date=date(2024, 12, 31))
    assert result.benchmark_method == BENCHMARK_METHOD == "empirical_rank_v1"
    assert result.sample_size == 9 and result.median == 5.0 and result.rank == 5
    assert result.percentiles_reliable and result.p90 is not None
    definition = result.cohort_definition
    assert [p["entity_id"] for p in definition["peers"]] == [f"P{i}" for i in range(1, 10)]
    assert definition["peers"][0]["source"] == {"document_id": "doc-P1", "fact_ids": ["f"]}
    assert definition["cohort_hash"] and result.cohort_version == 3


def test_small_sample_safeguards_remain_intact():
    cohort = _cohort([_observation(f"P{i}", float(i)) for i in range(1, 5)])
    result = benchmark_against_cohort(
        PeerBenchmarker(min_sample_for_percentiles=8), cohort,
        metric_name="net_debt_to_ebitda", borrower_value=2.0)
    assert result.sample_size == 4
    assert result.p90 is None and result.p95 is None
    assert result.percentiles_reliable is False


def test_borrower_and_duplicates_are_excluded_with_reasons():
    cohort = _cohort([
        _observation("SYK", 1.5),
        _observation("P1", 2.0),
        _observation("P1", 2.1, period_end=date(2024, 12, 30)),
        _observation("P2", None),
        _observation("P3", 3.0, metric_definition_id="gross_debt_to_ebitda"),
        _observation("P4", 3.0, metric_definition_version=2),
        _observation("P5", 3.0, period_basis="ttm"),
        _observation("P6", 3.0, source={}),
        _observation("P7", 3.0, period_end=date(2022, 12, 31)),
        _observation("P8", 4.0),
    ])
    # Of two observations for one entity, the most recent period is kept.
    assert [o.entity_id for o in cohort.included] == ["P1", "P8"]
    assert {e["entity_id"]: e["reason"] for e in cohort.excluded} == {
        "SYK": "subject_borrower",
        "P1": "duplicate_entity",
        "P2": "value_unavailable",
        "P3": "different_metric_definition:gross_debt_to_ebitda",
        "P4": "different_definition_version:2",
        "P5": "different_period_basis:ttm",
        "P6": "missing_source_provenance",
        "P7": "reporting_period_out_of_range",
    }
    result = benchmark_against_cohort(
        PeerBenchmarker(), cohort, metric_name="net_debt_to_ebitda", borrower_value=1.5)
    assert result.raw_peer_values == [2.0, 4.0]


def test_industry_aggregate_can_never_enter_a_peer_cohort(config, dataset):
    aggregate = _bench(config, dataset).compare(
        _comparison(config, "net_debt_to_ebitda"),
        _metric("net_debt_to_ebitda", 1.5, units="x"))["industry_aggregate"]["value"]
    fake_peer = _observation("HEALTHCARE_PRODUCTS", aggregate, record_type=INDUSTRY_AGGREGATE)
    with pytest.raises(PeerCohortError, match="never treated as peers"):
        _cohort([_observation("P1", 2.0), fake_peer])


# --------------------------------------------------------- sector formulas


@pytest.mark.parametrize(
    ("inputs", "state", "value"),
    [
        ({"numerator": 6000.0, "denominator": 3230.0}, MetricState.OK, 1.857585),
        ({"numerator": 6000.0, "denominator": -250.0}, MetricState.NOT_MEANINGFUL, None),
        ({"numerator": 6000.0, "denominator": 0.0}, MetricState.REQUIRES_REVIEW, None),
        ({"numerator": 6000.0, "denominator": 0.5}, MetricState.REQUIRES_REVIEW, None),
        ({"numerator": 6000.0, "denominator": None}, MetricState.MISSING_INPUT, None),
        ({"numerator": None, "denominator": 3230.0}, MetricState.MISSING_INPUT, None),
    ],
)
def test_guarded_ratio_never_reports_a_misleading_number(inputs, state, value):
    computation = ParameterEngine().compute(
        "gross_debt_to_ebitda", inputs, analysis_run_id="t")
    assert computation.state is state
    assert computation.result.value == value
    assert computation.result.method.value == "deterministic"
    assert computation.result.formula_id == "positive_denominator_ratio"


def test_sector_formula_definitions():
    engine = ParameterEngine()

    def value(parameter_id, **inputs):
        return engine.compute(parameter_id, inputs, analysis_run_id="t").result.value

    assert value("ebit_interest_coverage", numerator=2450, denominator=260) == pytest.approx(9.423077)
    assert value("ebitda_margin", numerator=3230, denominator=13400) == pytest.approx(0.241045)
    assert value("dso", balance=2350, flow=13400) == pytest.approx(64.011194)
    assert value("dio", balance=2850, flow=5000) == pytest.approx(208.05)
    assert value("dpo", balance=850, flow=5000) == pytest.approx(62.05)
    assert value("cash_conversion_cycle", dso=64.011194, dio=208.05, dpo=62.05) == \
        pytest.approx(210.011194)
    assert value("revenue_cagr", begin=10000, end=13400, periods=3) == pytest.approx(0.102474)
    # A negative EBIT yields a negative coverage (the denominator is valid).
    assert value("ebit_interest_coverage", numerator=-100, denominator=260) < 0


def test_dscr_stays_unavailable_without_cfads_and_contractual_debt_service():
    engine = ParameterEngine()
    for inputs in ({"available": None, "required": None},
                   {"available": 2400.0, "required": None},
                   {"available": None, "required": 600.0}):
        computation = engine.compute("dscr", inputs, analysis_run_id="t")
        assert computation.state is MetricState.MISSING_INPUT
        assert computation.result.value is None
        assert computation.result.status.value == "unavailable"
    # The borrower DSCR is defined on CFADS and contractual debt service only.
    spec = sector_content()["borrower_metrics"]["dscr"]
    assert spec["inputs"] == {"available": ["cfads"], "required": ["contractual_debt_service"]}
    assert "short_term_debt_share" not in json.dumps(spec)


def test_sector_parameters_cannot_reach_official_scoring():
    scoring = json.loads((REPO / "config" / "poc" / "scoring.json").read_text(encoding="utf-8"))
    scored = (set(scoring["parameter_bands"]) | set(scoring["financial_weights"])
              | set(scoring["business_weights"]) | set(scoring["structure_protection_weights"]))
    sector_ids = {d.parameter_id for d in sector_parameter_definitions()}
    assert not (sector_ids & scored)
    assert not {s for s in scored if s.startswith("sector_benchmark.")}
    # Existing synthetic peer fixture is preserved.
    peers = json.loads((REPO / "config" / "poc" / "peers.json").read_text(encoding="utf-8"))
    assert peers["entities"] == ["PEER_A", "PEER_B"] and peers["synthetic"] is True


def test_configured_comparisons_cover_the_handoff_core_metrics(config):
    ids = set(by_id(config["comparisons"]))
    assert {"net_debt_to_ebitda", "gross_debt_to_ebitda", "ebit_interest_coverage",
            "ebitda_interest_coverage", "cfo_to_ebitda", "operating_margin",
            "ebitda_margin", "fcf_to_revenue", "capex_to_revenue", "liquidity",
            "dso", "dio", "dpo", "cash_conversion_cycle", "rnd_to_revenue",
            "cash_tax_burden", "revenue_growth", "revenue_cagr", "free_cash_flow",
            "dscr"} <= ids
