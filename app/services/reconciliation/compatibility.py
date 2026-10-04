"""Definition-compatibility checks before numerical comparison (task 4.1).

Two financial facts must NOT be treated as equivalent until they are known to be
comparable. Before any value comparison, the system checks:

* **entity** -- the facts describe the same ``entity_id``;
* **period** -- same ``period_start``/``period_end``/``period_type``/
  ``fiscal_year``;
* **unit / scale** -- same normalized unit (scale differences are a mismatch,
  never silently rescaled during comparison);
* **currency** -- same currency;
* **accounting definition** -- same ``accounting_basis`` (e.g. GAAP vs
  management-adjusted) and ``definition_version``;
* **restatement status** -- compatible ``restated`` flags (an original vs a
  restated figure is a definition mismatch, not a plain disagreement);
* **source authority** -- recorded, but it NEVER acts as a single global ranking
  that erases disagreement (Req 6.3).

The structured value wins ONLY when every check passes (Req 6.1). If ANY check
fails the system does NOT auto-prefer the structured source: it preserves both
observations, marks ``definition_mismatch``, and routes for reconciliation
(Req 6.2, 3.7). There is no global source ranking that erases disagreement.

Everything here is pure and deterministic -- no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.schemas.evidence import CanonicalFact


class CompatibilityDimension(str, Enum):
    """A single dimension that must match before two facts are comparable."""

    ENTITY = "entity"
    CONSOLIDATION_SCOPE = "consolidation_scope"
    PERIOD = "period"
    UNIT_SCALE = "unit_scale"
    CURRENCY = "currency"
    ACCOUNTING_DEFINITION = "accounting_definition"
    RESTATEMENT = "restatement"


@dataclass(frozen=True)
class CompatibilityCheck:
    """Outcome of a single compatibility dimension."""

    dimension: CompatibilityDimension
    compatible: bool
    expected: object | None
    observed: object | None
    detail: str


@dataclass(frozen=True)
class CompatibilityResult:
    """Aggregate compatibility of two facts across all dimensions.

    ``compatible`` is True only when every dimension passed. ``failures`` lists
    the dimensions that disagreed, so a reconciliation record can explain
    exactly why two facts were ruled a ``definition_mismatch``.
    """

    compatible: bool
    checks: list[CompatibilityCheck] = field(default_factory=list)

    @property
    def failures(self) -> list[CompatibilityCheck]:
        return [c for c in self.checks if not c.compatible]

    @property
    def mismatch_dimensions(self) -> list[str]:
        return [c.dimension.value for c in self.failures]

    def summary(self) -> str:
        if self.compatible:
            return "compatible"
        return "incompatible: " + ", ".join(
            f"{c.dimension.value} ({c.detail})" for c in self.failures
        )


def _norm(value: object | None) -> object | None:
    """Case/space-insensitive normalization for string-like fields."""
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def _effective_unit(fact: CanonicalFact) -> object | None:
    """The unit that governs comparison: normalized unit if present, else raw."""
    return _norm(
        fact.normalized_unit if fact.normalized_unit is not None else fact.raw_unit
    )


def check_compatibility(a: CanonicalFact, b: CanonicalFact) -> CompatibilityResult:
    """Return a :class:`CompatibilityResult` comparing two facts.

    Source authority is intentionally NOT a pass/fail dimension here: it is
    recorded metadata used only to pick a preferred *display* value when the
    facts are otherwise fully compatible, never to erase a disagreement
    (Req 6.3). The caller consults :func:`prefer_structured` for that.
    """
    checks: list[CompatibilityCheck] = []
    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.CONSOLIDATION_SCOPE,
            _norm(a.consolidation_scope) == _norm(b.consolidation_scope),
            a.consolidation_scope,
            b.consolidation_scope,
            "consolidation scope must match",
        )
    )

    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.ENTITY,
            a.entity_id == b.entity_id,
            a.entity_id,
            b.entity_id,
            "entity_id must match",
        )
    )

    period_a = (a.period_start, a.period_end, _norm(a.period_type), a.fiscal_year)
    period_b = (b.period_start, b.period_end, _norm(b.period_type), b.fiscal_year)
    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.PERIOD,
            period_a == period_b,
            period_a,
            period_b,
            "period (start/end/type/fiscal_year) must match",
        )
    )

    unit_a, unit_b = _effective_unit(a), _effective_unit(b)
    scale_a, scale_b = _norm(a.scale), _norm(b.scale)
    unit_scale_ok = unit_a == unit_b and scale_a == scale_b
    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.UNIT_SCALE,
            unit_scale_ok,
            (unit_a, scale_a),
            (unit_b, scale_b),
            "unit/scale must match (no silent rescaling)",
        )
    )

    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.CURRENCY,
            _norm(a.currency) == _norm(b.currency),
            a.currency,
            b.currency,
            "currency must match",
        )
    )

    def_a = (_norm(a.accounting_basis), _norm(a.definition_version))
    def_b = (_norm(b.accounting_basis), _norm(b.definition_version))
    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.ACCOUNTING_DEFINITION,
            def_a == def_b,
            def_a,
            def_b,
            "accounting basis / definition version must match",
        )
    )

    checks.append(
        CompatibilityCheck(
            CompatibilityDimension.RESTATEMENT,
            a.restated == b.restated,
            a.restated,
            b.restated,
            "restatement status must be compatible",
        )
    )

    compatible = all(c.compatible for c in checks)
    return CompatibilityResult(compatible=compatible, checks=checks)


# Source-authority ordering is DISPLAY metadata only. It is used to choose which
# value to show first when two facts are *fully compatible* (Req 6.1); it is
# NEVER used to silence a disagreement between incompatible facts (Req 6.3).
STRUCTURED_METHODS: frozenset[str] = frozenset({"xbrl", "xlsx", "csv", "pdf_table"})


def is_structured(fact: CanonicalFact) -> bool:
    """True when a fact came from a structured/typed extraction route."""
    method = (
        fact.extraction_method.value if fact.extraction_method is not None else None
    )
    return method in STRUCTURED_METHODS


def prefer_structured(a: CanonicalFact, b: CanonicalFact) -> CanonicalFact | None:
    """Return the structured fact to prefer, ONLY when the pair is compatible.

    Returns ``None`` when the facts are not compatible (so the caller must NOT
    prefer either -- both observations are preserved and routed to
    reconciliation, Req 6.2) or when neither/both are structured.
    """
    if not check_compatibility(a, b).compatible:
        return None
    a_struct, b_struct = is_structured(a), is_structured(b)
    if a_struct and not b_struct:
        return a
    if b_struct and not a_struct:
        return b
    return None
