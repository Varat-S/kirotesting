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
    """The illustrative default deterministic parameter set (Req 7.2-7.4)."""
    return [
        # Business concentration / stability
        ParameterDefinition("segment_hhi", Topic.BUSINESS, "hhi", "index",
                            description="Segment revenue concentration (HHI)."),
        ParameterDefinition("geographic_hhi", Topic.BUSINESS, "hhi", "index",
                            description="Geographic revenue concentration (HHI)."),
        ParameterDefinition("customer_hhi", Topic.BUSINESS, "hhi", "index",
                            description="Customer concentration (HHI)."),
        ParameterDefinition("top5_customer_concentration", Topic.BUSINESS,
                            "top_n_concentration", "ratio",
                            description="Top-5 customer share of revenue."),
        ParameterDefinition("revenue_volatility", Topic.BUSINESS, "volatility", "ratio",
                            description="Coefficient of variation of revenue."),
        # Financial trend / ratios
        ParameterDefinition("revenue_cagr", Topic.FINANCIAL, "cagr", "ratio",
                            description="Revenue CAGR."),
        ParameterDefinition("margin_trend", Topic.FINANCIAL, "trend_direction", "number",
                            description="Operating-margin trend slope."),
        ParameterDefinition("fcf_conversion", Topic.FINANCIAL, "safe_ratio", "ratio",
                            description="FCF / EBITDA conversion."),
        ParameterDefinition("maturity_to_ebitda", Topic.FINANCIAL, "safe_ratio", "ratio",
                            description="Upcoming maturities / EBITDA."),
        # Financial stress
        ParameterDefinition("downside_ebitda", Topic.FINANCIAL, "downside_stress",
                            "number", description="Base-case EBITDA under downside shock."),
        # Structuring
        ParameterDefinition("ltv", Topic.STRUCTURING, "safe_ratio", "ratio",
                            description="Loan-to-value under a candidate structure."),
        ParameterDefinition("bullet_to_fcf", Topic.STRUCTURING, "safe_ratio", "ratio",
                            description="Bullet exposure / FCF."),
    ]
