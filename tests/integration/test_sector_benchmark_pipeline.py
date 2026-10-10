"""Sector benchmarking through the real pipeline (handoff sections 5-13, 14).

Runs the offline Stryker FIXTURE (synthetic values, clearly labelled) through
ingestion, the evidence snapshot, deterministic metrics, classification,
industry-aggregate comparison, the agentic layer (fake backends only), the memo
and human-gated finalization.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.core.config_registry import ARTIFACT_KINDS
from app.models.orm import (
    AgenticAnalysisRun,
    AuditEvent,
    Benchmark,
    Document,
    Escalation,
    Fact,
    IndustryBenchmarkComparisonRow,
    ParameterResultRow,
    RiskScoreRow,
    Snapshot,
    TopicConclusionRow,
)
from app.schemas.agentic import ScoreKind
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.audit.log import AuditLog
from app.services.benchmarking.aggregates import PEER_ONLY_FIELDS, StaleBenchmarkError
from app.services.llm.client import LLMRawResult
from app.services.orchestration.benchmark_context import (
    BenchmarkContextError,
    validate_benchmark_context,
    validate_benchmark_narrative,
)
from app.services.orchestration.business import OrchestratorInputError
from app.services.orchestration.financial import build_financial_orchestrator_input
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.pipeline.sector import SectorBenchmarkError
from app.services.pipeline.sector_demo import (
    ScriptedBenchmarkBackend,
    scripted_financial_conclusion,
)
from app.services.reporting.memo import MemoReportGenerator
from app.services.review.workflow import HumanReviewWorkflow
from app.services.scoring.engine import ScoringConfig, ScoringEngine
from app.services.workbench.agentic import AgenticWorkbenchService
from tests.healthcare_helpers import (
    WORKBOOK_SHA256,
    all_keys,
    by_id,
    edited_workbook,
    load_package,
    new_session,
    run_fixture,
    strip_run_identity,
    variant_package,
)

from tests.healthcare_helpers import requires_workbook  # noqa: E402

pytestmark = requires_workbook

CASE = "SYK_FIXTURE_CASE"


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    return run_fixture(tmp_path_factory.mktemp("legacy"))


@pytest.fixture(scope="module")
def agentic(tmp_path_factory):
    backend = RecordingBackend()
    session, result = run_fixture(
        tmp_path_factory.mktemp("agentic"), mode="agentic", backend=backend)
    return session, result, backend


class RecordingBackend(ScriptedBenchmarkBackend):
    """The scripted double, also recording every request it receives."""

    def __init__(self):
        super().__init__()
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


class TamperingBackend(ScriptedBenchmarkBackend):
    """Applies ``mutate`` to the scripted financial conclusion before returning it."""

    def __init__(self, mutate):
        super().__init__()
        self._mutate = mutate

    def generate(self, request):
        if request.method == "orchestrate" and "benchmark_context" in request.inputs:
            answer = scripted_financial_conclusion(request.inputs)
            self._mutate(answer, request.inputs)
            return LLMRawResult(raw_response=json.dumps(answer), model_id="tamper",
                                model_config={"backend": "tamper"})
        return super().generate(request)


class ObservationBackend(ScriptedBenchmarkBackend):
    """Returns sector observations from one narrow agent."""

    def __init__(self, agent, observations):
        super().__init__()
        self._agent, self._observations = agent, observations

    def generate(self, request):
        if request.method == self._agent:
            observations = [
                {**o, "evidence_ids": (request.evidence_ids[:1]
                                       if o.get("evidence_ids") == "ADMITTED"
                                       else o.get("evidence_ids", []))}
                for o in self._observations
            ]
            return LLMRawResult(
                raw_response=json.dumps({"parameters": [],
                                         "sector_observations": observations}),
                model_id="obs", model_config={"backend": "obs"})
        return super().generate(request)


def _bench(result):
    return result.industry_benchmarking


# ------------------------------------------------------------ deterministic stage


def test_fixture_runs_end_to_end_and_selects_healthcare_products(legacy):
    session, result = legacy
    bench = _bench(result)
    assert bench["status"] == "completed"
    assert bench["industry_label"] == "Healthcare Products"
    assert bench["benchmark_method"] == "industry_aggregate_damodaran_v1"
    assert bench["classification"]["status"] == "classified"
    assert bench["classification"]["identity"]["cik"] == "310764"
    assert bench["classification"]["identity"]["identity_verification"] == "declared_unverified"
    assert bench["summary"] == {"comparable": 3, "comparable_with_caveats": 10,
                                "not_comparable": 4, "unavailable": 6}
    assert bench["as_of_period"] == {"fiscal_year": 2024, "period_end": "2024-12-31"}
    assert [w["fiscal_year"] for w in bench["history_window"]] == [2021, 2022, 2023, 2024]
    assert bench["affects"] == {"narrative": True, "official_scores": False}
    # Draft only; human review remains mandatory.
    assert result.draft_snapshot.finalized is False
    assert result.memo_json["final_status"] == "draft"


def test_borrower_metrics_use_the_reviewed_evidence_and_correct_formulas(legacy):
    comparisons = by_id(_bench(legacy[1])["comparisons"])
    expected = {
        "net_debt_to_ebitda": (6000 - 1100) / (2450 + 780),
        "gross_debt_to_ebitda": 6000 / 3230,
        "ebit_interest_coverage": 2450 / 260,
        "ebitda_interest_coverage": 3230 / 260,
        "cfo_to_ebitda": 2400 / 3230,
        "operating_margin": 2450 / 13400,
        "ebitda_margin": 3230 / 13400,
        "gross_margin": 8400 / 13400,
        "fcf_to_revenue": (2400 - 520) / 13400,
        "capex_to_revenue": 520 / 13400,
        "rnd_to_revenue": 880 / 13400,
        "cash_tax_burden": 390 / 2190,
        "dso": 2350 / 13400 * 365,
        "dio": 2850 / 5000 * 365,
        "dpo": 850 / 5000 * 365,
        "cash_conversion_cycle": 2350 / 13400 * 365 + 2850 / 5000 * 365 - 850 / 5000 * 365,
        "revenue_growth": 13400 / 12100 - 1,
        "revenue_cagr": (13400 / 10000) ** (1 / 3) - 1,
        "free_cash_flow": 1880.0,
        "liquidity": 2600.0,
        "goodwill_intangibles_to_total_assets": (6600 + 2600) / 20500,
        "acquisitions_to_cfo": 1800 / 2400,
    }
    for metric, value in expected.items():
        borrower = comparisons[metric]["borrower"]
        assert borrower["state"] == "ok", metric
        assert borrower["value"] == pytest.approx(value, abs=1e-5), metric
        assert borrower["fact_ids"], metric
        assert borrower["period"] == "2024-12-31"
    # Every cited fact is a persisted fact of this case.
    session = legacy[0]
    cited = {f for c in comparisons.values() for f in c["borrower"]["fact_ids"]}
    assert cited and all(session.get(Fact, f) is not None for f in cited)


def test_dscr_is_unavailable_and_never_fabricated(legacy):
    dscr = by_id(_bench(legacy[1])["comparisons"])["dscr"]
    assert dscr["borrower"]["state"] == "missing_input"
    assert dscr["borrower"]["value"] is None
    assert dscr["comparison_state"] == "not_comparable"
    assert dscr["industry_aggregate"]["value"] is None
    assert dscr["industry_aggregate"]["value_withheld"] is True
    assert dscr["difference"] is None
    assert "borrower_metric_missing_input" in dscr["reasons"]
    assert "depends_on_placeholder_assumption:short_term_debt_share" in dscr["reasons"]


def test_incompatible_metrics_are_blocked_with_reasons(legacy):
    comparisons = by_id(_bench(legacy[1])["comparisons"])
    for blocked in ("fcf_to_revenue", "capex_to_revenue", "rnd_to_revenue", "dscr"):
        comparison = comparisons[blocked]
        assert comparison["comparison_state"] == "not_comparable"
        assert comparison["reasons"]
        assert comparison["industry_aggregate"]["value"] is None
    for missing in ("revenue_growth", "revenue_cagr", "liquidity", "free_cash_flow"):
        assert comparisons[missing]["comparison_state"] == "unavailable"
        assert comparisons[missing]["reasons"] == ["no_industry_benchmark_in_reference_data"]


def test_position_versus_aggregate_and_own_history_are_distinguished(legacy):
    leverage = by_id(_bench(legacy[1])["comparisons"])["net_debt_to_ebitda"]
    assert leverage["position_vs_aggregate"] == "stronger_than_industry_aggregate"
    assert leverage["direction_vs_aggregate"] == "below"
    history = leverage["own_history"]
    assert history["direction"] == "deteriorating"
    assert history["observed_years"] == [2021, 2022, 2023, 2024]
    assert [p["fiscal_year"] for p in history["series"]] == [2021, 2022, 2023, 2024]
    assert history["series"][0]["value"] == pytest.approx(1500 / 2500)
    assert leverage["comparison_state"] == "comparable_with_caveats"


def test_no_percentile_or_rank_exists_for_an_aggregate(legacy):
    session, result = legacy
    bench = _bench(result)
    for comparison in bench["comparisons"]:
        assert not (all_keys(comparison) & PEER_ONLY_FIELDS)
    # The empirical peer table is untouched by the aggregate method...
    assert {b.benchmark_method for b in session.query(Benchmark).all()} == {"empirical_rank_v1"}
    # ...and with no company-level peers, it reports an empty cohort (no fake peers).
    assert all(b.sample_size == 0 and b.raw_peer_values == [] and b.p90 is None
               for b in session.query(Benchmark).all())
    html = MemoReportGenerator(session).render_html(result.memo_json)
    section = html.split('data-subsection="industry_benchmarking"')[1].split("</div>")[0]
    assert "percentile" in section and "none is shown" in section
    assert "No percentile, rank or peer distribution exists" in section


def test_workbook_is_reference_data_not_borrower_evidence(legacy):
    session, result = legacy
    documents = session.query(Document).filter_by(case_id=CASE).all()
    assert [d.filename for d in documents] == ["financials.csv"]
    assert WORKBOOK_SHA256 not in result.admitted_source_hashes
    evidence = result.evidence_snapshot
    assert len(evidence.documents) == 1
    assert "industry_reference_dataset.medical_devices" not in evidence.config_versions
    assert evidence.config_versions["sector_benchmark.medical_devices"] == 1
    draft = result.draft_snapshot.payload
    assert draft["config_versions"]["industry_reference_dataset.medical_devices"] == 1
    assert set(draft["config_versions"]) == set(ARTIFACT_KINDS) | {
        "sector_benchmark.medical_devices", "industry_reference_dataset.medical_devices"}
    # Empirical benchmarks dict on the snapshot stays empirical-only.
    assert "industry_benchmarking" not in draft["benchmarks"]
    assert draft["financial_analysis"]["industry_benchmarking"]["status"] == "completed"


def test_comparisons_are_persisted_with_full_lineage_and_audited(legacy):
    session, result = legacy
    bench = _bench(result)
    rows = session.query(IndustryBenchmarkComparisonRow).filter_by(case_id=CASE).all()
    assert len(rows) == 23 == len(bench["comparison_row_ids"])
    for row in rows:
        assert row.acceptance_state == "accepted" and row.analysis_run_id is None
        assert row.source_sha256 == WORKBOOK_SHA256
        assert row.dataset_hash == bench["reference"]["reference_dataset_hash"]
        assert row.sector_config_hash == bench["sector"]["config_hash"]
        assert row.snapshot_version == result.evidence_snapshot.snapshot_version
        assert row.payload["input_hash"] == row.input_hash
    events = {e.event_type for e in session.query(AuditEvent).filter_by(case_id=CASE)}
    assert {"reference_dataset_imported", "sector_classified",
            "industry_benchmark_compared"} <= events
    imported_event = session.query(AuditEvent).filter_by(
        event_type="reference_dataset_imported").one()
    assert imported_event.after["source_sha256"] == WORKBOOK_SHA256


def test_memo_shows_comparisons_limitations_missing_data_and_lineage(legacy):
    session, result = legacy
    html = MemoReportGenerator(session).render_html(result.memo_json)
    assert "<h3>Healthcare Industry Benchmarking</h3>" in html
    assert 'data-field="benchmark_industry">Healthcare Products<' in html
    assert 'data-field="benchmark_vintage">2026-01<' in html
    assert 'data-field="benchmark_method">industry_aggregate_damodaran_v1' in html
    assert f'data-field="benchmark_workbook_sha256">{WORKBOOK_SHA256}<' in html
    assert 'data-comparison="net_debt_to_ebitda" data-comparison-state="comparable_with_caveats"' in html
    assert 'data-borrower-metric="dscr">missing_input<' in html
    assert 'data-industry-aggregate="dscr">not compared<' in html
    assert 'data-industry-aggregate="revenue_growth">not available<' in html
    assert "They are not medians, percentiles or peer observations." in html
    assert "does not set or alter any official risk score" in html
    # Every aggregate shown in the memo is a value present in the memo JSON.
    comparisons = by_id(_bench(result)["comparisons"])
    value = comparisons["gross_debt_to_ebitda"]["industry_aggregate"]["value"]
    assert f'data-industry-aggregate="gross_debt_to_ebitda">{value} (source_aggregate)' in html
    # Rendering is deterministic.
    assert html == MemoReportGenerator(session).render_html(result.memo_json)


def test_reference_workbook_hash_mismatch_is_rejected(tmp_path):
    package = load_package(reference_workbook_sha256="0" * 64)
    with pytest.raises(SectorBenchmarkError, match="hash does not match"):
        run_fixture(tmp_path, package=package)


# ------------------------------------------------------- missing-data behaviour


def test_negative_ebitda_is_not_meaningful_and_never_zero(tmp_path):
    package = variant_package(tmp_path, csv_edit={
        "Operating Income": "Operating Income,1900,2050,2250,-1500"})
    _, result = run_fixture(tmp_path, package=package)
    comparisons = by_id(_bench(result)["comparisons"])
    gross = comparisons["gross_debt_to_ebitda"]
    assert gross["borrower"]["state"] == "not_meaningful"
    assert gross["borrower"]["value"] is None
    assert gross["comparison_state"] == "unavailable"
    assert "borrower_metric_not_meaningful" in gross["reasons"]
    net = comparisons["net_debt_to_ebitda"]  # existing MetricEngine guard
    assert net["borrower"]["state"] == "requires_review" and net["borrower"]["value"] is None
    assert comparisons["ebitda_margin"]["borrower"]["value"] == pytest.approx(-720 / 13400, abs=1e-5)
    assert all(c["borrower"]["value"] != 0 for c in comparisons.values())


def test_missing_inputs_propagate_without_being_coerced(tmp_path):
    package = variant_package(tmp_path, csv_edit={
        "Inventories": None,
        "Interest Expense": "Interest Expense,150,170,200,0",
        "Research, development and engineering expenses": None,
    })
    _, result = run_fixture(tmp_path, package=package)
    comparisons = by_id(_bench(result)["comparisons"])
    assert comparisons["dio"]["borrower"]["state"] == "missing_input"
    assert "inventory" in comparisons["dio"]["borrower"]["detail"]
    ccc = comparisons["cash_conversion_cycle"]["borrower"]
    assert ccc["state"] == "missing_input" and ccc["value"] is None
    assert comparisons["rnd_to_revenue"]["borrower"]["state"] == "missing_input"
    # A zero denominator needs review; it is not a ratio.
    coverage = comparisons["ebit_interest_coverage"]
    assert coverage["borrower"]["state"] == "requires_review"
    assert coverage["borrower"]["value"] is None
    assert coverage["comparison_state"] == "unavailable"
    # Unaffected metrics are still computed.
    assert comparisons["dso"]["borrower"]["state"] == "ok"


def test_short_history_makes_multi_year_growth_unavailable(tmp_path):
    package = variant_package(tmp_path, csv_edit={
        "Net sales": "Net sales,,,12100,13400"})
    _, result = run_fixture(tmp_path, package=package)
    comparisons = by_id(_bench(result)["comparisons"])
    cagr = comparisons["revenue_cagr"]["borrower"]
    assert cagr["state"] == "missing_input" and "found [2023, 2024]" in cagr["detail"]
    assert comparisons["revenue_growth"]["borrower"]["state"] == "ok"


def test_unreliable_classification_stops_benchmarking_until_a_human_overrides(tmp_path):
    session = new_session()
    package = variant_package(tmp_path, identity={"reported_sic": 2834})
    _, result = run_fixture(tmp_path, session=session, package=package)
    bench = _bench(result)
    assert bench["status"] == "classification_requires_review"
    assert bench["comparisons"] == [] and bench["industry_label"] is None
    assert session.query(IndustryBenchmarkComparisonRow).count() == 0
    reasons = [e.reason for e in session.query(Escalation).all()]
    assert any("Sector classification requires review" in r for r in reasons)
    html = MemoReportGenerator(session).render_html(result.memo_json)
    assert "sector classification requires human review" in html

    # A human override (with rationale) is recorded; the re-run then benchmarks.
    from app.cli import _classify_override
    from types import SimpleNamespace

    out = _classify_override(session, SimpleNamespace(
        case_id=CASE, sector="medical_devices", industry="Healthcare Products",
        reviewer="credit.officer", rationale="Reviewed the segment note; devices only."))
    assert out["status"] == "human_override"
    _, rerun = run_fixture(tmp_path, session=session, package=package)
    assert _bench(rerun)["status"] == "completed"
    assert _bench(rerun)["classification"]["status"] == "human_override"
    assert _bench(rerun)["classification"]["human_override"]["reviewer"] == "credit.officer"


# ------------------------------------------------------------------ agentic


def test_benchmark_context_is_bounded_and_holds_only_validated_values(agentic):
    _, result, backend = agentic
    request = next(r for r in backend.requests if r.method == "orchestrate")
    assert set(request.inputs) == {
        "topic", "parameters", "score", "stress_results", "covenant_results",
        "open_conflicts", "data_limitations", "benchmark_context"}
    context = request.inputs["benchmark_context"]
    validate_benchmark_context(context)
    keys = all_keys(context)
    assert not (keys & {"workbook", "cells", "companies", "sic_codes", "formula", "metrics"})
    assert not (keys & PEER_ONLY_FIELDS)
    assert len(json.dumps(context)) < 60_000
    states = {c["comparison_id"]: c["comparison_state"] for c in context["comparisons"]}
    for entry in context["comparisons"]:
        usable = states[entry["comparison_id"]] in ("comparable", "comparable_with_caveats")
        assert (entry["industry_aggregate"] is not None) == usable
        assert (entry["difference"] is not None) == usable
        if usable:
            assert entry["industry_aggregate"]["value_status"] in ("source_aggregate", "derived")
    assert context["source_workbook_sha256"] == WORKBOOK_SHA256
    assert context["usage"] == "contextual_only_not_a_credit_threshold"
    # Narrow agents never receive the benchmark context or the workbook.
    for other in backend.requests:
        if other.method != "orchestrate":
            assert "benchmark_context" not in other.inputs


def test_every_context_number_is_an_accepted_deterministic_result(agentic):
    session, result, backend = agentic
    run = session.query(AgenticAnalysisRun).one()
    rows = {r.parameter_result_id: r for r in session.query(ParameterResultRow).all()}
    assert all(r.analysis_run_id == run.analysis_run_id for r in rows.values())
    context = next(r for r in backend.requests if r.method == "orchestrate").inputs[
        "benchmark_context"]
    checked = 0
    for entry in context["comparisons"]:
        for part in ("borrower", "industry_aggregate", "difference"):
            node = entry[part]
            if not node or node["parameter_result_id"] is None:
                continue
            row = rows[node["parameter_result_id"]]
            assert row.method == "deterministic" and row.acceptance_state == "accepted"
            assert row.agent_id is None and row.agent_run_id is None
            assert row.parameter_id.startswith("sector_benchmark.")
            assert row.value["v"] == node["value"]
            checked += 1
    assert checked == 23 + 13 + 13
    aggregate = next(r for r in rows.values()
                     if r.parameter_id == "sector_benchmark.industry_aggregate.gross_debt_to_ebitda")
    assert aggregate.evidence_ids == [f"refdata:{WORKBOOK_SHA256[:16]}:Benchmarks!E33"]
    assert aggregate.formula_id == "industry_aggregate_damodaran_v1"
    borrower = next(r for r in rows.values()
                    if r.parameter_id == "sector_benchmark.borrower.gross_debt_to_ebitda")
    assert borrower.source_fact_ids and all(session.get(Fact, f) for f in borrower.source_fact_ids)


def test_validated_interpretation_is_promoted_with_provenance(agentic):
    session, result, _ = agentic
    interpretation = _bench(result)["agentic_interpretation"]
    assert interpretation["orchestrator_outcome"]["status"] == "accepted"
    assert interpretation["orchestrator_outcome"]["issues"] == []
    rows = {r.parameter_result_id: r.value["v"] for r in session.query(ParameterResultRow)}
    claims = interpretation["narrative_claims"]
    assert len(claims) == 6
    for claim in claims:
        assert claim["parameter_result_ids"] and claim["evidence_ids"]
        assert set(claim["parameter_result_ids"]) <= set(rows)
        for quote in claim["quoted_values"]:
            assert rows[quote["parameter_result_id"]] == quote["value"]
    leverage = next(c for c in claims if c["claim_id"] == "bench_net_debt_to_ebitda")
    assert "1.52x" in leverage["text"] and "2.03x" in leverage["text"]
    assert "deteriorating" in leverage["text"]
    assert any("net debt/EBITDA" in t for t in interpretation["unresolved_contradictions"])
    conclusion = session.query(TopicConclusionRow).filter_by(
        topic="financial", acceptance_state="accepted").one()
    assert conclusion.payload["overall_assessment"]["claim_id"] == "bench_overall"
    assert conclusion.orchestrator_run_id == interpretation["orchestrator_outcome"]["agent_run_id"]
    html = MemoReportGenerator(session).render_html(result.memo_json)
    assert 'data-claim="industry_benchmarking.strength"' in html
    assert result.memo_json["agentic"]["industry_benchmark"]["affects_official_scores"] is False
    assert result.memo_json["agentic"]["accepted_analysis_run_id"] == \
        _bench(result)["analysis_run_id"]


def test_official_scores_are_identical_with_and_without_the_benchmark(agentic, tmp_path):
    session, result, _ = agentic
    package = load_package()
    package.sector_benchmark = None
    plain_session, plain = run_fixture(
        tmp_path, mode="agentic", backend=ScriptedBenchmarkBackend(), package=package)
    assert plain.industry_benchmarking is None

    def scores(s):
        return {r.kind: (r.status, r.band, r.coverage_weight,
                         sorted(r.contributing_parameter_ids) if r.kind != "obligor" else None,
                         sorted(r.missing_required_parameter_ids), r.method)
                for r in s.query(RiskScoreRow).all()}

    assert scores(session) == scores(plain_session)
    for row in session.query(RiskScoreRow).all():
        assert row.method == "deterministic"
        assert not any(str(p).startswith("sector_benchmark.")
                       for p in row.contributing_parameter_ids)
    # Even handed to the scoring engine directly, benchmark results score nothing.
    from app.core.config_registry import ConfigRegistry
    from app.services.orchestration.benchmark_context import build_benchmark_parameter_results

    engine = ScoringEngine(ScoringConfig.from_registry(ConfigRegistry(session)))
    payload = copy.deepcopy(_bench(result))
    benchmark_results = build_benchmark_parameter_results(payload, analysis_run_id="x")
    score = engine.score_financial(benchmark_results, analysis_run_id="x")
    assert score.kind is ScoreKind.FINANCIAL
    assert score.band is None and score.contributing_parameter_ids == []


def _reject(tmp_path, mutate):
    session, result = run_fixture(tmp_path, mode="agentic", backend=TamperingBackend(mutate))
    return session, result, _bench(result)["agentic_interpretation"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("invented_number", "numeric_correspondence"),
     ("altered_number", "numeric_correspondence"),
     ("computed_number", "unquoted_number"),
     ("score", "official_score_assignment"),
     ("unknown_evidence", "evidence_exists"),
     ("no_provenance", "provenance")],
)
def test_unsupported_orchestrator_claims_are_not_promoted(tmp_path, name, expected):
    def mutate(answer, inputs):
        claim = answer["strengths"][0]
        if name == "invented_number":
            claim["quoted_values"].append({"parameter_result_id": "pr_invented", "value": 3.0})
        elif name == "altered_number":
            claim["quoted_values"][0]["value"] += 0.5
        elif name == "computed_number":
            claim["text"] += " That is 25.3% better than the industry."
        elif name == "score":
            answer["risk_band"] = 1
        elif name == "unknown_evidence":
            claim["evidence_ids"] = ["doc_that_was_never_admitted"]
        else:
            answer["overall_assessment"]["parameter_result_ids"] = []
            answer["overall_assessment"]["evidence_ids"] = []

    session, result, interpretation = _reject(tmp_path, mutate)
    outcome = interpretation["orchestrator_outcome"]
    assert outcome["status"] == "rejected"
    assert any(expected in issue for issue in outcome["issues"]), outcome["issues"]
    assert interpretation["narrative_claims"] == []
    # The deterministic default conclusion stands; nothing from the answer is kept.
    conclusion = session.query(TopicConclusionRow).filter_by(topic="financial").one()
    assert conclusion.payload["overall_assessment"]["claim_id"] == "cl_financial"
    html = MemoReportGenerator(session).render_html(result.memo_json)
    assert 'data-claim="industry_benchmarking.' not in html
    assert "25.3%" not in html
    # The deterministic comparison table is unaffected by the rejected narrative.
    assert _bench(result)["summary"]["comparable"] == 3


def test_narrative_guard_accepts_only_quoted_numbers():
    claim = {"claim_id": "c", "text": "Leverage of 1.52x versus 2.03x over FY2021-FY2024.",
             "quoted_values": [{"parameter_result_id": "a", "value": 1.5170278},
                               {"parameter_result_id": "b", "value": 2.0313137}]}
    assert validate_benchmark_narrative({"overall_assessment": claim}) == []
    claim["text"] += " Coverage improved by 3.5x and margin by 12%."
    issues = validate_benchmark_narrative({"overall_assessment": claim})
    assert len(issues) == 2 and all(i.startswith("unquoted_number") for i in issues)
    assert validate_benchmark_narrative(
        {"overall_assessment": {"claim_id": "c", "text": "ok"},
         "strengths": [{"claim_id": "s", "text": "x", "rating": "A"}]})[0].startswith(
            "official_score_assignment")


def test_bounded_context_contract_is_enforced(agentic):
    session, result, backend = agentic
    context = next(r for r in backend.requests if r.method == "orchestrate").inputs[
        "benchmark_context"]
    leaked = {**context, "workbook": {"Benchmarks": {}}}
    with pytest.raises(BenchmarkContextError, match="Unexpected"):
        validate_benchmark_context(leaked)
    exposed = copy.deepcopy(context)
    blocked = next(c for c in exposed["comparisons"] if c["comparison_id"] == "dscr")
    blocked["industry_aggregate"] = {"parameter_result_id": "p", "value": 1.9,
                                     "value_status": "assumed"}
    with pytest.raises(BenchmarkContextError, match="exposes an aggregate"):
        validate_benchmark_context(exposed)
    nested = copy.deepcopy(context)
    nested["classification"]["cells"] = ["E3"]
    with pytest.raises(BenchmarkContextError, match="raw reference data"):
        validate_benchmark_context(nested)
    # Ids that are not accepted Financial parameters of the run are refused.
    engine = ScoringEngine(ScoringConfig.from_registry(
        __import__("app.core.config_registry", fromlist=["ConfigRegistry"]).ConfigRegistry(session)))
    score = engine.score_financial([], analysis_run_id="r")
    with pytest.raises(OrchestratorInputError, match="not an accepted Financial"):
        build_financial_orchestrator_input(
            analysis_run_id="r", parameters=[], financial_score=score,
            benchmark_context=context)
    # Without a benchmark the orchestrator input has no such key at all.
    plain = build_financial_orchestrator_input(
        analysis_run_id="r", parameters=[], financial_score=score).as_prompt_inputs()
    assert "benchmark_context" not in plain


def test_existing_narrow_agents_receive_only_their_sector_dimensions(agentic):
    _, _, backend = agentic
    by_method = {r.method: r for r in backend.requests}
    assert len(backend.requests) == 16  # 15 existing narrow agents + the orchestrator
    regulatory = by_method["regulatory_material_events"].inputs["sector_context"]
    assert {d["dimension_id"] for d in regulatory["risk_dimensions"]} == {
        "regulatory_approval_exposure", "product_recalls_adverse_events",
        "product_liability_litigation", "reimbursement_change", "quality_system_compliance"}
    competition = by_method["competition_pricing"].inputs["sector_context"]
    assert {d["dimension_id"] for d in competition["risk_dimensions"]} == {
        "patent_ip_exposure", "physician_adoption_switching_costs", "pricing_pressure"}
    # Agents with no assigned dimension get no sector context.
    assert "sector_context" not in by_method["management_governance"].inputs
    assert "sector_context" not in by_method["covenant_extraction"].inputs


def test_sector_observations_are_evidence_backed_and_not_scores(tmp_path):
    observations = [
        {"dimension_id": "product_recalls_adverse_events", "relevance": "relevant",
         "direction": "adverse", "confidence": "medium", "evidence_ids": "ADMITTED",
         "review_flag": True, "note": "Recall disclosed."},
        {"dimension_id": "reimbursement_change", "relevance": "not_assessable"},
    ]
    session, result = run_fixture(
        tmp_path / "ok", mode="agentic",
        backend=ObservationBackend("regulatory_material_events", observations))
    interpretation = _bench(result)["agentic_interpretation"]
    accepted = interpretation["qualitative_observations"]
    assert [o["dimension_id"] for o in accepted] == [
        "product_recalls_adverse_events", "reimbursement_change"]
    assert accepted[0]["evidence_ids"] and accepted[0]["agent_id"] == "regulatory_material_events"
    assert interpretation["rejected_qualitative_observations"] == []
    html = MemoReportGenerator(session).render_html(result.memo_json)
    assert "Sector qualitative observations (not scores)" in html
    # Observations create no parameters and move no score.
    assert not [r for r in session.query(ParameterResultRow)
                if r.agent_id == "regulatory_material_events"]

    for bad, message in (
        ({"dimension_id": "pricing_pressure", "relevance": "not_relevant"},
         "not a dimension assigned"),
        ({"dimension_id": "product_recalls_adverse_events", "relevance": "relevant"},
         "cites no evidence"),
        ({"dimension_id": "product_recalls_adverse_events", "relevance": "relevant",
          "evidence_ids": ["never_admitted"]}, "unadmitted evidence"),
        ({"dimension_id": "product_recalls_adverse_events", "relevance": "not_relevant",
          "risk_band": 4}, "observations are not scores"),
    ):
        _, rejected = run_fixture(
            tmp_path / message.replace(" ", "_"), mode="agentic",
            backend=ObservationBackend("regulatory_material_events", [observations[1], bad]))
        interpretation = _bench(rejected)["agentic_interpretation"]
        # All-or-nothing: the valid sibling is not retained either.
        assert interpretation["qualitative_observations"] == []
        issues = interpretation["rejected_qualitative_observations"][0]["issues"]
        assert any(message in i for i in issues), issues


def test_workbench_exposes_comparisons_for_the_run(agentic):
    session, result, _ = agentic
    run_id = _bench(result)["analysis_run_id"]
    view = AgenticWorkbenchService(session).assemble(run_id).to_dict()
    rows = view["sections"]["industry_benchmarks"]
    assert len(rows) == 23
    assert view["section_order"][-1]["key"] == "industry_benchmarks"
    leverage = next(r for r in rows if r["comparison_id"] == "net_debt_to_ebitda")
    assert leverage["parameter_result_ids"]["industry_aggregate"]
    assert leverage["provenance"]["source_workbook_sha256"] == WORKBOOK_SHA256
    assert not (all_keys(rows) & PEER_ONLY_FIELDS)


# --------------------------------------------- reproducibility and invalidation


def test_rerun_with_unchanged_inputs_is_substantively_identical(tmp_path):
    _, first = run_fixture(tmp_path / "a")
    _, second = run_fixture(tmp_path / "b")
    a, b = _bench(first), _bench(second)
    assert strip_run_identity(a) == strip_run_identity(b)
    assert [c["input_hash"] for c in a["comparisons"]] == \
        [c["input_hash"] for c in b["comparisons"]]
    _, agentic_a = run_fixture(tmp_path / "c", mode="agentic", backend=ScriptedBenchmarkBackend())
    _, agentic_b = run_fixture(tmp_path / "d", mode="agentic", backend=ScriptedBenchmarkBackend())
    texts = lambda r: [c["text"] for c in _bench(r)["agentic_interpretation"]["narrative_claims"]]  # noqa: E731
    assert texts(agentic_a) == texts(agentic_b)
    assert strip_run_identity(_bench(agentic_a)) == strip_run_identity(a)


def _approve_and_finalize(session, runner, version, reviewer="credit.officer"):
    row = session.query(Snapshot).filter_by(
        case_id=CASE, snapshot_type="final_case", snapshot_version=version).one()
    HumanReviewWorkflow(session, audit=AuditLog(session), case_id=CASE).sign_off_recommendation(
        reviewer=reviewer, snapshot=FinalCaseSnapshot.model_validate(row.payload),
        reason="Reviewed the draft and the contextual benchmark section.")
    return runner.finalize_case(CASE, version, signed_off_by=reviewer)


def test_changed_workbook_supersedes_comparisons_and_blocks_the_stale_draft(tmp_path):
    session = new_session()
    runner = CreditMemoPipeline(session, data_root=tmp_path / "data",
                                output_root=tmp_path / "output", analysis_mode="agentic",
                                backend=ScriptedBenchmarkBackend())
    first = runner.run_case(CASE, package=variant_package(tmp_path, name="v1"))
    first_bench = copy.deepcopy(first.industry_benchmarking)
    first_draft = copy.deepcopy(first.draft_snapshot.payload)
    first_evidence = copy.deepcopy(
        session.query(Snapshot).filter_by(case_id=CASE, snapshot_type="canonical_evidence",
                                          snapshot_version=1).one().payload)
    first_rows = {r.id: copy.deepcopy(r.payload)
                  for r in session.query(IndustryBenchmarkComparisonRow).all()}
    first_run = first_bench["analysis_run_id"]
    facts_before = {f.fact_id: f.normalized_value for f in session.query(Fact).all()}

    # Finalization requires human approval even with a current benchmark.
    from app.services.review.workflow import SignOffRequiredError

    with pytest.raises(SignOffRequiredError):
        runner.finalize_case(CASE, first.draft_snapshot.snapshot_version,
                             signed_off_by="credit.officer")

    # A changed workbook (published gross Debt/EBITDA restated) is a new dataset.
    changed = edited_workbook({("Benchmarks", "E33"): 3.1})
    second = runner.run_case(CASE, package=variant_package(tmp_path, workbook=changed, name="v2"))
    second_bench = second.industry_benchmarking
    assert second_bench["reference"]["reference_dataset_version"] == 2
    assert second_bench["reference"]["source_workbook_sha256"] != WORKBOOK_SHA256
    new = by_id(second_bench["comparisons"])
    old = by_id(first_bench["comparisons"])
    assert new["gross_debt_to_ebitda"]["industry_aggregate"]["value"] == 3.1
    assert new["gross_debt_to_ebitda"]["input_hash"] != old["gross_debt_to_ebitda"]["input_hash"]
    # Gross debt drives derived net debt too; the published margin is unchanged.
    assert new["net_debt_to_ebitda"]["industry_aggregate"]["value"] != \
        old["net_debt_to_ebitda"]["industry_aggregate"]["value"]
    assert new["operating_margin"]["industry_aggregate"]["value"] == \
        old["operating_margin"]["industry_aggregate"]["value"]

    # Prior comparisons are superseded, never rewritten.
    rows = session.query(IndustryBenchmarkComparisonRow).all()
    assert len(rows) == 46
    for row in rows:
        if row.id in first_rows:
            assert row.acceptance_state == "superseded"
            assert row.payload == first_rows[row.id]
        else:
            assert row.acceptance_state == "accepted"
            assert row.supersedes_id in first_rows
            assert row.dataset_version == 2
    assert session.query(AuditEvent).filter_by(
        event_type="industry_benchmark_superseded").count() == 1

    # Unrelated artifacts of the first run are untouched.
    assert {f.fact_id: f.normalized_value for f in session.query(Fact).all()} == facts_before
    assert session.query(Snapshot).filter_by(
        case_id=CASE, snapshot_type="canonical_evidence",
        snapshot_version=1).one().payload == first_evidence
    assert session.query(Snapshot).filter_by(
        case_id=CASE, snapshot_type="final_case",
        snapshot_version=first.draft_snapshot.snapshot_version).one().payload == first_draft
    for row in session.query(TopicConclusionRow).filter_by(analysis_run_id=first_run):
        assert row.acceptance_state == "accepted"
    assert all(r.acceptance_state == "accepted" for r in
               session.query(ParameterResultRow).filter_by(analysis_run_id=first_run))

    # The stale draft cannot be finalized, even with a valid human approval.
    with pytest.raises(StaleBenchmarkError, match="reference dataset has changed"):
        _approve_and_finalize(session, runner, first.draft_snapshot.snapshot_version)
    assert not session.query(Snapshot).filter_by(
        case_id=CASE, snapshot_type="final_case",
        snapshot_version=first.draft_snapshot.snapshot_version).one().finalized

    # The current draft finalizes after approval and is then immutable.
    frozen, output = _approve_and_finalize(session, runner, second.draft_snapshot.snapshot_version)
    assert frozen.finalized and frozen.content_hash
    final_bench = output.memo_json["final_case_snapshot"]["financial_analysis"][
        "industry_benchmarking"]
    assert final_bench["reference"]["reference_dataset_version"] == 2
    assert "<h3>Healthcare Industry Benchmarking</h3>" in output.html


def test_changed_sector_configuration_also_makes_a_draft_stale(tmp_path):
    from app.core.config_registry import ConfigRegistry
    from tests.healthcare_helpers import variant_config

    session = new_session()
    runner = CreditMemoPipeline(session, data_root=tmp_path / "data",
                                output_root=tmp_path / "output")
    result = runner.run_case(CASE, package=load_package())
    changed = variant_config(lambda c: c["benchmark"].update(in_line_relative_band=0.10))
    ConfigRegistry(session).register(
        "sector_benchmark.medical_devices", changed, label=changed["label"])
    with pytest.raises(StaleBenchmarkError, match="no longer current"):
        _approve_and_finalize(session, runner, result.draft_snapshot.snapshot_version)


# -------------------------------------------------------- existing cases intact


def test_cases_without_a_sector_benchmark_are_unchanged(tmp_path):
    from pathlib import Path
    from app.services.pipeline.package import SourcePackage

    root = Path(__file__).resolve().parents[2] / "examples" / "synthetic-case"
    for mode in ("legacy", "agentic"):
        session = new_session()
        runner = CreditMemoPipeline(session, data_root=tmp_path / mode / "data",
                                    output_root=tmp_path / mode / "output", analysis_mode=mode)
        result = runner.run_case("SYN", package=SourcePackage.load(root / "package.json"))
        assert result.industry_benchmarking is None
        draft = result.draft_snapshot.payload
        assert set(draft["config_versions"]) == set(ARTIFACT_KINDS)
        assert set(result.evidence_snapshot.config_versions) == set(ARTIFACT_KINDS)
        assert set(draft["financial_analysis"]) == {
            "trends", "grounding", "qualitative_facts", "data_limitations"}
        assert session.query(IndustryBenchmarkComparisonRow).count() == 0
        html = MemoReportGenerator(session).render_html(result.memo_json)
        assert "industry-benchmarking" not in html and "Industry Benchmarking" not in html
        # The airline metrics are still required for a non-sector case.
        assert "casm" in result.metrics
