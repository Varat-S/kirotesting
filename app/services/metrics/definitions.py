"""Versioned metric-definition registry (Req 8.2, 8.3, 8.8; task 5.1).

A metric definition states exactly WHICH components a metric includes/excludes
(e.g. what "EBITDA", "net debt", "FCF", "liquidity" comprise), the input fact
names it consumes, the formula it uses, and the units/period type of the result.
Each definition is identified by ``metric_definition_id`` + ``version``.

Guarantees:

* **Append-only versioning.** Registering changed components allocates a NEW
  version row; prior rows are never rewritten, so a historical metric output
  stays linked to the exact definition version it used (Req 8.8).
* **Idempotent.** Registering identical components returns the existing version.
* **Definition change -> audit event + regression trigger.** A changed
  definition emits ``metric_definition_changed`` and flags the metric for
  deterministic regression (Req 8.8); it does NOT rewrite historical outputs.
* **No silent defaults.** The illustrative airline metric set is registered
  explicitly and versioned through this registry; nothing is a silent in-code
  constant (Req 21.1). Callers must register (or pass) definitions.

The set of metric definitions is itself mirrored into the ``metric_defs``
configuration artifact kind so a snapshot can record the config version in force
(Req 19.6); this module owns the richer per-metric definition rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import MetricDefinition

# An audit callback accepts (metric_definition_id, version, before, after).
MetricDefAuditHook = Callable[[str, int, dict | None, dict], None]


ILLUSTRATIVE_LABEL = "ILLUSTRATIVE — NOT BANK POLICY"


@dataclass(frozen=True)
class ResolvedMetricDefinition:
    """A metric definition resolved from the registry for the engine to use."""

    metric_definition_id: str
    version: int
    formula_id: str
    components: dict[str, Any]
    content_hash: str
    label: str | None

    @property
    def inputs(self) -> list[str]:
        """Ordered list of input fact names this metric consumes."""
        return list(self.components.get("inputs", []))

    @property
    def units(self) -> str | None:
        return self.components.get("units")

    @property
    def included(self) -> list[str]:
        return list(self.components.get("included", []))

    @property
    def excluded(self) -> list[str]:
        return list(self.components.get("excluded", []))


# ---------------------------------------------------------------------------
# Illustrative airline metric definitions (configuration, NOT bank policy).
#
# Each entry states the formula id, the input fact names, included/excluded
# components, result units, and period type. These are registered explicitly
# and versioned; they are not silent in-code magic (Req 21.1, 8.3).
# ---------------------------------------------------------------------------
DEFAULT_METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    "revenue_growth": {
        "formula_id": "F-REV-GROWTH-01",
        "inputs": ["revenue", "revenue_prior"],
        "included": ["total_operating_revenue"],
        "excluded": [],
        "units": "ratio",
        "period_type": "FY",
    },
    "operating_margin": {
        "formula_id": "F-OP-MARGIN-01",
        "inputs": ["operating_income", "revenue"],
        "included": ["operating_income", "total_operating_revenue"],
        "excluded": ["special_items"],
        "units": "ratio",
        "period_type": "FY",
    },
    "net_debt": {
        "formula_id": "F-NET-DEBT-01",
        "inputs": ["total_debt", "unrestricted_cash"],
        "included": ["total_debt"],
        "excluded": ["restricted_cash", "operating_leases"],
        "units": "currency",
        "period_type": "instant",
    },
    "net_debt_to_ebitda": {
        "formula_id": "F-ND-EBITDA-01",
        "inputs": ["total_debt", "unrestricted_cash", "ebitda"],
        "included": ["net_debt", "ebitda"],
        "excluded": ["operating_leases"],
        "units": "x",
        "period_type": "FY",
    },
    "interest_coverage": {
        "formula_id": "F-INT-COV-01",
        "inputs": ["ebitda", "interest_expense"],
        "included": ["ebitda"],
        "excluded": ["capitalized_interest"],
        "units": "x",
        "period_type": "FY",
    },
    "cash_conversion": {
        "formula_id": "F-CFO-EBITDA-01",
        "inputs": ["cfo", "ebitda"],
        "included": ["cfo", "ebitda"],
        "excluded": [],
        "units": "ratio",
        "period_type": "FY",
    },
    "free_cash_flow": {
        "formula_id": "F-FCF-01",
        "inputs": ["cfo", "capex"],
        "included": ["cfo"],
        "excluded": ["acquisitions"],
        "units": "currency",
        "period_type": "FY",
    },
    "liquidity": {
        "formula_id": "F-LIQ-01",
        "inputs": ["unrestricted_cash", "undrawn_revolver"],
        "included": ["unrestricted_cash", "undrawn_revolver"],
        "excluded": ["restricted_cash"],
        "units": "currency",
        "period_type": "instant",
    },
    "capex_to_revenue": {
        "formula_id": "F-CAPEX-REV-01",
        "inputs": ["capex", "revenue"],
        "included": ["capex"],
        "excluded": [],
        "units": "ratio",
        "period_type": "FY",
    },
    "load_factor": {
        "formula_id": "F-LOAD-FACTOR-01",
        "inputs": ["rpm", "asm"],
        "included": ["revenue_passenger_miles", "available_seat_miles"],
        "excluded": [],
        "units": "ratio",
        "period_type": "FY",
    },
    "casm": {
        "formula_id": "F-CASM-01",
        "inputs": ["operating_expense", "asm"],
        "included": ["operating_expense", "available_seat_miles"],
        "excluded": ["special_items"],
        "units": "currency_per_asm",
        "period_type": "FY",
    },
}


class MetricDefinitionRegistry:
    """Append-only, versioned registry of metric definitions."""

    def __init__(
        self, session: Session, audit_hook: MetricDefAuditHook | None = None
    ) -> None:
        self._session = session
        self._audit_hook = audit_hook
        # metric_definition_ids whose latest version changed in this process
        # (deterministic regression trigger, Req 8.8).
        self.regression_triggered: set[str] = set()

    # -- registration ---------------------------------------------------------

    def register(
        self,
        metric_definition_id: str,
        components: dict[str, Any],
        *,
        label: str | None = ILLUSTRATIVE_LABEL,
    ) -> ResolvedMetricDefinition:
        """Register ``components`` for ``metric_definition_id``.

        Returns the existing version if the component hash is unchanged;
        otherwise allocates the next version, emits ``metric_definition_changed``
        and flags a regression trigger. Historical rows are never rewritten.
        """
        formula_id = components.get("formula_id")
        if not formula_id:
            raise ValueError(
                f"Metric definition {metric_definition_id!r} must declare a "
                "'formula_id' (no silent defaults, Req 21.1)."
            )
        new_hash = content_hash(components)
        latest = self._latest_row(metric_definition_id)

        if latest is not None and latest.content_hash == new_hash:
            return self._to_view(latest)

        next_version = 1 if latest is None else latest.version + 1
        row = MetricDefinition(
            metric_definition_id=metric_definition_id,
            version=next_version,
            formula_id=str(formula_id),
            components=components,
            content_hash=new_hash,
            label=label,
        )
        self._session.add(row)
        self._session.flush()

        if latest is not None:
            # A definition CHANGE (not first registration) triggers regression.
            self.regression_triggered.add(metric_definition_id)
        self._emit_change_event(metric_definition_id, next_version, latest, components)
        return self._to_view(row)

    def register_defaults(
        self, definitions: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, ResolvedMetricDefinition]:
        """Register the illustrative default metric definitions explicitly.

        Returns ``{metric_definition_id: ResolvedMetricDefinition}``. These are
        versioned via this registry so nothing is a silent in-code default
        (Req 21.1).
        """
        defs = definitions if definitions is not None else DEFAULT_METRIC_DEFINITIONS
        return {
            metric_id: self.register(metric_id, components)
            for metric_id, components in defs.items()
        }

    def _emit_change_event(
        self,
        metric_definition_id: str,
        version: int,
        previous: MetricDefinition | None,
        components: dict[str, Any],
    ) -> None:
        if self._audit_hook is None:
            return
        before = (
            {"version": previous.version, "content_hash": previous.content_hash}
            if previous is not None
            else None
        )
        after = {"version": version, "content_hash": content_hash(components)}
        self._audit_hook(metric_definition_id, version, before, after)

    # -- lookups --------------------------------------------------------------

    def _latest_row(self, metric_definition_id: str) -> MetricDefinition | None:
        stmt = (
            select(MetricDefinition)
            .where(MetricDefinition.metric_definition_id == metric_definition_id)
            .order_by(MetricDefinition.version.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def latest(self, metric_definition_id: str) -> ResolvedMetricDefinition | None:
        row = self._latest_row(metric_definition_id)
        return self._to_view(row) if row is not None else None

    def get(
        self, metric_definition_id: str, version: int
    ) -> ResolvedMetricDefinition | None:
        """Return a specific definition version (for historical reproduction)."""
        stmt = select(MetricDefinition).where(
            MetricDefinition.metric_definition_id == metric_definition_id,
            MetricDefinition.version == version,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return self._to_view(row) if row is not None else None

    def all_latest(self) -> dict[str, ResolvedMetricDefinition]:
        """Return the latest version of every registered metric definition."""
        rows = self._session.execute(select(MetricDefinition)).scalars().all()
        latest: dict[str, MetricDefinition] = {}
        for row in rows:
            cur = latest.get(row.metric_definition_id)
            if cur is None or row.version > cur.version:
                latest[row.metric_definition_id] = row
        return {k: self._to_view(v) for k, v in latest.items()}

    @staticmethod
    def _to_view(row: MetricDefinition) -> ResolvedMetricDefinition:
        return ResolvedMetricDefinition(
            metric_definition_id=row.metric_definition_id,
            version=row.version,
            formula_id=row.formula_id,
            components=row.components,
            content_hash=row.content_hash,
            label=row.label,
        )
