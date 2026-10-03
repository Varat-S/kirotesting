"""Missing-data ablation harness tests (task 9.4, Req 23.8, 23.14).

Runs the harness over the eight named variants (>= 4 required) and proves:

* removing a source makes the dependent fields/metrics MISSING, never a
  fabricated value and never missing-coerced-to-0;
* the degraded variant responds with an escalation / human review (graceful
  degradation);
* ``performance_drop = score_full - score_degraded`` is reported and non-zero
  for degraded variants;
* a variant that invents a value is flagged NON-graceful.
"""

from __future__ import annotations

from pathlib import Path

from app.services.evaluation import (
    AblationVariant,
    VariantOutcome,
    apply_variant,
    load_manifest,
    run_ablations,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "acme_fy2023_manifest.json"

# Synthetic evidence package tagged by source type (mirrors the eval case).
SOURCES = [
    {"document_id": "doc-xbrl", "tags": {"xbrl", "structured"}},
    {"document_id": "doc-csv", "tags": {"csv", "xlsx", "structured"}},
    {"document_id": "doc-pdf", "tags": {"pdf", "narrative"}},
    {"document_id": "doc-industry", "tags": {"industry"}},
    {"document_id": "doc-fuel", "tags": {"fuel", "market_macro"}},
    {"document_id": "doc-debt", "tags": {"pdf", "debt_maturity"}},
    {"document_id": "doc-interim", "tags": {"interim"}},
]

# Full-package produced fields (all required fields present + correct).
_FULL_FIELDS = {
    "revenue": 1500.0,
    "net_income": 250.0,
    "total_assets": 4200.0,
    "total_debt": 750.0,
    "load_factor": 0.85,
}
_FULL_METRICS = ["net_debt_to_ebitda", "operating_margin", "load_factor"]


def _evaluate(variant: AblationVariant, surviving: list) -> VariantOutcome:
    """Deterministic synthetic pipeline: which fields survive which sources."""
    surviving_tags: set[str] = set()
    for s in surviving:
        surviving_tags |= set(s.get("tags", ()))

    produced = dict(_FULL_FIELDS)
    metrics = list(_FULL_METRICS)
    missing: list[str] = []
    escalations: list[str] = []
    human_review = False

    # total_debt depends on the CSV/XLSX source.
    if "xlsx" not in surviving_tags and "csv" not in surviving_tags:
        produced.pop("total_debt", None)
        missing.append("total_debt")
        escalations.append("R-DATA-MISSING-CRITICAL-01")
        human_review = True
    # load_factor + fuel risk depend on fuel/debt tables.
    if "fuel" not in surviving_tags:
        escalations.append("fuel_price_exposure")  # surfaced as a known risk
    if "debt_maturity" not in surviving_tags:
        escalations.append("near_term_debt_maturity")
    # XBRL carries the primary financials.
    if "xbrl" not in surviving_tags:
        for f in ("revenue", "net_income", "total_assets"):
            produced.pop(f, None)
            missing.append(f)
        metrics = [m for m in metrics if m == "load_factor"]
        escalations.append("R-DATA-MISSING-CRITICAL-01")
        human_review = True

    return VariantOutcome(
        produced_fields=produced,
        missing_fields=missing,
        available_metrics=metrics,
        escalations=sorted(set(escalations)),
        unsupported_claims=0,
        total_claims=5,
        human_review_triggered=human_review,
        invented_values=[],  # NEVER invent a value (Req 23.8)
    )


def test_apply_variant_drops_expected_sources() -> None:
    no_xbrl = apply_variant(SOURCES, AblationVariant.NO_XBRL)
    assert all("xbrl" not in set(s["tags"]) for s in no_xbrl)

    pdf_only = apply_variant(SOURCES, AblationVariant.PDF_ONLY)
    assert pdf_only and all("pdf" in set(s["tags"]) for s in pdf_only)


def test_ablation_runs_all_eight_variants_with_performance_drop() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    report = run_ablations(SOURCES, _evaluate, manifest=manifest)

    # All eight named variants (>= 4 required).
    assert len(report.variants) == 8
    assert set(report.variants) == {v.value for v in AblationVariant}

    # FULL has zero performance drop and perfect completeness.
    full = report.variants["FULL"]
    assert full.field_completeness == 1.0
    assert report.performance_drop["FULL"] == 0.0

    # Removing XBRL degrades completeness and yields a positive performance drop.
    no_xbrl = report.variants["NO_XBRL"]
    assert no_xbrl.field_completeness < 1.0
    assert report.performance_drop["NO_XBRL"] > 0.0


def test_graceful_degradation_no_invented_values() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    report = run_ablations(SOURCES, _evaluate, manifest=manifest)

    for vs in report.variants.values():
        # No variant invented a value, and every degraded variant responded
        # with an escalation / human review (graceful).
        assert vs.invented_values == []
        assert vs.graceful is True


def test_missing_field_is_not_coerced_to_zero() -> None:
    """A removed source yields a MISSING field, never a fabricated 0."""
    surviving = apply_variant(SOURCES, AblationVariant.NO_XBRL)
    outcome = _evaluate(AblationVariant.NO_XBRL, surviving)
    assert "revenue" not in outcome.produced_fields  # not present, not 0
    assert "revenue" in outcome.missing_fields
    assert 0.0 not in {outcome.produced_fields.get("revenue")}


def test_invented_value_is_flagged_non_graceful() -> None:
    """A hypothetical variant that fabricates a value is NOT graceful."""
    manifest = load_manifest(MANIFEST_PATH)

    def cheating_eval(variant: AblationVariant, surviving: list) -> VariantOutcome:
        outcome = _evaluate(variant, surviving)
        if variant is AblationVariant.NO_XBRL:
            # Fabricate the missing revenue -> must be flagged.
            outcome.produced_fields["revenue"] = 9999.0
            outcome.invented_values = ["revenue"]
        return outcome

    report = run_ablations(SOURCES, cheating_eval, manifest=manifest)
    assert report.variants["NO_XBRL"].graceful is False
    assert "revenue" in report.variants["NO_XBRL"].invented_values
