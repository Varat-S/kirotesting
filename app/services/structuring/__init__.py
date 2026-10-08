"""Deterministic structuring feasibility engine (Milestone 16).

Answers "how do we protect the bank?" with deterministic tests, never an LLM:
assemble IMMUTABLE candidate structures from extracted terms + bounded mitigant
proposals (``candidates``), test each candidate's feasibility and policy
compliance (``engine``), and run base/downside performance (``stress``). An
infeasible candidate cannot be selected; selection itself is recorded on the
StructuringConclusion (M17), never as a mutable flag on the candidate.
"""

from __future__ import annotations

from app.services.structuring.candidates import assemble_candidate
from app.services.structuring.engine import StructuringEngine
from app.services.structuring.stress import stress_candidate

__all__ = ["assemble_candidate", "StructuringEngine", "stress_candidate"]
