"""Load explicit, labelled PoC artifacts through the version/hash registry."""

import json
from pathlib import Path

from app.core.config_registry import ARTIFACT_KINDS, ConfigRegistry

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config" / "poc"


def bootstrap_config(
    registry: ConfigRegistry, root: Path = CONFIG_ROOT
) -> dict[str, int]:
    versions = {}
    for kind in sorted(ARTIFACT_KINDS):
        content = json.loads((root / f"{kind}.json").read_text(encoding="utf-8"))
        if not content.get("label", "").startswith("ILLUSTRATIVE"):
            raise ValueError(
                f"Configuration {kind} must declare its illustrative status."
            )
        versions[kind] = registry.register(
            kind, content, label=content["label"]
        ).version
    return versions
