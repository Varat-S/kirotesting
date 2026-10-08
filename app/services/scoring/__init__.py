"""Deterministic scoring subsystem (Milestone 9).

Computes ALL official scores deterministically from validated ParameterResults
and a versioned, hashed ``scoring`` config artifact. No LLM assigns or modifies
an official score. Scores may be ``final`` / ``provisional`` / ``unavailable`` /
``not_applicable``; missing weighted dimensions are tracked explicitly and are
NEVER silently renormalized. Credit risk is kept strictly separate from evidence
quality: a low-quality result marks the score provisional/unavailable but never
moves a risk band.
"""

from __future__ import annotations

from app.services.scoring.engine import ScoringConfig, ScoringEngine

__all__ = ["ScoringEngine", "ScoringConfig"]
