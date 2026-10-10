"""Versioned sector benchmarking configuration (medical devices and beyond).

A sector configuration is a labelled JSON artifact (e.g.
``config/healthcare/medical_devices.json``) that declares the sector, its
classification rules, the workbook mapping, the benchmark metric definitions,
the borrower metric definitions, comparability rules and warnings. It is
registered through the existing :class:`~app.core.config_registry.ConfigRegistry`
(optional kind ``sector_benchmark.<sector_id>``), so it is content-hashed,
append-only versioned and audited exactly like every other configuration.

No benchmark VALUE lives in this file or in Python source: values come only
from the validated reference dataset imported from the workbook.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config_registry import ConfigRegistry

CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"
SECTOR_CONFIG_PATHS: dict[str, Path] = {
    "medical_devices": CONFIG_ROOT / "healthcare" / "medical_devices.json",
}

SECTOR_KIND_PREFIX = "sector_benchmark."
DATASET_KIND_PREFIX = "industry_reference_dataset."

_REQUIRED_KEYS = (
    "label", "sector_id", "display_name", "analytical_status", "benchmark",
    "workbook_mapping", "benchmark_metrics", "borrower_metrics", "comparisons",
    "comparability", "classification", "required_metadata",
)
# Official scoring thresholds live only in the ``scoring`` artifact; a sector
# benchmark configuration may never carry them.
_FORBIDDEN_SCORING_KEYS = (
    "parameter_bands", "financial_weights", "business_weights", "floors", "rubrics",
)


class SectorConfigError(ValueError):
    """The sector configuration is missing, unlabelled or internally invalid."""


@dataclass(frozen=True)
class SectorConfig:
    """A registered sector configuration with its version + hash."""

    content: dict[str, Any]
    version: int
    content_hash: str

    @property
    def sector_id(self) -> str:
        return self.content["sector_id"]

    @property
    def kind(self) -> str:
        return SECTOR_KIND_PREFIX + self.sector_id

    @property
    def dataset_kind(self) -> str:
        return DATASET_KIND_PREFIX + self.sector_id

    @property
    def industry_label(self) -> str:
        return self.content["benchmark"]["industry_label"]

    @property
    def method(self) -> str:
        return self.content["benchmark"]["method"]

    def __getitem__(self, key: str) -> Any:
        return self.content[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.content.get(key, default)

    def identity(self) -> dict[str, Any]:
        return {
            "sector_id": self.sector_id,
            "display_name": self.content["display_name"],
            "memo_section_title": self.content.get(
                "memo_section_title", "Industry Benchmarking"
            ),
            "config_kind": self.kind,
            "config_version": self.version,
            "config_hash": self.content_hash,
            "analytical_status": self.content["analytical_status"],
            "label": self.content["label"],
        }


def validate_sector_config(content: dict[str, Any]) -> None:
    """Structural validation; raises :class:`SectorConfigError`."""
    missing = [k for k in _REQUIRED_KEYS if k not in content]
    if missing:
        raise SectorConfigError(f"Sector configuration is missing {missing}.")
    if not str(content["label"]).startswith("ILLUSTRATIVE"):
        raise SectorConfigError(
            "Sector configuration must declare its illustrative status."
        )
    if content["analytical_status"] not in {"illustrative", "analytically_validated"}:
        raise SectorConfigError(
            "analytical_status must be 'illustrative' or 'analytically_validated'."
        )
    industry = content["benchmark"]["industry_label"]
    if industry not in content["workbook_mapping"]["expected_industries"]:
        raise SectorConfigError(
            f"Benchmark industry {industry!r} is not a mapped industry."
        )
    borrower, bench = content["borrower_metrics"], content["benchmark_metrics"]
    seen: set[str] = set()
    for comparison in content["comparisons"]:
        cid = comparison["comparison_id"]
        if cid in seen:
            raise SectorConfigError(f"Duplicate comparison_id {cid!r}.")
        seen.add(cid)
        if comparison["borrower_metric"] not in borrower:
            raise SectorConfigError(
                f"Comparison {cid!r} names unknown borrower metric "
                f"{comparison['borrower_metric']!r}."
            )
        target = comparison["benchmark_metric"]
        if target is not None and target not in bench:
            raise SectorConfigError(
                f"Comparison {cid!r} names unknown benchmark metric {target!r}."
            )
    mapped = {r["benchmark_metric_id"] for r in content["workbook_mapping"]["metric_rows"]}
    unknown = sorted(mapped - set(bench))
    if unknown:
        raise SectorConfigError(
            f"Workbook rows map to undefined benchmark metrics {unknown}."
        )
    for name, spec in bench.items():
        if spec.get("use") not in {"comparison", "context_only", "diagnostic_only"}:
            raise SectorConfigError(f"Benchmark metric {name!r} has an invalid 'use'.")
    for forbidden in _FORBIDDEN_SCORING_KEYS:
        if forbidden in content:
            raise SectorConfigError(
                f"Sector configuration may not define scoring key {forbidden!r}; "
                "official scoring thresholds live only in the 'scoring' artifact."
            )


def load_sector_config_content(
    sector_id: str, path: Path | None = None
) -> dict[str, Any]:
    target = path or SECTOR_CONFIG_PATHS.get(sector_id)
    if target is None or not Path(target).is_file():
        raise SectorConfigError(f"No sector configuration for {sector_id!r}.")
    content = json.loads(Path(target).read_text(encoding="utf-8"))
    validate_sector_config(content)
    if content["sector_id"] != sector_id:
        raise SectorConfigError(
            f"Configuration declares sector {content['sector_id']!r}, "
            f"not {sector_id!r}."
        )
    return content


def register_sector_config(
    registry: ConfigRegistry,
    sector_id: str,
    *,
    path: Path | None = None,
    content: dict[str, Any] | None = None,
) -> SectorConfig:
    """Register (idempotently) and return the sector configuration."""
    if content is None:
        content = load_sector_config_content(sector_id, path)
    else:
        validate_sector_config(content)
    view = registry.register(
        SECTOR_KIND_PREFIX + sector_id, content, label=content["label"]
    )
    return SectorConfig(
        content=content, version=view.version, content_hash=view.content_hash
    )


def register_reference_dataset(
    registry: ConfigRegistry, config: SectorConfig, dataset: dict[str, Any]
) -> tuple[int, str]:
    """Register the normalized reference dataset; returns ``(version, hash)``.

    The dataset is immutable by construction: identical content returns the
    existing version; changed content (a different workbook or mapping)
    allocates a new version and never rewrites the old one.
    """
    view = registry.register(
        config.dataset_kind,
        dataset,
        label=(
            "Workbook-derived industry reference dataset "
            f"({dataset['source']['sha256'][:12]})"
        ),
    )
    return view.version, view.content_hash
