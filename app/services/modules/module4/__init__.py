"""Module 4 -- Debt Structure, Refinancing History & Debt Utilisation.

The debt-structure specialist, one of Module 4's two agents (the other,
``m4_strategy_capital``, owns acquisition/capex strategy and capital-allocation
stewardship judgement; this agent owns the debt and its leverage impact, with no
overlap). An INDEPENDENT specialist agent. It reads the shared SEC 10-K +
structured financial data and produces four deliverables
(see :mod:`app.schemas.module4`).
It does NOT consume Modules 1/2/3 outputs and does NOT draft loan term sheets,
covenants or security agreements.

Public surface:

* :data:`MODULE4_AGENT_NAME`, :data:`MODULE4_PROMPT` -- the versioned agent
  prompt data registered into the prompt catalogue.
* :class:`Module4Specialist` -- orchestrates the LLM call + debt-structure validator.
* :class:`DebtStructureValidator`, :class:`DebtStructureValidatorReport` -- the
  deterministic debt-structure checks.

Note: the heavier service symbols (:class:`Module4Specialist`, the validator
types) are exposed lazily via :func:`__getattr__` so that importing the prompt
data (consumed by :mod:`app.prompts.templates`) does NOT pull in the LLM client
and create an import cycle (``templates -> module4 -> client -> registry ->
templates``). Import the submodules directly for eager access.
"""

from __future__ import annotations

from typing import Any

from app.services.modules.module4.prompt import (
    MODULE4_AGENT_NAME,
    MODULE4_PROMPT,
    MODULE4_ROLE,
)

__all__ = [
    "MODULE4_AGENT_NAME",
    "MODULE4_PROMPT",
    "MODULE4_ROLE",
    "Module4Inputs",
    "Module4RejectedError",
    "Module4Result",
    "Module4Specialist",
    "DebtStructureValidator",
    "DebtStructureValidatorCheck",
    "DebtStructureValidatorReport",
]

_LAZY: dict[str, tuple[str, str]] = {
    "Module4Inputs": ("specialist", "Module4Inputs"),
    "Module4RejectedError": ("specialist", "Module4RejectedError"),
    "Module4Result": ("specialist", "Module4Result"),
    "Module4Specialist": ("specialist", "Module4Specialist"),
    "DebtStructureValidator": ("debt_structure_validator", "DebtStructureValidator"),
    "DebtStructureValidatorCheck": ("debt_structure_validator", "DebtStructureValidatorCheck"),
    "DebtStructureValidatorReport": ("debt_structure_validator", "DebtStructureValidatorReport"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{target[0]}")
    return getattr(module, target[1])
