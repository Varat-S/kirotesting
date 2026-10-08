"""Deterministic parameter definition registry (Milestone 7).

Maps a ``parameter_id`` to the versioned deterministic formula that computes it.
Mirrors ``MetricDefinitionRegistry``: each definition is content-hashed and the
``formula_version`` is reproducible. A parameter with no registered definition is
treated as a ``PROPOSED_NEW_CALCULATION`` by the engine (Req 3.5) and cannot
affect official analysis until implemented/reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.hashing import content_hash
from app.schemas.agentic import Topic
from app.services.parameters.formulas import FORMULA_LIBRARY


@dataclass(frozen=True)
class ParameterDefinition:
    """A deterministic parameter: which formula computes it, under what version."""

    parameter_id: str
    topic: Topic
    formula_id: str
    value_type: str  # ratio|number|index|category
    units: str | None = None
    description: str | None = None
    version: str = "1"

    def __post_init__(self) -> None:
        if self.formula_id not in FORMULA_LIBRARY:
            raise ValueError(
                f"Parameter {self.parameter_id!r} references unregistered formula "
                f"{self.formula_id!r}; a proposed calculation must be implemented "
                "in the deterministic engine before use (Req 3.5)."
            )

    @property
    def formula_version(self) -> str:
        """Content hash over the definition (its reproducible identity)."""
        return content_hash(
            {
                "parameter_id": self.parameter_id,
                "formula_id": self.formula_id,
                "value_type": self.value_type,
                "units": self.units,
                "version": self.version,
            }
        )


class ParameterDefinitionRegistry:
    """Append-only registry of deterministic parameter definitions."""

    def __init__(self, definitions: list[ParameterDefinition] | None = None) -> None:
        self._by_id: dict[str, ParameterDefinition] = {}
        for d in definitions or default_parameter_definitions():
            self.register(d)

    def register(self, definition: ParameterDefinition) -> ParameterDefinition:
        existing = self._by_id.get(definition.parameter_id)
        if existing is not None and existing.formula_version != definition.formula_version:
            raise ValueError(
                f"Parameter {definition.parameter_id!r} already registered with a "
                "different definition; register a new parameter_id instead of "
                "mutating a versioned one."
            )
        self._by_id[definition.parameter_id] = definition
        return definition

    def get(self, parameter_id: str) -> ParameterDefinition | None:
        return self._by_id.get(parameter_id)

    def all(self) -> list[ParameterDefinition]:
        return list(self._by_id.values())

    def __contains__(self, parameter_id: object) -> bool:
        return parameter_id in self._by_id


def default_parameter_definitions() -> list[ParameterDefinition]:
    """The illustrative default deterministic parameter set (Req 7.2-7.4).

    Composed from the per-topic modules (``business`` / ``financial`` /
    ``structuring``) so the three deterministic domains stay separable, matching
    the design intent.
    """
    # Imported lazily to avoid a circular import (the per-topic modules import
    # ParameterDefinition from this module).
    from app.services.parameters.business import business_parameter_definitions
    from app.services.parameters.financial import financial_parameter_definitions
    from app.services.parameters.structuring import structuring_parameter_definitions

    return [
        *business_parameter_definitions(),
        *financial_parameter_definitions(),
        *structuring_parameter_definitions(),
    ]
