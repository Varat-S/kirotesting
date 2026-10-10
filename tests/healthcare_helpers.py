"""Shared helpers for the healthcare / medical-device benchmarking tests."""

from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.core.config_registry import ConfigRegistry
from app.core.hashing import content_hash
from app.models.base import create_engine_and_session, init_db
from app.services.benchmarking.sector_config import SectorConfig
from app.services.benchmarking.workbook import import_workbook
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline

REPO = Path(__file__).resolve().parents[1]
HEALTHCARE = REPO / "examples" / "healthcare"
WORKBOOK = HEALTHCARE / "reference" / "Healthcare_Financial_Overview_Benchmarks.xlsx"
PACKAGE = HEALTHCARE / "stryker_fixture_package.json"
SECTOR_CONFIG = REPO / "config" / "healthcare" / "medical_devices.json"
WORKBOOK_SHA256 = "25ae2845901d77a9bb3e798a6da63b0b660088734c20c0ad0d4dc7c72241baa2"
BENCH_SHEET_XML = "xl/worksheets/sheet3.xml"

# The reference workbook is kept out of the public repository. Tests that need
# it skip when it is absent and run wherever it has been placed locally.
requires_workbook = pytest.mark.skipif(
    not WORKBOOK.is_file(),
    reason="Benchmark reference workbook not present (kept local; see "
           "examples/healthcare/README.md).",
)


def sector_content() -> dict:
    return json.loads(SECTOR_CONFIG.read_text(encoding="utf-8"))


def sector_config(content: dict | None = None, version: int = 1) -> SectorConfig:
    content = content or sector_content()
    return SectorConfig(content=content, version=version,
                        content_hash=content_hash(content))


def workbook_bytes() -> bytes:
    return WORKBOOK.read_bytes()


def imported(data: bytes | None = None, content: dict | None = None):
    return import_workbook(data or workbook_bytes(), content or sector_content(),
                           filename=WORKBOOK.name)


def edited_workbook(edits: dict[tuple[str, str], object]) -> bytes:
    """A modified COPY of the workbook (the original file is never written).

    openpyxl drops cached formula results on save, so the importer must
    re-derive every formula from literal inputs for such a copy.
    """
    book = load_workbook(io.BytesIO(workbook_bytes()))
    for (sheet, cell), value in edits.items():
        book[sheet][cell].value = value
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def with_cached_error(cell: str, error: str = "#NAME?") -> bytes:
    """A copy whose CACHED result for a Benchmarks formula cell is an Excel error.

    Rewrites only the cached ``<v>`` of one cell inside the zip, leaving the
    formula intact — the shape Excel leaves behind when a formula could not be
    evaluated.
    """
    source = zipfile.ZipFile(io.BytesIO(workbook_bytes()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == BENCH_SHEET_XML:
                text = data.decode("utf-8")
                pattern = re.compile(
                    rf'<c r="{cell}"( s="\d+")?><f>(.*?)</f><v>[^<]*</v></c>')
                assert pattern.search(text), f"formula cell {cell} not found"
                text = pattern.sub(
                    rf'<c r="{cell}"\1 t="e"><f>\2</f><v>{error}</v></c>', text, count=1)
                data = text.encode("utf-8")
            target.writestr(item, data)
    return out.getvalue()


def new_session():
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return factory()


def load_package(**sector_overrides) -> SourcePackage:
    package = SourcePackage.load(PACKAGE)
    for key, value in sector_overrides.items():
        setattr(package.sector_benchmark, key, value)
    return package


def run_fixture(tmp_path, *, mode="legacy", backend=None, session=None,
                case_id="SYK_FIXTURE_CASE", package=None):
    session = session or new_session()
    runner = CreditMemoPipeline(
        session, data_root=tmp_path / "data", output_root=tmp_path / "output",
        analysis_mode=mode, backend=backend)
    result = runner.run_case(case_id, package=package or load_package())
    return session, result


def registry(session) -> ConfigRegistry:
    return ConfigRegistry(session)


def variant_config(mutate) -> dict:
    """A deep copy of the sector configuration with ``mutate`` applied."""
    content = copy.deepcopy(sector_content())
    mutate(content)
    return content


def by_id(comparisons: list[dict]) -> dict[str, dict]:
    return {c["comparison_id"]: c for c in comparisons}


def all_keys(node) -> set[str]:
    """Every dict key anywhere inside ``node``."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            keys |= all_keys(value)
    elif isinstance(node, list):
        for value in node:
            keys |= all_keys(value)
    return keys


def variant_package(tmp_path, *, csv_edit=None, workbook=None, identity=None,
                    name="variant") -> SourcePackage:
    """A package in ``tmp_path`` built from the fixture with optional changes.

    ``csv_edit`` maps a line-item label to a replacement row (or None to drop
    the row); ``workbook`` replaces the reference workbook bytes; ``identity``
    overrides declared identity fields.
    """
    root = tmp_path / name
    (root / "stryker-fixture").mkdir(parents=True)
    (root / "reference").mkdir()
    lines = (HEALTHCARE / "stryker-fixture" / "financials.csv").read_text(
        encoding="utf-8").splitlines()
    out = []
    for line in lines:
        label = next((k for k in (csv_edit or {}) if line.startswith(k + ",")
                      or line.startswith('"' + k + '",')), None)
        if label is None:
            out.append(line)
        elif csv_edit[label] is not None:
            out.append(csv_edit[label])
    (root / "stryker-fixture" / "financials.csv").write_text(
        "\n".join(out) + "\n", encoding="utf-8", newline="\n")
    data = workbook if workbook is not None else workbook_bytes()
    (root / "reference" / WORKBOOK.name).write_bytes(data)
    manifest = json.loads(PACKAGE.read_text(encoding="utf-8"))
    manifest["sector_benchmark"].pop("reference_workbook_sha256", None)
    manifest["sector_benchmark"]["identity"].update(identity or {})
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return SourcePackage.load(root / "package.json")


def strip_run_identity(node):
    """Drop run-specific ids/timestamps so two runs can be compared substantively."""
    volatile = {"parameter_result_ids", "analysis_run_id", "comparison_row_ids",
                "classification_id", "agent_run_id", "agentic_interpretation",
                "fact_ids"}
    if isinstance(node, dict):
        return {k: strip_run_identity(v) for k, v in node.items() if k not in volatile}
    if isinstance(node, list):
        return [strip_run_identity(v) for v in node]
    return node
