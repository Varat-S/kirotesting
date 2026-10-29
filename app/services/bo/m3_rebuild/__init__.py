"""Module 3: deterministic corporate conversion and filing-only management."""

VERSION = "m3-rebuild-1.0"
# Marker on the handoff block this workflow produces (recorded in persisted artifacts).
M3_WORKFLOW = "m3-rebuild"


def is_m3_block(module: dict | None) -> bool:
    """True for the Module 3 conclusion block this workflow produced."""
    return isinstance(module, dict) and module.get("workflow") == M3_WORKFLOW
