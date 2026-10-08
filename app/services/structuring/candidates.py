"""Assemble immutable CandidateStructures (Milestone 16 / Req 36).

A candidate is a typed, IMMUTABLE proposal built from extracted facility/
collateral/covenant terms plus bounded mitigant proposals. It carries no
feasibility verdict and no mutable ``selected`` flag — feasibility is a separate
append-only ``CandidateFeasibility`` and selection lives on the
StructuringConclusion.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.schemas.agentic import CandidateStructure


def assemble_candidate(
    *,
    analysis_run_id: str,
    facility_amount: float | None = None,
    tenor_months: int | None = None,
    amortization: dict[str, Any] | None = None,
    covenant_package: list[dict[str, Any]] | None = None,
    collateral: list[dict[str, Any]] | None = None,
    guarantees: list[dict[str, Any]] | None = None,
    liquidity_protection: dict[str, Any] | None = None,
    cash_sweep: dict[str, Any] | None = None,
    hedge_requirements: list[dict[str, Any]] | None = None,
    reporting_requirements: list[dict[str, Any]] | None = None,
    other_protections: list[dict[str, Any]] | None = None,
    candidate_id: str | None = None,
) -> CandidateStructure:
    """Build one immutable CandidateStructure proposal."""
    return CandidateStructure(
        candidate_id=candidate_id or f"cs_{uuid.uuid4().hex[:16]}",
        analysis_run_id=analysis_run_id,
        facility_amount=facility_amount,
        tenor_months=tenor_months,
        amortization=amortization,
        covenant_package=list(covenant_package or []),
        collateral=list(collateral or []),
        guarantees=list(guarantees or []),
        liquidity_protection=liquidity_protection,
        cash_sweep=cash_sweep,
        hedge_requirements=list(hedge_requirements or []),
        reporting_requirements=list(reporting_requirements or []),
        other_protections=list(other_protections or []),
    )
