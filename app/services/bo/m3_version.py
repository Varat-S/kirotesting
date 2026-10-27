"""Which Module 3 implementation a Business Overview run uses.

* ``v1`` (M3-v1, the default): the single ``m3_corporate_management`` specialist
  run through ``m3_standalone.run_m3`` and governed by ``m3_validation``
  (Validator 7). It runs with the v3 roster only.
* ``v2`` (M3-v2, the rebuild in ``app/services/bo/m3_rebuild``): two
  Python-orchestrated components (corporate-structure conversion and filing-only
  management assessment) over sources declared in ``SourcePackage.module3``. The
  old specialist is not scheduled; ``module_conclusions["M3"]`` is the rebuild's
  ``workflow == "m3-rebuild"`` block.

M3-v1 may be dropped. Code that exists only for it branches on
``is_m3_v2``/``m3_version`` or on the ``"m3-rebuild"`` workflow marker, so
retiring it means deleting the v1 side of those branches.
"""

from __future__ import annotations

M3_VERSIONS = ("v1", "v2")
DEFAULT_M3_VERSION = "v1"
M3_V2_WORKFLOW = "m3-rebuild"
M3_VERSION_LABELS = {
    "v1": "M3-v1: corporate-management specialist + Validator 7",
    "v2": "M3-v2: rebuild (structure conversion + management continuity)",
}


def check_m3_version(value: str | None) -> str:
    """The validated version, or the default when none is given."""
    version = value or DEFAULT_M3_VERSION
    if version not in M3_VERSIONS:
        raise ValueError(f"m3_version must be one of {list(M3_VERSIONS)}, not {value!r}.")
    return version


def is_m3_v2_block(module: dict | None) -> bool:
    """True for an M3-v2 module conclusion (the rebuild's handoff block)."""
    return isinstance(module, dict) and module.get("workflow") == M3_V2_WORKFLOW
