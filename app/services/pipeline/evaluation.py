"""Score artifacts produced by the runner, never surrogate pipeline outputs."""

import json

from app.services.evaluation.ablation import VariantOutcome, run_ablations
from app.services.evaluation.manifest import GroundTruthManifest
from app.services.pipeline.package import SourcePackage


def variant_outcome(
    result, manifest: GroundTruthManifest, borrower_entity_id: str
) -> VariantOutcome:
    if (
        result.evidence_snapshot.as_of_date != manifest.as_of_date
        or result.evidence_snapshot.evidence_cutoff_timestamp
        != manifest.evidence_cutoff_timestamp
    ):
        raise ValueError("Scoring manifest must match the run's as-of date and cutoff.")
    produced = {}
    current_year = result.evidence_snapshot.as_of_date.year
    for key, quality in result.evidence_snapshot.data_quality.items():
        entity, year, period_type, end, name = json.loads(key)
        if (
            entity == borrower_entity_id
            and year == current_year
            and end == result.evidence_snapshot.as_of_date.isoformat()
            and quality.value is not None
        ):
            produced[name] = quality.value
    missing = sorted(
        f.name for f in manifest.required_fields() if f.name not in produced
    )
    selected_ids = {
        quality.selected_fact_id
        for quality in result.evidence_snapshot.data_quality.values()
        if quality.selected_fact_id
    }
    actual_facts = {f["fact_id"]: f for f in result.evidence_snapshot.facts}
    # An invented hard value is one without a selected, mapped source fact.
    supported = {
        actual_facts[fid]["name"]
        for fid in selected_ids
        if fid in actual_facts and actual_facts[fid].get("mapping_status") == "mapped"
    }
    grounding = result.draft_snapshot.payload["financial_analysis"].get("grounding", [])
    return VariantOutcome(
        produced_fields=produced,
        missing_fields=missing,
        available_metrics=sorted(
            k
            for k, m in result.metrics.items()
            if m.result is not None and m.state.value == "ok"
        ),
        escalations=sorted({e["rule_id"] for e in result.escalations}),
        unsupported_claims=sum(not g["is_grounded"] for g in grounding),
        total_claims=len(grounding),
        human_review_triggered=result.draft_snapshot.payload["recommendation"].get(
            "human_sign_off_required", False
        ),
        invented_values=sorted(set(produced) - supported),
    )


def run_pipeline_ablations(
    package: SourcePackage, *, manifest: GroundTruthManifest, execute
):
    """execute(variant_name, package) must invoke CreditMemoPipeline.run_case."""
    sources = [
        {"index": index, "tags": set(source.tags)}
        for index, source in enumerate(package.sources)
    ]

    def evaluate(variant, surviving):
        changed = package.model_copy(
            update={"sources": [package.sources[item["index"]] for item in surviving]}
        )
        result = execute(variant.value, changed)
        return variant_outcome(result, manifest, package.borrower_entity_id)

    return run_ablations(sources, evaluate, manifest=manifest)
