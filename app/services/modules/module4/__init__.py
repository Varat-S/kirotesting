"""Module 4 -- Debt Structure, Refinancing History & Financial Stewardship.

An INDEPENDENT specialist agent. It reads the shared SEC 10-K + structured
financial data and produces four deliverables (see :mod:`app.schemas.module4`).
It does NOT consume Modules 1/2/3 outputs and does NOT draft loan term sheets,
covenants or security agreements.

Public surface:

* :data:`MODULE4_AGENT_NAME`, :data:`MODULE4_PROMPT` -- the versioned agent
  prompt data registered into the prompt catalogue.
* :class:`Module4Specialist` -- orchestrates the LLM call + Validator 8.
* :class:`Validator8`, :class:`Validator8Report` -- the deterministic checks.

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
    "Validator8",
    "Validator8Check",
    "Validator8Report",
]

_LAZY: dict[str, tuple[str, str]] = {
    "Module4Inputs": ("specialist", "Module4Inputs"),
    "Module4RejectedError": ("specialist", "Module4RejectedError"),
    "Module4Result": ("specialist", "Module4Result"),
    "Module4Specialist": ("specialist", "Module4Specialist"),
    "Validator8": ("validator8", "Validator8"),
    "Validator8Check": ("validator8", "Validator8Check"),
    "Validator8Report": ("validator8", "Validator8Report"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f"{__name__}.{target[0]}")
    return getattr(module, target[1])
