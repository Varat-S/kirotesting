"""Versioned policy / rule layer (Req 11.1-11.6; task 5.5).

This layer evaluates deterministic rules whose thresholds live in VERSIONED
configuration, never in code. It keeps the THREE concepts strictly separate
(Req 11.1):

* ``policy_threshold``          -- a hard illustrative policy limit (e.g. net
  debt / EBITDA policy_limit = 3.5). A breach is a MANDATORY escalation.
* ``peer_benchmark``            -- a borrower value at/above a configured peer
  percentile (e.g. P90). An ANOMALY SIGNAL -> analyst review, NEVER a pass/fail
  credit threshold (Req 10.7).
* ``historical_deterioration``  -- adverse N-year change beyond a configured
  fraction (e.g. 2-year change > 50%). Analyst review.

Each concept is a distinct rule with a distinct evaluation. There is NO single
opaque risk score; the layer emits individual rule-trigger signals (Req 14.1).

Versioning (Req 11.3, 11.5, 11.6): thresholds come from the ``policy`` and
``trend_rules`` config artifacts (labelled ``ILLUSTRATIVE — NOT BANK POLICY``);
each rule is registered as a :class:`~app.models.orm.Rule` + versioned
:class:`~app.models.orm.RuleVersion`. A rule change creates a new version row +
a ``config_version_changed`` audit event, and a run stays linked to the rule
version in force.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import Rule, RuleVersion

ILLUSTRATIVE_LABEL = "ILLUSTRATIVE — NOT BANK POLICY"


class RuleConcept(str, Enum):
    """The three strictly-separate rule concepts (Req 11.1)."""

    POLICY_THRESHOLD = "policy_threshold"
    PEER_BENCHMARK = "peer_benchmark"
    HISTORICAL_DETERIORATION = "historical_deterioration"


class EscalationCategory(str, Enum):
    """Escalation categories (Req 14.2)."""

    DATA_INTEGRITY = "data_integrity"
    FINANCIAL_RULES = "financial_rules"
    AI_DETERMINISTIC_CONFLICT = "ai_deterministic_conflict"
    EVIDENCE = "evidence"


class Severity(str, Enum):
    """Severity of a triggered rule."""

    MANDATORY = "mandatory"
    REVIEW = "review"


# ---------------------------------------------------------------------------
# Illustrative policy config (Req 11.3). This is the DATA for the ``policy``
# config artifact kind, mirrored from design.md's worked example.
# ---------------------------------------------------------------------------
DEFAULT_POLICY: dict[str, Any] = {
    "label": ILLUSTRATIVE_LABEL,
    "metrics": {
        "net_debt_to_ebitda": {
            "policy_limit": 3.5,
            "peer_review_percentile": 90,
            "historical_change_review": 0.50,
            "adverse_direction": "up",
        },
    },
}


@dataclass(frozen=True)
class RuleOutcome:
    """The result of evaluating a single rule against an input."""

    rule_id: str
    rule_version: int | None
    concept: RuleConcept
    category: EscalationCategory
    severity: Severity
    triggered: bool
    metric_name: str
    reason: str
    observed: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)

    @property
    def mandatory(self) -> bool:
        return self.severity is Severity.MANDATORY


class RuleRegistry:
    """Append-only, versioned registry of policy/escalation rules."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def register(
        self,
        rule_id: str,
        *,
        concept: RuleConcept | str,
        category: EscalationCategory | str,
        definition: dict[str, Any],
        description: str | None = None,
        config_kind: str | None = None,
        config_version: int | None = None,
    ) -> RuleVersion:
        """Register (or re-version) a rule. Returns the active version row.

        Identical definitions are idempotent; a changed definition allocates a
        new version. Prior rows are never rewritten so historical runs stay
        linked to the version used (Req 11.5, 11.6).
        """
        concept_v = concept.value if isinstance(concept, RuleConcept) else str(concept)
        category_v = (
            category.value
            if isinstance(category, EscalationCategory)
            else str(category)
        )
        rule = self._session.get(Rule, rule_id)
        if rule is None:
            rule = Rule(
                rule_id=rule_id,
                concept=concept_v,
                category=category_v,
                description=description,
            )
            self._session.add(rule)
            self._session.flush()

        new_hash = content_hash(definition)
        latest = self._latest_version(rule_id)
        if latest is not None and latest.content_hash == new_hash:
            return latest

        next_version = 1 if latest is None else latest.version + 1
        row = RuleVersion(
            rule_id=rule_id,
            version=next_version,
            definition=definition,
            content_hash=new_hash,
            config_kind=config_kind,
            config_version=config_version,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _latest_version(self, rule_id: str) -> RuleVersion | None:
        stmt = (
            select(RuleVersion)
            .where(RuleVersion.rule_id == rule_id)
            .order_by(RuleVersion.version.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def latest_version(self, rule_id: str) -> RuleVersion | None:
        return self._latest_version(rule_id)


class PolicyRuleEvaluator:
    """Evaluate the three separate rule concepts deterministically.

    Thresholds come from the supplied ``policy`` config content (versioned);
    nothing is hardcoded. Each ``evaluate_*`` method is independent so the three
    concepts never collapse into a single score (Req 11.1, 14.1).
    """

    # Stable illustrative rule ids. ``R-TREND-LEV-01`` is the design worked
    # example (historical leverage deterioration).
    RULE_POLICY_LEVERAGE = "R-POLICY-LEV-01"
    RULE_PEER_LEVERAGE = "R-PEER-LEV-01"
    RULE_TREND_LEVERAGE = "R-TREND-LEV-01"

    def __init__(
        self,
        policy: dict[str, Any],
        *,
        policy_version: int | None = None,
    ) -> None:
        self._policy = policy
        self._policy_version = policy_version

    def _metric_policy(self, metric_name: str) -> dict[str, Any]:
        metrics = self._policy.get("metrics", {})
        entry = metrics.get(metric_name)
        if entry is None:
            raise ValueError(
                f"No policy configured for metric {metric_name!r} "
                "(no silent default, Req 21.1)."
            )
        return entry

    # -- concept 1: policy threshold (hard breach -> mandatory) ---------------

    def evaluate_policy_threshold(
        self,
        metric_name: str,
        value: float | None,
        *,
        rule_id: str = RULE_POLICY_LEVERAGE,
        rule_version: int | None = None,
        evidence_refs: list[str] | None = None,
    ) -> RuleOutcome:
        """A hard policy breach is a MANDATORY escalation (Req 11.2)."""
        cfg = self._metric_policy(metric_name)
        limit = cfg.get("policy_limit")
        adverse = cfg.get("adverse_direction", "up")
        triggered = False
        if value is not None and limit is not None:
            triggered = value > limit if adverse == "up" else value < limit
        reason = (
            f"{metric_name}={value} breaches ILLUSTRATIVE policy limit {limit} "
            f"(adverse={adverse})."
            if triggered
            else f"{metric_name}={value} within illustrative policy limit {limit}."
        )
        return RuleOutcome(
            rule_id=rule_id,
            rule_version=rule_version,
            concept=RuleConcept.POLICY_THRESHOLD,
            category=EscalationCategory.FINANCIAL_RULES,
            severity=Severity.MANDATORY,
            triggered=triggered,
            metric_name=metric_name,
            reason=reason,
            observed={"value": value, "policy_limit": limit},
            evidence_refs=list(evidence_refs or []),
        )

    # -- concept 2: peer benchmark (anomaly -> review, never a threshold) -----

    def evaluate_peer_benchmark(
        self,
        metric_name: str,
        value: float | None,
        peer_percentile_value: float | None,
        *,
        rule_id: str = RULE_PEER_LEVERAGE,
        rule_version: int | None = None,
        evidence_refs: list[str] | None = None,
    ) -> RuleOutcome:
        """A borrower at/beyond the configured peer percentile is an ANOMALY.

        This is a REVIEW signal only, NEVER a pass/fail credit threshold
        (Req 10.7, 11.1). ``peer_percentile_value`` is the benchmark layer's
        value at the configured percentile; if it was suppressed (None) the rule
        does not fire.
        """
        cfg = self._metric_policy(metric_name)
        pct = cfg.get("peer_review_percentile")
        adverse = cfg.get("adverse_direction", "up")
        triggered = False
        if value is not None and peer_percentile_value is not None:
            triggered = (
                value >= peer_percentile_value
                if adverse == "up"
                else value <= peer_percentile_value
            )
        reason = (
            f"{metric_name}={value} is an anomaly at/beyond peer P{pct} "
            f"({peer_percentile_value}); analyst review (anomaly signal only)."
            if triggered
            else f"{metric_name}={value} not anomalous vs peer P{pct} "
            f"({peer_percentile_value})."
        )
        return RuleOutcome(
            rule_id=rule_id,
            rule_version=rule_version,
            concept=RuleConcept.PEER_BENCHMARK,
            category=EscalationCategory.FINANCIAL_RULES,
            severity=Severity.REVIEW,
            triggered=triggered,
            metric_name=metric_name,
            reason=reason,
            observed={
                "value": value,
                "peer_percentile": pct,
                "peer_percentile_value": peer_percentile_value,
            },
            evidence_refs=list(evidence_refs or []),
        )

    # -- concept 3: historical deterioration (adverse N-year change) ----------

    def evaluate_historical_deterioration(
        self,
        metric_name: str,
        pct_change: float | None,
        *,
        direction_is_adverse: bool,
        window: str = "2y",
        rule_id: str = RULE_TREND_LEVERAGE,
        rule_version: int | None = None,
        evidence_refs: list[str] | None = None,
    ) -> RuleOutcome:
        """Adverse N-year change beyond the configured fraction -> review.

        ``pct_change`` is the signed relative change over the window (from the
        trend layer); ``direction_is_adverse`` says whether that movement is the
        adverse direction for this metric. The rule fires only on an ADVERSE
        move whose magnitude exceeds ``historical_change_review`` (Req 9.2, 11.1).
        """
        cfg = self._metric_policy(metric_name)
        threshold = cfg.get("historical_change_review")
        triggered = False
        if (
            pct_change is not None
            and threshold is not None
            and direction_is_adverse
            and abs(pct_change) > threshold
        ):
            triggered = True
        reason = (
            f"{metric_name} {window} change {pct_change:+.2%} is adverse and "
            f"exceeds illustrative review threshold {threshold:.0%}; analyst review."
            if triggered
            else f"{metric_name} {window} change does not meet the adverse "
            "deterioration review threshold."
        )
        return RuleOutcome(
            rule_id=rule_id,
            rule_version=rule_version,
            concept=RuleConcept.HISTORICAL_DETERIORATION,
            category=EscalationCategory.FINANCIAL_RULES,
            severity=Severity.REVIEW,
            triggered=triggered,
            metric_name=metric_name,
            reason=reason,
            observed={
                "pct_change": pct_change,
                "window": window,
                "threshold": threshold,
                "direction_is_adverse": direction_is_adverse,
            },
            evidence_refs=list(evidence_refs or []),
        )
