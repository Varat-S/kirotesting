"""Industry group (ticker / company-name mapping) and per-industry benchmarks.

Ported from the "10-K with FO" project (``industry_mappings``, ``classify_industry``,
``load_benchmarks``). The files live in ``config/financial_overview/``, outside
``config/poc``, so the versioned PoC config bootstrap never reads them; each run
records their content hash instead.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from pathlib import Path

from app.core.hashing import content_hash

MAPPINGS_DIR = Path(__file__).resolve().parents[3] / "config" / "financial_overview"
BENCHMARK_FILE_NAME = "Benchmarks.json"  # every benchmark comes from this file
NAME_NOISE = re.compile(r"\b(the|incorporated|inc|corporation|corp|company|co|plc|ltd|limited|llc|lp|sa|nv|ag|se)\b")

_mappings = {"sig": None, "by_ticker": {}, "by_name": {}}
_mappings_lock = threading.Lock()
_benchmarks = {"sig": None, "by_sector": {}, "files": []}
_benchmarks_lock = threading.Lock()


def _json_files():
    return sorted(MAPPINGS_DIR.glob("*.json")) if MAPPINGS_DIR.is_dir() else []


def name_key(name):
    """A company name reduced for matching: 'ELI LILLY & Co' and 'Eli Lilly and Company' both give 'eli lilly'."""
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower().replace("&", " and "))
    return " ".join(NAME_NOISE.sub(" ", s).replace(" and ", " ").split())


def industry_mappings():
    """Ticker -> industry group from every mapping file ({ticker: {company_name, industry_group}}), re-read
    when a file is added or changed. Returns (by_ticker, by_name)."""
    files = _json_files()
    sig = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    with _mappings_lock:
        if sig != _mappings["sig"]:
            by_ticker, by_name = {}, defaultdict(list)
            for p in files:
                try:
                    rows = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for ticker, row in rows.items() if isinstance(rows, dict) else ():
                    if not isinstance(row, dict) or not row.get("industry_group"):
                        continue
                    entry = {"ticker": ticker.upper(), "company_name": row.get("company_name"),
                             "industry_group": row["industry_group"], "mapping_file": p.name}
                    by_ticker.setdefault(entry["ticker"], entry)
                    by_name[name_key(entry["company_name"])].append(entry)
            _mappings.update(sig=sig, by_ticker=by_ticker, by_name=dict(by_name))
        return _mappings["by_ticker"], _mappings["by_name"]


def classify_industry(data):
    """The company's industry group from the mapping files: by ticker (any class of stock), else by an
    unambiguous company-name match. Includes how it was matched; industry_group is None if not found."""
    meta, company = data["meta"], data.get("company") or {}
    by_ticker, by_name = industry_mappings()
    tickers = list(dict.fromkeys(t.upper() for t in [meta.get("ticker"), *meta.get("tickers", []), company.get("ticker")] if t))
    hit = next((by_ticker[t] for t in tickers if t in by_ticker), None)
    matched_by = "ticker"
    if not hit:
        names = [name_key(n) for n in (meta.get("entity_name"), company.get("name")) if n]
        found = {e["ticker"]: e for n in names for e in by_name.get(n, [])}
        hit = next(iter(found.values())) if len(found) == 1 else None
        matched_by = "company name"
    result = {"industry_group": None, "sic": company.get("sic"), "sic_description": company.get("sic_description")}
    if hit:
        result.update(hit, matched_by=matched_by)
    elif not by_ticker:
        result["note"] = "No industry mapping files found in config/financial_overview."
    else:
        result["note"] = (f"{', '.join(tickers) or 'This company'} is not in the industry mappings "
                          f"({', '.join(sorted({e['mapping_file'] for e in by_ticker.values()}))}).")
    return result


def load_benchmarks():
    """{industry group: {measure: benchmark, "p"?: score}} from Benchmarks.json
    ({"benchmarks": [{"sector": ..., measure: value, ...}]}), re-read when it changes."""
    files = [p for p in [MAPPINGS_DIR / BENCHMARK_FILE_NAME] if p.is_file()]
    sig = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    with _benchmarks_lock:
        if sig != _benchmarks["sig"]:
            by_sector = {}
            for p in files:
                try:
                    rows = json.loads(p.read_text(encoding="utf-8")).get("benchmarks", [])
                except (OSError, ValueError, AttributeError):
                    continue
                for row in rows if isinstance(rows, list) else ():
                    if isinstance(row, dict) and row.get("sector"):
                        by_sector.setdefault(row["sector"], {}).update(
                            {k: float(v) for k, v in row.items() if k != "sector" and isinstance(v, (int, float))})
            _benchmarks.update(sig=sig, by_sector=by_sector, files=[p.name for p in files])
        return _benchmarks["by_sector"], _benchmarks["files"]


def reference_hash():
    """Content hash of every reference file, recorded on each run so a result traces to its mappings/benchmarks."""
    contents = {}
    for p in _json_files():
        try:
            contents[p.name] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            contents[p.name] = None
    return content_hash(contents)
