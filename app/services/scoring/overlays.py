"""Deterministic scoring overlays — floors / overrides (Milestone 9 / Req 9.7-9.8).

Overlays dominate weighted averages so a severe risk is never washed out by many
benign parameters. They are configuration-driven (``scoring.json`` ``obligor.floors``)
and labelled illustrative. Overlays operate ONLY on risk bands and explicit risk
conditions — never on evidence quality (Req 10).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OverlayResult:
    band: int
    applied: list[str]


def apply_floors(
    base_band: int,
    *,
    floors: list[dict],
    conditions: dict[str, bool],
    key: str,
) -> OverlayResult:
    """Raise ``base_band`` to any configured floor whose condition holds.

    ``floors`` is the config list; each entry is ``{"if": <condition>, "<key>": N}``.
    A floor applies only if ``conditions[<condition>]`` is truthy and the entry
    names ``key`` (e.g. ``financial_min_band`` or ``obligor_min_band``). The band
    can only get WORSE (higher) via a floor, never better.
    """
    band = base_band
    applied: list[str] = []
    for floor in floors:
        condition = floor.get("if")
        if condition and conditions.get(condition) and key in floor:
            floor_band = int(floor[key])
            if floor_band > band:
                band = floor_band
            applied.append(condition)
    return OverlayResult(band=band, applied=applied)
