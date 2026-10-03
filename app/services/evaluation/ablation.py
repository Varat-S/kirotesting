"""Missing-data ablation harness (task 9.4, Req 23.8, 23.14).

Removes source types from the evidence package to create DEGRADED variants and
measures how the system responds. The required variants (Req 23.8) are:

``FULL, NO_XLSX, NO_XBRL, PDF_ONLY, NO_INDUSTRY_DATA, NO_FUEL_DATA,
NO_DEBT_MATURITY_TABLE, NO_INTERIM_STATEMENTS``.

For each variant the harness measures (all against a declared manifest, so no
scored number is produced without ground truth, Req 23.5):

* field completeness / accuracy
* metric availability
* risk recall
* unsupported-claim rate
* correct-escalation rate
* human-review rate
* ``performance_drop = score_full - score_degraded``

The headline behavioral guarantee (Req 23.8) is GRACEFUL DEGRADATION: when a
source is removed, the affected facts/metrics become MISSING (never a fabricated
value, never missing-coerced-to-0) and the system responds with a caveat or
escalation. The harness asserts no invented values appear and surfaces
``performance_drop`` rather than hiding it.

The harness ORCHESTRATES the degradation; it does not reimplement extraction or
escalation. A caller supplies an ``evaluate`` callback that, given the surviving
sources, returns the pipeline outcome for that variant (facts, metric
availability, escalations, unsupported claims). The harness scores that outcome
against the manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

from app.services.evaluation.manifest import GroundTruthManifest


class AblationVariant(str, Enum):
    """The required missing-data ablation variants (Req 23.8)."""

    FULL = "FULL"
    NO_XLSX = "NO_XLSX"
    NO_XBRL = "NO_XBRL"
    PDF_ONLY = "PDF_ONLY"
    NO_INDUSTRY_DATA = "NO_INDUSTRY_DATA"
    NO_FUEL_DATA = "NO_FUEL_DATA"
    NO_DEBT_MATURITY_TABLE = "NO_DEBT_MATURITY_TABLE"
    NO_INTERIM_STATEMENTS = "NO_INTERIM_STATEMENTS"


# Which source "tags" each variant removes from the FULL package. A source item
# is a mapping with a ``tags`` set; a variant drops any item carrying a dropped
# tag. PDF_ONLY keeps only pdf-tagged sources.
_DROP_TAGS: dict[AblationVariant, set[str]] = {
    AblationVariant.FULL: set(),
    AblationVariant.NO_XLSX: {"xlsx"},
    AblationVariant.NO_XBRL: {"xbrl"},
    AblationVariant.NO_INDUSTRY_DATA: {"industry"},
    AblationVariant.NO_FUEL_DATA: {"fuel"},
    AblationVariant.NO_DEBT_MATURITY_TABLE: {"debt_maturity"},
    AblationVariant.NO_INTERIM_STATEMENTS: {"interim"},
}


@dataclass
class VariantOutcome:
    """Pipeline outcome for one ablation variant, supplied by the caller.

    All fields are explicit and never defaulted into fabricated values; a fact
    that cannot be produced is reported MISSING via ``missing_fields``.
    """

    produced_fields: dict[str, Any]  # field name -> value (never fabricated)
    missing_fields: list[str]
    available_metrics: list[str]
    escalations: list[str]  # rule_ids raised
    unsupported_claims: int = 0
    total_claims: int = 0
    human_review_triggered: bool = False
    invented_values: list[str] = field(default_factory=list)  # must stay empty


@dataclass
class VariantScore:
    """Scored metrics for one variant (all vs the declared manifest)."""

    variant: str
    field_completeness: float
    field_accuracy: float
    metric_availability: float
    risk_recall: float
    unsupported_claim_rate: float
    correct_escalation_rate: float
    human_review_rate: float
    graceful: bool
    invented_values: list[str]

    @property
    def composite_score(self) -> float:
        """A single 0..1 quality score used for ``performance_drop``.

        Combines completeness, accuracy, metric availability, risk recall, and
        correct escalations, penalised by the unsupported-claim rate.
        """
        base = (
            self.field_completeness
            + self.field_accuracy
            + self.metric_availability
            + self.risk_recall
            + self.correct_escalation_rate
        ) / 5.0
        return max(0.0, base - self.unsupported_claim_rate)

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "field_completeness": self.field_completeness,
            "field_accuracy": self.field_accuracy,
            "metric_availability": self.metric_availability,
            "risk_recall": self.risk_recall,
            "unsupported_claim_rate": self.unsupported_claim_rate,
            "correct_escalation_rate": self.correct_escalation_rate,
            "human_review_rate": self.human_review_rate,
            "graceful_degradation": self.graceful,
            "composite_score": self.composite_score,
            "invented_values": list(self.invented_values),
        }


@dataclass
class AblationReport:
    """Full ablation report with per-variant scores + performance_drop."""

    case_id: str
    manifest_version: str
    variants: dict[str, VariantScore] = field(default_factory=dict)
    performance_drop: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "manifest_version": self.manifest_version,
            "variant_count": len(self.variants),
            "variants": {k: v.as_dict() for k, v in self.variants.items()},
            "performance_drop": dict(self.performance_drop),
        }


def apply_variant(
    sources: list[Mapping[str, Any]], variant: AblationVariant
) -> list[Mapping[str, Any]]:
    """Return the surviving sources after removing a variant's dropped tags.

    Each source is a mapping with a ``tags`` iterable. PDF_ONLY keeps only
    pdf-tagged sources; the named NO_* variants drop any source carrying the
    corresponding tag.
    """
    if variant is AblationVariant.PDF_ONLY:
        return [s for s in sources if "pdf" in set(s.get("tags", ()))]
    drop = _DROP_TAGS.get(variant, set())
    if not drop:
        return list(sources)
    return [s for s in sources if not (set(s.get("tags", ())) & drop)]


def _score_variant(
    variant: AblationVariant,
    outcome: VariantOutcome,
    manifest: GroundTruthManifest,
) -> VariantScore:
    required = manifest.required_fields()
    required_names = {f.name for f in required}
    expected_values = {f.name: f for f in manifest.verified_facts}

    produced = outcome.produced_fields
    completeness = (
        len([n for n in required_names if n in produced]) / len(required_names)
        if required_names
        else 1.0
    )

    # Accuracy over the produced required fields (missing ones are not "wrong",
    # they are reported missing -- graceful degradation, Req 23.8).
    acc_total = 0
    acc_ok = 0
    for name, ef in expected_values.items():
        if name not in produced:
            continue
        acc_total += 1
        got = produced[name]
        exp = ef.expected_value
        if isinstance(exp, (int, float)) and isinstance(got, (int, float)):
            if abs(float(got) - float(exp)) <= ef.tolerance:
                acc_ok += 1
        elif str(got) == str(exp):
            acc_ok += 1
    field_accuracy = (acc_ok / acc_total) if acc_total else 1.0

    expected_metrics = [m.metric_id for m in manifest.expected_metric_outputs]
    metric_availability = (
        len([m for m in expected_metrics if m in outcome.available_metrics])
        / len(expected_metrics)
        if expected_metrics
        else 1.0
    )

    expected_risks = set(manifest.adjudicated_material_risks)
    # Risk recall here is approximated by escalations/claims covering known
    # risks; the caller marks which risks the variant surfaced via escalations.
    risk_recall = (
        len(expected_risks & set(outcome.escalations)) / len(expected_risks)
        if expected_risks
        else 1.0
    )

    unsupported_rate = (
        outcome.unsupported_claims / outcome.total_claims
        if outcome.total_claims
        else 0.0
    )

    expected_rules = set(manifest.expected_rule_triggers)
    raised = set(outcome.escalations)
    correct_escalation_rate = (
        len(expected_rules & raised) / len(expected_rules) if expected_rules else 1.0
    )

    human_review_rate = 1.0 if outcome.human_review_triggered else 0.0

    # Graceful degradation: nothing fabricated AND any lost field/metric is
    # accompanied by a caveat/escalation OR human review (never silent).
    lost_fields = bool(set(required_names) - set(produced))
    lost_metrics = bool(set(expected_metrics) - set(outcome.available_metrics))
    degraded = lost_fields or lost_metrics
    responded = bool(outcome.escalations) or outcome.human_review_triggered
    graceful = (not outcome.invented_values) and (not degraded or responded)

    return VariantScore(
        variant=variant.value,
        field_completeness=completeness,
        field_accuracy=field_accuracy,
        metric_availability=metric_availability,
        risk_recall=risk_recall,
        unsupported_claim_rate=unsupported_rate,
        correct_escalation_rate=correct_escalation_rate,
        human_review_rate=human_review_rate,
        graceful=graceful,
        invented_values=list(outcome.invented_values),
    )


def run_ablations(
    sources: list[Mapping[str, Any]],
    evaluate: Callable[[AblationVariant, list[Mapping[str, Any]]], VariantOutcome],
    *,
    manifest: GroundTruthManifest | None,
    variants: list[AblationVariant] | None = None,
) -> AblationReport:
    """Run the ablation harness over ``variants`` (default: all eight).

    ``evaluate(variant, surviving_sources)`` returns the pipeline
    :class:`VariantOutcome` for that variant. The harness scores each variant
    against the declared ``manifest`` and reports ``performance_drop`` relative
    to FULL. Raises when no manifest is declared (Req 23.5).
    """
    if manifest is None:
        raise ValueError(
            "run_ablations requires a declared GroundTruthManifest (Req 23.5)."
        )
    if variants is None:
        variants = list(AblationVariant)
    if AblationVariant.FULL not in variants:
        variants = [AblationVariant.FULL, *variants]

    report = AblationReport(
        case_id=manifest.case_id, manifest_version=manifest.manifest_version
    )
    for variant in variants:
        surviving = apply_variant(sources, variant)
        outcome = evaluate(variant, surviving)
        report.variants[variant.value] = _score_variant(variant, outcome, manifest)

    full_score = report.variants[AblationVariant.FULL.value].composite_score
    for name, vs in report.variants.items():
        report.performance_drop[name] = full_score - vs.composite_score
    return report
