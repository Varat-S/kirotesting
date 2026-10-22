"""The ``edgar_filings/`` folder: one sub-folder of raw 10-K Inline-XBRL files per filing.

A folder holds what EDGAR serves for one 10-K (the ``.htm`` instance, its ``.xsd`` and the
``_lab`` / ``_pre`` / ``_cal`` linkbases, optionally exhibits and ``company.json``), the same
layout as the "10-K with FO" project. Folder names come from the browser or CLI, so they are
resolved strictly inside the root; parsed filings are cached by a signature of their files.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from app.core.hashing import content_hash
from app.services.extraction.sec.xml import MAX_FILE_BYTES

from .parsing import load_filing

FILINGS_ROOT = Path(__file__).resolve().parents[3] / "edgar_filings"
FILING_SUFFIXES = {".htm", ".html", ".xhtml", ".xsd", ".xml", ".json"}
_CACHE_SIZE = 4
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()


class FilingFolderError(ValueError):
    """The named folder is not a readable 10-K filing inside ``edgar_filings/``."""


def _root() -> Path:
    return Path(FILINGS_ROOT).resolve()


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in FILING_SUFFIXES)


def list_filings() -> list[dict]:
    """The filing folders available to run, sorted by name."""
    root = _root()
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        files = _files(folder)
        rows.append({
            "name": folder.name,
            "files": len(files),
            "has_instance": any(p.suffix.lower() in {".htm", ".html", ".xhtml"} for p in files),
            "has_schema": any(p.suffix.lower() == ".xsd" for p in files),
        })
    return rows


def resolve_folder(name: str) -> Path:
    """The folder ``name`` directly inside the filings root; anything else is refused."""
    if not name or name != name.strip() or name in {".", ".."} or any(c in name for c in "/\\:\0"):
        raise FilingFolderError(f"Invalid filing folder name {name!r}.")
    root = _root()
    folder = (root / name).resolve()
    if folder.parent != root or not folder.is_dir():
        raise FilingFolderError(f"No filing folder {name!r} in {root.name}/.")
    return folder


def folder_signature(folder: Path) -> str:
    """A hash of the folder's file names, sizes and modification times (changes when a file changes)."""
    return content_hash([(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in _files(folder)])


def load(name: str) -> tuple[dict, dict]:
    """(parsed filing, folder info) for one filing folder, from the cache when its files are unchanged."""
    folder = resolve_folder(name)
    files = _files(folder)
    too_big = [p.name for p in files if p.stat().st_size > MAX_FILE_BYTES]
    if too_big:
        raise FilingFolderError(f"File(s) over the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit: {', '.join(too_big)}.")
    signature = folder_signature(folder)
    key = (str(folder), signature)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key], {"name": folder.name, "signature": signature}
    try:
        data = load_filing(folder)
    except FileNotFoundError as exc:
        raise FilingFolderError(str(exc)) from exc
    with _cache_lock:
        _cache[key] = data
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return data, {"name": folder.name, "signature": signature}
