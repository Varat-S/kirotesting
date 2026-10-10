"""Authoritative Fact/Metric resolver for numerical corroboration (Task 11).

Task 6C made premise-level numerical corroboration require explicit, concept-
linked metric metadata -- but that metadata was CALLER-supplied. This read-only
resolver instead derives it from the AUTHORITATIVE persisted records
(:class:`~app.models.orm.Fact`, :class:`~app.models.orm.Metric`), so an id in
``validated_metric_ids`` is only honoured when a real, approved record backs it.

It produces either a validated :class:`~app.services.analysis.grounding.
EvidenceItem` (carrying the record's trusted concept / entity / period / unit /
scale / currency / accounting_basis) or a typed :class:`ResolvedMetric` marked
``usable=False`` with the reason it cannot corroborate. It FAILS CLOSED for
unknown, cross-case, wrong-entity/period, conflicting/unverified or
insufficiently-sourced records. It never invents a value, a definition, or
broadens accepted statuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import Fact, FactSourceRef, Metric
from app.schemas.enums import FactStatus
from app.services.analysis.grounding import EvidenceItem

# Only a deterministically VERIFIED fact may corroborate a factual premise. A
# fact's value comes from reconciliation; AI/unverified/conflicting never do.
_CORROBORATING_FACT_STATUSES = frozenset({FactStatus.VERIFIED.value})


@dataclass
class ResolvedMetric:
    """Outcome of resolving one requested metric/fact id against the DB."""

    requested_id: str
    usable: bool
    evidence_item: EvidenceItem | None = None
    reasons: list[str] = field(default_factory=list)
    record_kind: str | None = None  # "fact" | "metric" | None


@dataclass
class MetricResolution:
    """The batch result: usable items + per-id dispositions."""

    items: list[EvidenceItem] = field(default_factory=list)
    resolved: list[ResolvedMetric] = field(default_factory=list)

    @property
    def usable_ids(self) -> set[str]:
        return {r.requested_id for r in self.resolved if r.usable}

    def rejected(self) -> list[ResolvedMetric]:
        return [r for r in self.resolved if not r.usable]


class AuthoritativeMetricResolver:
    """Read-only resolver from persisted Fact/Metric records to EvidenceItems."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve(
        self,
        requested_ids,
        *,
        case_id: str,
        reporting_entity_id: str | None = None,
        evidence_cutoff: datetime | None = None,
    ) -> MetricResolution:
        result = MetricResolution()
        for rid in sorted(set(requested_ids)):
            resolved = self._resolve_one(
                rid, case_id=case_id, entity_id=reporting_entity_id,
                cutoff=evidence_cutoff,
            )
            result.resolved.append(resolved)
            if resolved.usable and resolved.evidence_item is not None:
                result.items.append(resolved.evidence_item)
        return result

    # -- single-id resolution -------------------------------------------------

    def _resolve_one(self, rid, *, case_id, entity_id, cutoff) -> ResolvedMetric:
        fact = self._session.get(Fact, rid)
        if fact is not None:
            return self._from_fact(rid, fact, case_id, entity_id, cutoff)
        metric = self._metric_by_name(rid, case_id)
        if metric is not None:
            return self._from_metric(rid, metric, case_id)
        return ResolvedMetric(rid, usable=False,
                              reasons=["no Fact or Metric record with this id"])

    def _metric_by_name(self, name, case_id):
        return self._session.scalars(
            select(Metric).where(
                Metric.metric_name == name, Metric.case_id == case_id
            ).order_by(Metric.created_at.desc())
        ).first()

    # -- Fact-backed corroboration -------------------------------------------

    def _from_fact(self, rid, fact: Fact, case_id, entity_id, cutoff) -> ResolvedMetric:
        reasons: list[str] = []
        if fact.case_id != case_id:
            reasons.append(f"fact belongs to case {fact.case_id!r}, not {case_id!r}")
        if entity_id is not None and fact.entity_id not in (None, entity_id):
            reasons.append(
                f"fact entity {fact.entity_id!r} != reporting entity {entity_id!r}")
        if fact.status not in _CORROBORATING_FACT_STATUSES:
            reasons.append(
                f"fact status {fact.status!r} is not verified; cannot corroborate")
        if fact.normalized_value is None:
            reasons.append("fact carries no normalized value (missing/non-value)")
        if not self._has_source(rid):
            reasons.append("fact has no persisted source reference (untraceable)")
        if cutoff is not None and self._after_cutoff(fact.data_freshness, cutoff):
            reasons.append("fact data is after the evidence cutoff")
        concept = fact.taxonomy_concept or fact.name
        if not concept:
            reasons.append("fact has no concept/name metadata")
        if reasons:
            return ResolvedMetric(rid, usable=False, reasons=reasons, record_kind="fact")
        return ResolvedMetric(
            rid, usable=True, record_kind="fact",
            evidence_item=EvidenceItem(
                evidence_id=rid, value=fact.normalized_value,
                unit=fact.normalized_unit, concept=concept,
                entity=fact.entity_id, period=self._period(fact),
                scale=fact.scale, currency=fact.currency,
                accounting_basis=fact.accounting_basis,
            ),
        )

    # -- Metric-backed corroboration -----------------------------------------

    def _from_metric(self, rid, metric: Metric, case_id) -> ResolvedMetric:
        reasons: list[str] = []
        if metric.case_id != case_id:
            reasons.append(f"metric belongs to case {metric.case_id!r}")
        if metric.result is None or metric.explicit_state is not None:
            reasons.append(
                f"metric has no usable result (state={metric.explicit_state!r})")
        # A metric that still requires review or lacks verified evidence quality
        # cannot independently corroborate.
        if metric.review_required:
            reasons.append("metric still requires review")
        if metric.evidence_quality != "verified":
            reasons.append(
                f"metric evidence_quality {metric.evidence_quality!r} is not verified")
        if reasons:
            return ResolvedMetric(rid, usable=False, reasons=reasons,
                                  record_kind="metric")
        return ResolvedMetric(
            rid, usable=True, record_kind="metric",
            evidence_item=EvidenceItem(
                evidence_id=rid, value=metric.result, unit=metric.units,
                concept=metric.metric_name,
                period=str(metric.period) if metric.period else None,
            ),
        )

    # -- helpers --------------------------------------------------------------

    def _has_source(self, fact_id) -> bool:
        return self._session.scalars(
            select(FactSourceRef).where(FactSourceRef.fact_id == fact_id).limit(1)
        ).first() is not None

    @staticmethod
    def _period(fact: Fact) -> str | None:
        if fact.period_end is not None:
            return fact.period_end.isoformat()
        if fact.fiscal_year is not None:
            return f"FY{fact.fiscal_year}"
        return None

    @staticmethod
    def _after_cutoff(freshness, cutoff: datetime) -> bool:
        if freshness is None:
            return False
        if isinstance(freshness, date) and not isinstance(freshness, datetime):
            return False
        try:
            return freshness > cutoff
        except TypeError:
            return False
