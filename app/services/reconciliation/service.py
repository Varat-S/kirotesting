"""Zero-safe reconciliation + conflict detection + human correction (tasks 4.2, 4.3).

This module reconciles multiple extraction routes for the same numerical field
into a single resolved fact state, keeping conflicts VISIBLE and NEVER silently
merging. It is pure/deterministic apart from the explicit persistence helpers:
same inputs + same config versions yield the same reconciliation records.

Headline guarantees:

* **Zero-safe (Req 7.1, 7.2).** The system ALWAYS computes an absolute delta.
  It computes a *relative* delta ONLY when the denominator exceeds a versioned
  near-zero floor read from the ``tolerances`` config kind. Near the floor it
  falls back to an absolute tolerance -- it NEVER divides by a zero/near-zero
  denominator.
* **Comparison method recorded (Req 7.3).** Every comparison records a
  ``comparison_method`` from ``{relative, absolute, exact, definition_mismatch,
  not_comparable}``.
* **Compatibility before comparison (task 4.1 / Req 6).** Facts are routed
  through :func:`check_compatibility` first; any failure yields
  ``definition_mismatch`` and preserves both observations -- it does NOT
  auto-prefer the structured source.
* **Versioned, field-specific tolerances (Req 7.4).** Tolerances come from the
  ``tolerances`` config artifact and the record stores the ``tolerance_version``
  used. Illustrative values only -- NOT bank policy.
* **State resolution (Req 7.5-7.8).** verified | conflicting | unverified |
  missing.
* **No silent merge (Req 7.9, 7.10).** Conflicts preserve BOTH values and BOTH
  source refs. Non-numeric disagreement creates a contradiction record.
* **Exact dedup (Req 3.8).** Byte-for-byte identical facts are deduplicated.
* **Non-destructive human correction (Req 7.11, 7.12).** A correction is an
  additive record + ``fact_human_corrected`` event; the conflict row is never
  deleted and before/after remain recoverable.

Escalation boundary: a ``conflicting`` resolution is a forward-compatible signal
for the Milestone 5 escalation engine. This module emits
``fact_conflict_detected`` / ``fact_verified`` and persists the record; it does
NOT build the escalation engine or routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.schemas.enums import FactStatus
from app.schemas.evidence import CanonicalFact
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.reconciliation.compatibility import (
    CompatibilityResult,
    check_compatibility,
)

# ---------------------------------------------------------------------------
# Default versioned tolerances (illustrative -- NOT bank policy, Req 7.4).
# ---------------------------------------------------------------------------

# These are the DATA for the ``tolerances`` ConfigRegistry kind. The near-zero
# floor guards relative-delta division (Req 7.2). Field tolerances are relative
# unless an ``_abs`` absolute tolerance is supplied for near-zero comparisons.
DEFAULT_TOLERANCES: dict[str, Any] = {
    "label": "ILLUSTRATIVE — NOT BANK POLICY",
    "near_zero_floor": 1.0,
    "default_relative": 0.005,
    "default_absolute": 1.0,
    "fields": {
        # relative tolerance; illustrative values from requirements/design.
        "revenue": {"relative": 0.005, "absolute": 1.0},
        "total_debt": {"relative": 0.005, "absolute": 1.0},
        "debt": {"relative": 0.005, "absolute": 1.0},
        "ebitda": {"relative": 0.005, "absolute": 1.0},
        # ratios compare on an absolute 0.01x band.
        "ratio": {"relative": None, "absolute": 0.01},
    },
}


class ComparisonMethod(str, Enum):
    """How two values were compared (Req 7.3)."""

    RELATIVE = "relative"
    ABSOLUTE = "absolute"
    EXACT = "exact"
    DEFINITION_MISMATCH = "definition_mismatch"
    NOT_COMPARABLE = "not_comparable"


@dataclass(frozen=True)
class FieldTolerance:
    """Resolved tolerance for a single field, plus the near-zero floor."""

    relative: float | None
    absolute: float | None
    near_zero_floor: float
    version: int | None
    field_name: str


class ToleranceConfig:
    """Reads field-specific tolerances + near-zero floor from the registry.

    Resolution order for a field: an exact field entry, else the ``default_*``
    values. No silent magic numbers: everything comes from the versioned
    ``tolerances`` artifact (Req 7.4, 19.6, 21.1).
    """

    def __init__(self, content: dict[str, Any], version: int | None) -> None:
        self._content = content
        self._version = version

    @classmethod
    def from_registry(
        cls, registry: ConfigRegistry, *, register_default: bool = True
    ) -> "ToleranceConfig":
        """Load the latest ``tolerances`` artifact, registering a default if absent.

        Registering the default is explicit and versioned (it is NOT a silent
        in-code default): it goes through the ConfigRegistry so the version and
        hash are recorded (Req 21.1).
        """
        latest = registry.latest("tolerances")
        if latest is None:
            if not register_default:
                raise ValueError(
                    "No 'tolerances' configuration registered and "
                    "register_default=False (Req 21.1: no silent defaults)."
                )
            registered = registry.register(
                "tolerances", DEFAULT_TOLERANCES, label=DEFAULT_TOLERANCES["label"]
            )
            row = registry.get("tolerances", registered.version)
            return cls(row.content, registered.version)
        row = registry.get("tolerances", latest.version)
        return cls(row.content, latest.version)

    @property
    def version(self) -> int | None:
        return self._version

    @property
    def near_zero_floor(self) -> float:
        return float(self._content.get("near_zero_floor", 0.0))

    def for_field(self, field_name: str) -> FieldTolerance:
        fields = self._content.get("fields", {})
        entry = fields.get(field_name)
        if entry is None:
            relative = self._content.get("default_relative")
            absolute = self._content.get("default_absolute")
        else:
            relative = entry.get("relative", self._content.get("default_relative"))
            absolute = entry.get("absolute", self._content.get("default_absolute"))
        return FieldTolerance(
            relative=None if relative is None else float(relative),
            absolute=None if absolute is None else float(absolute),
            near_zero_floor=self.near_zero_floor,
            version=self._version,
            field_name=field_name,
        )


@dataclass
class ReconciliationResult:
    """In-memory reconciliation result (design.md "Reconciliation result").

    Mirrors the persisted :class:`~app.models.orm.ReconciliationRecord`. Keeps
    both values + both source refs on a conflict (no silent merge).
    """

    field: str
    values: list[float]
    source_refs: list[dict]
    fact_ids: list[str]
    comparison_method: ComparisonMethod
    resolved_state: FactStatus
    absolute_delta: float | None = None
    relative_delta: float | None = None
    near_zero_floor: float | None = None
    tolerance: float | None = None
    tolerance_version: int | None = None
    mismatch_dimensions: list[str] = field(default_factory=list)
    record_kind: str = "numeric"
    detail: str | None = None

    @property
    def within_tolerance(self) -> bool:
        return self.resolved_state is FactStatus.VERIFIED

    def as_payload(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "values": self.values,
            "source_refs": self.source_refs,
            "fact_ids": self.fact_ids,
            "absolute_delta": self.absolute_delta,
            "relative_delta": self.relative_delta,
            "near_zero_floor": self.near_zero_floor,
            "comparison_method": self.comparison_method.value,
            "tolerance": self.tolerance,
            "tolerance_version": self.tolerance_version,
            "resolved_state": self.resolved_state.value,
            "mismatch_dimensions": self.mismatch_dimensions,
            "record_kind": self.record_kind,
            "detail": self.detail,
        }


def _source_refs_payload(fact: CanonicalFact) -> list[dict]:
    return [ref.model_dump(mode="json") for ref in fact.source_refs]


def _fact_signature(fact: CanonicalFact) -> tuple:
    """Identity used to detect EXACT duplicate facts (Req 3.8).

    An exact duplicate has identical value, unit, period, entity, currency,
    scale, accounting basis, and restatement status *and* identical source
    references. Facts that merely share a value but differ in provenance are NOT
    duplicates.
    """
    return (
        fact.name,
        fact.normalized_value,
        fact.normalized_unit,
        fact.raw_value,
        fact.raw_unit,
        fact.currency,
        fact.scale,
        fact.period_start,
        fact.period_end,
        fact.period_type,
        fact.fiscal_year,
        fact.entity_id,
        fact.accounting_basis,
        fact.definition_version,
        fact.restated,
        tuple(tuple(sorted(r.model_dump(mode="json").items())) for r in fact.source_refs),
    )


def deduplicate_exact(facts: list[CanonicalFact]) -> list[CanonicalFact]:
    """Drop byte-for-byte identical facts, preserving first-seen order (Req 3.8)."""
    seen: set[tuple] = set()
    unique: list[CanonicalFact] = []
    for fact in facts:
        sig = _fact_signature(fact)
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(fact)
    return unique


class Reconciler:
    """Zero-safe reconciliation of numerical facts across extraction routes."""

    def __init__(
        self,
        tolerances: ToleranceConfig,
        *,
        session: Session | None = None,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._tol = tolerances
        self._session = session
        self._audit = audit
        self._case_id = case_id

    # -- pure comparison ------------------------------------------------------

    def compare_values(
        self, field_name: str, a: float, b: float
    ) -> ReconciliationResult:
        """Compare two already-compatible numeric values, zero-safe (Req 7.1-7.6).

        * Equal values -> ``exact``, ``verified``.
        * Denominator above the near-zero floor -> ``relative`` delta against the
          field's relative tolerance.
        * Denominator at/below the floor (includes 0-vs-0 and 0-vs-small) ->
          ``absolute`` delta against the field's absolute tolerance; NEVER
          divides.
        """
        tol = self._tol.for_field(field_name)
        absolute_delta = abs(a - b)

        if a == b:
            return ReconciliationResult(
                field=field_name,
                values=[a, b],
                source_refs=[],
                fact_ids=[],
                comparison_method=ComparisonMethod.EXACT,
                resolved_state=FactStatus.VERIFIED,
                absolute_delta=0.0,
                near_zero_floor=tol.near_zero_floor,
                tolerance=0.0,
                tolerance_version=tol.version,
            )

        # Zero-safe denominator: use the larger magnitude so neither a 0 nor a
        # near-zero value can be chosen as the divisor (Req 7.2).
        denominator = max(abs(a), abs(b))
        can_divide = (
            tol.relative is not None and denominator > tol.near_zero_floor
        )

        if can_divide:
            relative_delta = absolute_delta / denominator
            within = relative_delta <= tol.relative
            return ReconciliationResult(
                field=field_name,
                values=[a, b],
                source_refs=[],
                fact_ids=[],
                comparison_method=ComparisonMethod.RELATIVE,
                resolved_state=(
                    FactStatus.VERIFIED if within else FactStatus.CONFLICTING
                ),
                absolute_delta=absolute_delta,
                relative_delta=relative_delta,
                near_zero_floor=tol.near_zero_floor,
                tolerance=tol.relative,
                tolerance_version=tol.version,
            )

        # Fall back to absolute tolerance (no division). This path covers
        # 0-vs-0 (equal, handled above), 0-vs-small, and near-zero denominators.
        absolute_tol = tol.absolute if tol.absolute is not None else 0.0
        within = absolute_delta <= absolute_tol
        return ReconciliationResult(
            field=field_name,
            values=[a, b],
            source_refs=[],
            fact_ids=[],
            comparison_method=ComparisonMethod.ABSOLUTE,
            resolved_state=FactStatus.VERIFIED if within else FactStatus.CONFLICTING,
            absolute_delta=absolute_delta,
            near_zero_floor=tol.near_zero_floor,
            tolerance=absolute_tol,
            tolerance_version=tol.version,
        )

    def reconcile_pair(
        self, field_name: str, a: CanonicalFact, b: CanonicalFact
    ) -> ReconciliationResult:
        """Reconcile two facts: compatibility first, then zero-safe comparison.

        If compatibility fails, the result is ``definition_mismatch`` /
        ``conflicting`` and BOTH observations are preserved -- the structured
        source is NOT auto-preferred (Req 6.2). If a fact lacks a numeric value,
        the pair is ``not_comparable``.
        """
        compat: CompatibilityResult = check_compatibility(a, b)
        source_refs = _source_refs_payload(a) + _source_refs_payload(b)
        fact_ids = [a.fact_id, b.fact_id]
        values = [v for v in (a.normalized_value, b.normalized_value) if v is not None]

        if not compat.compatible:
            return ReconciliationResult(
                field=field_name,
                values=values,
                source_refs=source_refs,
                fact_ids=fact_ids,
                comparison_method=ComparisonMethod.DEFINITION_MISMATCH,
                resolved_state=FactStatus.CONFLICTING,
                near_zero_floor=self._tol.near_zero_floor,
                tolerance_version=self._tol.version,
                mismatch_dimensions=compat.mismatch_dimensions,
                detail=compat.summary(),
            )

        if a.normalized_value is None or b.normalized_value is None:
            return ReconciliationResult(
                field=field_name,
                values=values,
                source_refs=source_refs,
                fact_ids=fact_ids,
                comparison_method=ComparisonMethod.NOT_COMPARABLE,
                resolved_state=FactStatus.UNVERIFIED,
                near_zero_floor=self._tol.near_zero_floor,
                tolerance_version=self._tol.version,
                detail="One or both facts carry no numeric value to compare.",
            )

        result = self.compare_values(
            field_name, a.normalized_value, b.normalized_value
        )
        result.source_refs = source_refs
        result.fact_ids = fact_ids
        return result

    def reconcile_field(
        self, field_name: str, facts: list[CanonicalFact]
    ) -> ReconciliationResult:
        """Reconcile all facts for one field into a single resolved state.

        Resolution (Req 7.5-7.8): no facts -> ``missing``; one reliable fact ->
        ``unverified``; multiple comparable facts within tolerance -> ``verified``;
        otherwise ``conflicting``. Exact duplicates are removed first (Req 3.8).
        Conflicts preserve both values + refs (no silent merge, Req 7.9, 7.10).
        """
        value_facts = [
            f
            for f in deduplicate_exact(facts)
            if f.status not in {FactStatus.MISSING}
        ]

        if not value_facts:
            return ReconciliationResult(
                field=field_name,
                values=[],
                source_refs=[],
                fact_ids=[],
                comparison_method=ComparisonMethod.NOT_COMPARABLE,
                resolved_state=FactStatus.MISSING,
                near_zero_floor=self._tol.near_zero_floor,
                tolerance_version=self._tol.version,
                detail="No source provides this field (missing is never zero).",
            )

        if len(value_facts) == 1:
            only = value_facts[0]
            state = (
                FactStatus.UNVERIFIED
                if only.normalized_value is not None
                else FactStatus.MISSING
            )
            return ReconciliationResult(
                field=field_name,
                values=[only.normalized_value] if only.normalized_value is not None else [],
                source_refs=_source_refs_payload(only),
                fact_ids=[only.fact_id],
                comparison_method=ComparisonMethod.NOT_COMPARABLE,
                resolved_state=state,
                near_zero_floor=self._tol.near_zero_floor,
                tolerance_version=self._tol.version,
                detail="Only one reliable source; cannot cross-verify.",
            )

        # Reconcile pairwise against the first fact; any conflict/mismatch keeps
        # the field in a non-verified state with both values preserved.
        anchor = value_facts[0]
        worst: ReconciliationResult | None = None
        for other in value_facts[1:]:
            result = self.reconcile_pair(field_name, anchor, other)
            if worst is None or _severity(result.resolved_state) > _severity(
                worst.resolved_state
            ):
                worst = result
        assert worst is not None  # len >= 2 guaranteed above

        # Collect every value + ref so no observation is dropped (Req 7.10).
        all_values = [
            f.normalized_value
            for f in value_facts
            if f.normalized_value is not None
        ]
        all_refs: list[dict] = []
        all_ids: list[str] = []
        for f in value_facts:
            all_refs.extend(_source_refs_payload(f))
            all_ids.append(f.fact_id)
        worst.values = all_values
        worst.source_refs = all_refs
        worst.fact_ids = all_ids
        return worst

    # -- non-numeric contradiction -------------------------------------------

    def contradiction(
        self, field_name: str, a: CanonicalFact, b: CanonicalFact
    ) -> ReconciliationResult:
        """Create a contradiction record for non-numeric disagreement (Req 7.9).

        Used when two qualitative/textual facts disagree. The system records a
        contradiction rather than forcing a merge; both raw statements and both
        source refs are preserved.
        """
        return ReconciliationResult(
            field=field_name,
            values=[],
            source_refs=_source_refs_payload(a) + _source_refs_payload(b),
            fact_ids=[a.fact_id, b.fact_id],
            comparison_method=ComparisonMethod.NOT_COMPARABLE,
            resolved_state=FactStatus.CONFLICTING,
            near_zero_floor=self._tol.near_zero_floor,
            tolerance_version=self._tol.version,
            record_kind="non_numeric",
            detail=(
                f"Non-numeric disagreement: {a.raw_value!r} vs {b.raw_value!r}."
            ),
        )

    # -- persistence ----------------------------------------------------------

    def persist(self, result: ReconciliationResult) -> Any:
        """Persist a reconciliation result and emit the matching audit event.

        ``verified`` emits ``fact_verified``; ``conflicting`` emits
        ``fact_conflict_detected`` (the forward-compatible escalation signal).
        Requires a session; the audit event is emitted when an audit log is
        wired. The persisted row stores both values + both refs so a conflict is
        never silently merged (Req 7.10).
        """
        if self._session is None:
            raise ValueError("Reconciler.persist requires a SQLAlchemy session.")

        from app.models.orm import ReconciliationRecord

        row = ReconciliationRecord(
            case_id=self._case_id,
            field=result.field,
            values=list(result.values),
            source_refs=list(result.source_refs),
            fact_ids=list(result.fact_ids),
            absolute_delta=result.absolute_delta,
            relative_delta=result.relative_delta,
            near_zero_floor=result.near_zero_floor,
            comparison_method=result.comparison_method.value,
            tolerance=result.tolerance,
            tolerance_version=result.tolerance_version,
            resolved_state=result.resolved_state.value,
            mismatch_dimensions=list(result.mismatch_dimensions),
            record_kind=result.record_kind,
            detail=result.detail,
        )
        self._session.add(row)
        self._session.flush()

        if self._audit is not None:
            if result.resolved_state is FactStatus.VERIFIED:
                self._audit.record(
                    EventType.FACT_VERIFIED,
                    case_id=self._case_id,
                    actor_type=ActorType.SYSTEM,
                    after=result.as_payload(),
                    reason=f"Field {result.field!r} verified within tolerance.",
                    linked_objects=result.fact_ids,
                )
            elif result.resolved_state is FactStatus.CONFLICTING:
                self._audit.record(
                    EventType.FACT_CONFLICT_DETECTED,
                    case_id=self._case_id,
                    actor_type=ActorType.SYSTEM,
                    after=result.as_payload(),
                    reason=(
                        result.detail
                        or f"Field {result.field!r} conflicts across sources."
                    ),
                    linked_objects=result.fact_ids,
                )
        return row


def _severity(state: FactStatus) -> int:
    """Order states so the worst outcome wins in a multi-source field."""
    order = {
        FactStatus.VERIFIED: 0,
        FactStatus.UNVERIFIED: 1,
        FactStatus.MISSING: 2,
        FactStatus.CONFLICTING: 3,
    }
    return order.get(state, 1)


class HumanCorrectionWorkflow:
    """Non-destructive human resolution of a conflict (task 4.3).

    A correction is ADDITIVE: it creates a :class:`~app.models.orm.FactCorrection`
    row referencing the original :class:`~app.models.orm.ReconciliationRecord`
    and emits ``fact_human_corrected``. The conflict row is NEVER deleted or
    overwritten, so before/after states remain recoverable (Req 7.11, 7.12,
    15.2, 15.3).
    """

    def __init__(self, session: Session, audit: AuditLog | None = None) -> None:
        self._session = session
        self._audit = audit

    def correct(
        self,
        reconciliation_id: int,
        *,
        corrected_by: str,
        after_state: dict[str, Any],
        reason: str | None = None,
        case_id: str | None = None,
    ) -> Any:
        """Record a human correction without mutating the original conflict.

        ``after_state`` is the reviewer's chosen resolution (e.g. the accepted
        value + which source they trust). The prior reconciliation payload is
        snapshotted into ``before_state`` for recoverability.
        """
        from app.models.orm import FactCorrection, ReconciliationRecord

        record = self._session.get(ReconciliationRecord, reconciliation_id)
        if record is None:
            raise ValueError(
                f"No reconciliation record with id={reconciliation_id!r} to correct."
            )

        before_state = {
            "field": record.field,
            "values": record.values,
            "source_refs": record.source_refs,
            "comparison_method": record.comparison_method,
            "resolved_state": record.resolved_state,
            "mismatch_dimensions": record.mismatch_dimensions,
        }

        correction = FactCorrection(
            case_id=case_id if case_id is not None else record.case_id,
            reconciliation_id=record.id,
            field=record.field,
            before_state=before_state,
            after_state=after_state,
            corrected_by=corrected_by,
            reason=reason,
        )
        self._session.add(correction)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.FACT_HUMAN_CORRECTED,
                case_id=correction.case_id,
                actor_type=ActorType.HUMAN,
                actor_id=corrected_by,
                before=before_state,
                after=after_state,
                reason=reason,
                linked_objects=[f"reconciliation:{record.id}", *record.fact_ids],
            )

        # The original conflict row is intentionally left intact (Req 7.11).
        return correction

    def history(self, reconciliation_id: int) -> list[Any]:
        """Return all corrections for a reconciliation record, oldest first."""
        from app.models.orm import FactCorrection

        return list(
            self._session.query(FactCorrection)
            .filter(FactCorrection.reconciliation_id == reconciliation_id)
            .order_by(FactCorrection.created_at.asc(), FactCorrection.id.asc())
            .all()
        )
