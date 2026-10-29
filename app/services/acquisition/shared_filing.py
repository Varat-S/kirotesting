"""One 10-K folder that both the Business Overview and the Financial Overview read.

A shared filing folder sits directly in ``edgar_filings/`` (the FO's root) and holds the filing's
files flat, as EDGAR serves them, plus two small files:

* ``bundle.json`` -- the BO's input: every file's role, SHA-256 and public availability
  (:class:`SecFilingBundle`), with paths relative to the folder;
* ``company.json`` -- identity (cik, ticker, name, SIC) that the FO reads.

A proxy statement is a separate accession, so its files go in ``proxy/`` (the FO reads only the
folder's top level). This is the layout ``examples/sec/nvda-2026`` already uses.

There are two ways in:

* :func:`fetch_filing` downloads a 10-K from SEC EDGAR; availability is SEC's acceptance time.
* :func:`import_filing` takes files the user already has (a folder, loose files or a ``.zip``).
  The cover page gives the CIK, form and period; the accession and filing date come from
  the caller or, when an SEC client is given, from EDGAR. Dates the user types are recorded
  with ``declared`` availability, never as SEC timestamps.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.services.acquisition.models import SecFiling
from app.services.acquisition.sec_edgar import EdgarError, file_role
from app.services.extraction.sec.bundle import (
    SecFilingBundle,
    SecFilingFile,
    validate_sec_filename,
)
from app.services.extraction.sec.xml import MAX_FILE_BYTES
from app.services.fo import loader as fo_loader

MANIFEST = "bundle.json"
COMPANY = "company.json"
# Optional Module 3 corporate-structure onboarding JSON kept with the filing; not SEC evidence.
M3_STRUCTURE = "m3_corporate_structure.json"
PROXY_DIR = "proxy"
# Saved beside a proxy statement: its form, accession, filing date and primary document.
PROXY_META = "filing.json"
# Files that can belong to a filing bundle; ``company.json`` is identity, not evidence.
SEC_SUFFIXES = (".htm", ".html", ".xhtml", ".xsd", ".xml")
MAX_TOTAL_BYTES = 128 * 1024 * 1024


class SharedFilingError(ValueError):
    """The filing cannot be fetched, imported or read as a shared filing folder."""


@dataclass(frozen=True)
class SharedFiling:
    name: str
    folder: Path
    cik: str
    ticker: str | None
    accession: str
    form: str
    report_date: date
    filing_date: date
    files: int
    availability: str

    @property
    def manifest(self) -> Path:
        return self.folder / MANIFEST

    def summary(self) -> dict:
        return {
            "folder": self.name,
            "path": str(self.folder),
            "cik": self.cik,
            "ticker": self.ticker,
            "accession": self.accession,
            "form": self.form,
            "report_date": self.report_date.isoformat(),
            "filing_date": self.filing_date.isoformat(),
            "files": self.files,
            "availability": self.availability,
        }


def filings_root() -> Path:
    return Path(fo_loader.FILINGS_ROOT).resolve()


def folder_name(ticker: str | None, cik: str, form: str, fiscal_year) -> str:
    """``HCA 10-K FY2025``, the naming the ``edgar_filings/`` folders already use."""
    label = re.sub(r"[^A-Za-z0-9.-]+", "", (ticker or f"CIK{int(cik)}").upper())
    return f"{label} {re.sub(r'[^A-Za-z0-9-]+', '', form)} FY{fiscal_year}"


def select_annual_filing(client, ticker: str, *, fy: int | None = None, accession: str | None = None):
    """(company, all filings, the one selected 10-K) for a ticker; never guesses between two."""
    if bool(fy) == bool(accession):
        raise SharedFilingError("Give exactly one of a fiscal year or an accession.")
    company = client.lookup_ticker(ticker)
    filings = client.list_filings(company["cik"])
    matches = [
        f for f in filings
        if (f.accession == accession if accession else f.form == "10-K" and f.fiscal_year == fy)
    ]
    if not matches:
        raise EdgarError("No filing matched; use sec-list and choose an explicit accession.")
    if len(matches) != 1:
        raise EdgarError("Multiple filings matched; select an explicit accession.")
    return company, filings, matches[0]


def annual_reports(client, ticker: str, *, include_amendments: bool = False) -> tuple[dict, list[dict]]:
    """(company, the company's 10-Ks newest first) so the user can choose which one to download.

    Each row says whether it can be used (Inline XBRL, a flat primary document), which proxy statement
    would come with it, and whether its folder is already in ``edgar_filings/``.
    """
    company = client.lookup_ticker(ticker)
    filings = client.list_filings(company["cik"])
    root = filings_root()
    rows = []
    for filing in client.annual_reports(filings, include_amendments):
        proxy = client.proxy_for(filings, filing)
        name = folder_name(company.get("ticker"), company["cik"], filing.form, filing.fiscal_year)
        problem = (None if filing.inline_xbrl and filing.primary_document and "/" not in filing.primary_document
                   else "no Inline XBRL; not supported")
        rows.append({
            "accession": filing.accession,
            "form": filing.form,
            "fiscal_year": filing.fiscal_year,
            "report_date": filing.report_date.isoformat(),
            "filing_date": filing.filing_date.isoformat(),
            "folder": name,
            "downloaded": (root / name).exists(),
            "problem": problem,
            "proxy": f"{proxy.form} filed {proxy.filing_date.isoformat()}" if proxy else None,
        })
    return company, rows


def fetch_filing(client, ticker: str, *, fy: int | None = None, accession: str | None = None,
                 with_proxy: bool = False, name: str | None = None) -> SharedFiling:
    """Download a 10-K from SEC EDGAR into a new shared folder in ``edgar_filings/``."""
    company, filings, filing = select_annual_filing(client, ticker, fy=fy, accession=accession)
    proxy = client.proxy_for(filings, filing) if with_proxy else None
    bundle = client.fetch_bundle(company, filing, proxy=proxy)
    profile = client.company_profile(company["cik"])
    identity = {
        "cik": int(company["cik"]),
        "ticker": company.get("ticker"),
        "name": profile.get("name") or company.get("name"),
        "sic": str(profile["sic"]) if profile.get("sic") else None,
        "sic_description": profile.get("sic_description"),
    }
    name = name or folder_name(bundle.ticker, bundle.cik, bundle.form, filing.fiscal_year)
    return _write_new(bundle, name, identity)


def import_filing(sources, *, accession: str | None = None, filing_date: date | None = None,
                  ticker: str | None = None, name: str | None = None, client=None) -> SharedFiling:
    """Turn files the user has into a shared folder.

    ``sources`` is a list of paths (a folder, files, or ``.zip`` archives) or a ``{filename: bytes}``
    mapping (a browser upload). A single folder that is already directly inside ``edgar_filings/``
    is prepared in place: its files stay where they are and ``bundle.json`` is added beside them.
    """
    root = filings_root()
    in_place = None
    if not isinstance(sources, dict):
        paths = [Path(p).resolve() for p in sources]
        if len(paths) == 1 and paths[0].is_dir() and paths[0].parent == root:
            in_place = paths[0]
            if (in_place / MANIFEST).exists():
                raise SharedFilingError(f"{in_place.name!r} already has a {MANIFEST}; nothing to import.")
        files = _collect(paths)
    else:
        files = _checked(dict(sources))
    identity = _identity(files.pop(COMPANY, None))
    m3_structure = files.pop(M3_STRUCTURE, None)
    files = {k: v for k, v in files.items() if k.lower().endswith(SEC_SUFFIXES)}
    if not files:
        raise SharedFilingError("No .htm/.xsd/.xml filing files were given.")

    meta = _cover_page(files, root)
    cik = str(int(meta["cik"])) if str(meta.get("cik") or "").isdigit() else None
    if cik is None:
        raise SharedFilingError("The Inline XBRL cover page has no dei:EntityCentralIndexKey (CIK).")
    form = str(meta.get("document_type") or "").strip()
    if not form.startswith("10-K"):
        raise SharedFilingError(f"The Inline XBRL document is a {form or 'document of unknown type'}, not a 10-K.")
    try:
        report_date = date.fromisoformat(str(meta.get("period_end") or "").strip())
    except ValueError as exc:
        raise SharedFilingError("The cover page has no valid dei:DocumentPeriodEndDate.") from exc
    primary = meta["source"]
    ticker = (ticker or identity.get("ticker") or meta.get("ticker") or None)
    ticker = ticker.upper() if ticker else None

    filing = _official_filing(client, cik, form, report_date, primary, accession, filing_date)
    if filing is None:
        if not (accession and filing_date):
            raise SharedFilingError(
                "The accession and filing date are needed: give --accession and --filing-date "
                "(or set SEC_USER_AGENT so they are looked up on SEC EDGAR).")
        filing, granularity, base_url = (
            SecFiling(cik, form, accession, filing_date, report_date, primary), "declared", None)
    else:
        granularity = filing.availability_granularity
        base_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{filing.accession.replace('-', '')}")
    # A folder's proxy/ is a separate accession (as fetch_filing writes it); a .zip or upload has none.
    proxy_files = [] if isinstance(sources, dict) else [
        f for p in paths if p.is_dir() and (p / PROXY_DIR).is_dir()
        for f in _proxy_files(p / PROXY_DIR, client, cik, filing)]
    try:
        bundle = SecFilingBundle(
            cik=cik, ticker=ticker, accession=filing.accession, form=form,
            report_date=report_date, filing_date=filing.filing_date, primary_document=primary,
            files=[SecFilingFile(
                filename=n, role=file_role(n, primary), content=data,
                available_at=filing.available_at, availability_granularity=granularity,
                source_url=f"{base_url}/{n}" if base_url else None,
                accession=filing.accession, cik=cik, form=form,
                filing_date=filing.filing_date, report_date=report_date,
                accepted_at=filing.accepted_at,
            ) for n, data in sorted(files.items())] + proxy_files)
    except ValueError as exc:
        raise SharedFilingError(f"The files do not form a valid filing bundle: {exc}") from exc

    identity = {"cik": int(cik), "ticker": ticker, "name": meta.get("entity_name"), **identity}
    if client is not None and not identity.get("sic"):
        try:
            profile = client.company_profile(cik)
            identity.update(sic=str(profile["sic"]) if profile.get("sic") else None,
                            sic_description=profile.get("sic_description"))
        except (EdgarError, OSError):
            pass  # SIC only refines the FO's industry mapping; the import does not depend on it
    if in_place is not None:
        _write_manifest(bundle, in_place)
        if not (in_place / COMPANY).exists():
            _write_company(in_place, identity)
        shared = _shared(in_place, bundle)
    else:
        fiscal_year = meta.get("fiscal_year") or filing.fiscal_year
        shared = _write_new(bundle, name or folder_name(ticker, cik, form, fiscal_year), identity)
    if m3_structure is not None:
        (shared.folder / M3_STRUCTURE).write_bytes(m3_structure)
    return shared


def load_bundle(name: str) -> SecFilingBundle:
    """The BO's view of a shared folder: its ``bundle.json``, with every file hash-checked."""
    folder = fo_loader.resolve_folder(name)
    if not (folder / MANIFEST).is_file():
        raise SharedFilingError(
            f"{name!r} has no {MANIFEST}, so the Business Overview cannot read it. Prepare it with "
            f"python -m app.cli import-filing \"edgar_filings/{name}\" --accession ... --filing-date ...")
    try:
        return SecFilingBundle.load(folder / MANIFEST)
    except (ValueError, OSError) as exc:
        raise SharedFilingError(f"{name!r}: {MANIFEST} cannot be read: {exc}") from exc


def m3_structure_path(name: str) -> Path | None:
    """The folder's Module 3 corporate-structure JSON: ``m3_corporate_structure.json``, or else the one JSON
    file declaring ``"document_type": "corporate_structure_onboarding"``. Two such files are an error."""
    folder = fo_loader.resolve_folder(name)
    if (folder / M3_STRUCTURE).is_file():
        return folder / M3_STRUCTURE
    found = []
    for path in sorted(folder.glob("*.json")):
        if path.name in (MANIFEST, COMPANY):
            continue
        try:
            declared = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(declared, dict) and declared.get("document_type") == "corporate_structure_onboarding":
            found.append(path)
    if len(found) > 1:
        raise SharedFilingError(
            f"{name!r} has more than one corporate-structure JSON ({', '.join(p.name for p in found)}); "
            f"keep one, or name the one to use {M3_STRUCTURE}.")
    return found[0] if found else None


def company_info(name: str) -> dict:
    try:
        info = json.loads((fo_loader.resolve_folder(name) / COMPANY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def list_shared() -> list[dict]:
    """The ``edgar_filings/`` folders, each marked with whether both sections can read it."""
    root = filings_root()
    rows = fo_loader.list_filings()
    for row in rows:
        row["has_manifest"] = (root / row["name"] / MANIFEST).is_file()
        row["runnable"] = row["has_manifest"] and row["has_instance"] and row["has_schema"]
    return rows


# --- internals ---------------------------------------------------------------------------------------


def _collect(paths: list[Path]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}

    def add(filename, data):
        if filename in files:
            raise SharedFilingError(f"{filename!r} was given twice.")
        files[filename] = data

    for path in paths:
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.is_file() and (child.suffix.lower() in SEC_SUFFIXES or child.name in (COMPANY, M3_STRUCTURE)):
                    add(child.name, _read(child))
        elif path.is_file() and path.suffix.lower() == ".zip":
            for filename, data in _unzip(path.read_bytes()).items():
                add(filename, data)
        elif path.is_file():
            add(path.name, _read(path))
        else:
            raise SharedFilingError(f"Not a file or folder: {path}")
    return _checked(files)


def _read(path: Path) -> bytes:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise SharedFilingError(f"{path.name} is over the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit.")
    return path.read_bytes()


def _unzip(data: bytes) -> dict[str, bytes]:
    """Top-level-agnostic: members are taken by their base name; folders inside the archive are flattened."""
    files: dict[str, bytes] = {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SharedFilingError("The .zip archive cannot be read.") from exc
    with archive:
        for info in archive.infolist():
            filename = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if info.is_dir() or not filename or filename.startswith(".") or "__MACOSX" in info.filename:
                continue
            if not (filename.lower().endswith(SEC_SUFFIXES) or filename in (COMPANY, M3_STRUCTURE)):
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise SharedFilingError(f"{filename} is over the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit.")
            if filename in files:
                raise SharedFilingError(f"The archive holds two files named {filename!r}.")
            with archive.open(info) as member:
                files[filename] = member.read(MAX_FILE_BYTES + 1)
    return files


def _checked(files: dict[str, bytes]) -> dict[str, bytes]:
    expanded: dict[str, bytes] = {}
    for filename, data in files.items():
        if filename.lower().endswith(".zip"):
            for inner, inner_data in _unzip(data).items():
                if inner in expanded or inner in files:
                    raise SharedFilingError(f"{inner!r} was given twice.")
                expanded[inner] = inner_data
        else:
            expanded[filename] = data
    for filename, data in expanded.items():
        try:
            validate_sec_filename(filename)
        except ValueError as exc:
            raise SharedFilingError(
                f"{filename!r} is not an acceptable SEC file name (letters, digits, '_', '.', '-').") from exc
        if len(data) > MAX_FILE_BYTES:
            raise SharedFilingError(f"{filename} is over the {MAX_FILE_BYTES // (1024 * 1024)} MiB limit.")
    if sum(len(d) for d in expanded.values()) > MAX_TOTAL_BYTES:
        raise SharedFilingError("The filing is over the 128 MiB limit.")
    return expanded


def _identity(data: bytes | None) -> dict:
    if not data:
        return {}
    try:
        info = json.loads(data)
    except ValueError as exc:
        raise SharedFilingError(f"{COMPANY} is not valid JSON.") from exc
    return {k: v for k, v in info.items() if v not in (None, "")} if isinstance(info, dict) else {}


def _cover_page(files: dict[str, bytes], root: Path) -> dict:
    """The FO parser's reading of the cover page (CIK, form, period, ticker), from a scratch copy."""
    from app.services.fo.parsing import load_filing

    root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".intake-", dir=root))  # dot folders are not listed as filings
    try:
        for filename, data in files.items():
            (scratch / filename).write_bytes(data)
        try:
            return load_filing(scratch)["meta"]
        except FileNotFoundError as exc:
            raise SharedFilingError(str(exc)) from exc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _official_filing(client, cik, form, report_date, primary, accession, filing_date):
    """The filing as SEC EDGAR lists it, when a client is given; checks any accession/date the user gave."""
    if client is None:
        return None
    matches = [
        f for f in client.list_filings(cik)
        if (f.accession == accession if accession else f.form == form and f.report_date == report_date)
    ]
    if len(matches) != 1:
        raise SharedFilingError(
            f"SEC EDGAR lists {len(matches)} {form} filing(s) for CIK {cik} "
            f"{'with accession ' + accession if accession else 'for period ' + report_date.isoformat()}; "
            "give --accession explicitly.")
    filing = matches[0]
    if filing_date and filing.filing_date != filing_date:
        raise SharedFilingError(
            f"SEC EDGAR dates accession {filing.accession} {filing.filing_date}, not {filing_date}.")
    if filing.primary_document and filing.primary_document != primary:
        raise SharedFilingError(
            f"SEC EDGAR's primary document for {filing.accession} is {filing.primary_document}, "
            f"but the files given have {primary}.")
    return filing


def _proxy_files(folder: Path, client, cik: str, filing) -> list[SecFilingFile]:
    """The DEF 14A in a folder's ``proxy/``, with its own accession and availability.

    SEC EDGAR identifies it when a client is given (by the ``filing.json`` accession beside it, else
    the proxy that would be fetched with this 10-K); offline, ``filing.json`` declares it. A proxy
    that cannot be identified is an error rather than silently left out of the bundle.
    """
    data = {p.name: _read(p) for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in SEC_SUFFIXES}
    if not data:
        return []
    _checked(data)
    meta = {}
    if (folder / PROXY_META).is_file():
        try:
            meta = json.loads((folder / PROXY_META).read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SharedFilingError(f"{PROXY_DIR}/{PROXY_META} is not valid JSON.") from exc
    if client is not None:
        filings = client.list_filings(cik)
        proxy = (next((f for f in filings if f.accession == meta["accession"]), None)
                 if meta.get("accession") else client.proxy_for(filings, filing))
        granularity = proxy.availability_granularity if proxy else None
        base_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{proxy.accession.replace('-', '')}") if proxy else None
    else:
        try:
            proxy = SecFiling(cik, str(meta["form"]), str(meta["accession"]),
                              date.fromisoformat(str(meta["filing_date"])), None, str(meta["primary_document"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SharedFilingError(
                f"{PROXY_DIR}/ needs a {PROXY_META} with form, accession, filing_date and primary_document "
                "(or set SEC_USER_AGENT so SEC EDGAR identifies the proxy statement).") from exc
        granularity, base_url = "declared", None
    if proxy is None or proxy.form != "DEF 14A":
        raise SharedFilingError(f"SEC EDGAR lists no DEF 14A proxy statement matching {PROXY_DIR}/.")
    if proxy.primary_document not in data:
        raise SharedFilingError(f"{PROXY_DIR}/ does not contain the proxy statement {proxy.primary_document}.")
    return [SecFilingFile(
        filename=n, role=file_role(n, proxy.primary_document, proxy=True), content=content,
        available_at=proxy.available_at, availability_granularity=granularity,
        source_url=f"{base_url}/{n}" if base_url else None,
        accession=proxy.accession, cik=cik, form=proxy.form,
        filing_date=proxy.filing_date, report_date=proxy.report_date, accepted_at=proxy.accepted_at,
    ) for n, content in sorted(data.items())]


def _write_new(bundle: SecFilingBundle, name: str, identity: dict) -> SharedFiling:
    root = filings_root()
    if not name or name != name.strip() or any(c in name for c in "/\\:\0") or name in {".", ".."}:
        raise SharedFilingError(f"Invalid folder name {name!r}.")
    target = root / name
    if target.exists():
        raise SharedFilingError(
            f"edgar_filings/{name} already exists. Use it as it is, remove it, or choose another --name.")
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".partial-", dir=root))
    try:
        for file in bundle.files:
            path = staging / _relative(bundle, file)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file.content)
        _write_manifest(bundle, staging)
        _write_company(staging, identity)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return _shared(target, bundle)


def _relative(bundle, file) -> str:
    return file.filename if file.accession == bundle.accession else f"{PROXY_DIR}/{file.filename}"


def _write_manifest(bundle: SecFilingBundle, folder: Path) -> None:
    metadata = bundle.model_dump(mode="json", exclude={"files"})
    metadata["files"] = []
    for file in bundle.files:
        entry = file.metadata()
        entry.pop("document_id", None)
        entry["path"] = _relative(bundle, file)
        metadata["files"].append(entry)
    (folder / MANIFEST).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def _write_company(folder: Path, identity: dict) -> None:
    info = {k: identity.get(k) for k in ("cik", "ticker", "name", "sic", "sic_description")
            if identity.get(k) not in (None, "")}
    (folder / COMPANY).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def _shared(folder: Path, bundle: SecFilingBundle) -> SharedFiling:
    primary = next(f for f in bundle.files if f.role == "primary_inline_xbrl")
    return SharedFiling(
        name=folder.name, folder=folder, cik=bundle.cik, ticker=bundle.ticker,
        accession=bundle.accession, form=bundle.form, report_date=bundle.report_date,
        filing_date=bundle.filing_date, files=len(bundle.files),
        availability=primary.availability_granularity)
