"""Public SEC acquisition adapted from xbrl_dashboard.py, with injectable I/O."""

import gzip
import io
import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.services.extraction.sec.bundle import (
    SecFilingBundle,
    SecFilingFile,
    validate_sec_filename,
)
from app.services.extraction.sec.xml import MAX_FILE_BYTES
from .models import SecFiling


class EdgarError(RuntimeError):
    pass


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]


class HttpTransport(Protocol):
    def get(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> HttpResponse: ...


def validate_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise EdgarError("Only SEC HTTPS endpoints are allowed.")


class SecRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    def get(self, url, headers, timeout):
        opener = urllib.request.build_opener(SecRedirectHandler())
        try:
            response = opener.open(
                urllib.request.Request(url, headers=headers), timeout=timeout
            )
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, OSError) as exc:
            raise EdgarError(f"Unable to retrieve SEC response: {exc}") from exc
        with response:
            body = response.read(MAX_FILE_BYTES + 1)
            if len(body) > MAX_FILE_BYTES:
                raise EdgarError("SEC response exceeds size limit.")
            return HttpResponse(response.code, body, dict(response.headers))


class RateLimiter:
    def __init__(
        self, requests_per_second=8, *, clock=time.monotonic, sleep=time.sleep
    ):
        if not 0 < requests_per_second <= 8:
            raise ValueError(
                "SEC request rate must be greater than zero and at most 8/sec."
            )
        self.interval, self.clock, self.sleep = 1 / requests_per_second, clock, sleep
        self.next_request, self.lock = 0.0, threading.Lock()

    def acquire(self):
        with self.lock:
            delay = max(0, self.next_request - self.clock())
            if delay:
                self.sleep(delay)
            self.next_request = max(self.next_request, self.clock()) + self.interval


def valid_user_agent(value):
    return bool(
        value
        and not re.search(r"[\r\n]", value)
        and re.search(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}", value)
    )


def parse_accepted(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed.replace(tzinfo=ZoneInfo("America/New_York"))
        if parsed.tzinfo is None
        else parsed
    )


class SecEdgarClient:
    def __init__(
        self,
        user_agent,
        *,
        transport=None,
        limiter=None,
        timeout=30,
        max_retries=2,
        sleep=time.sleep,
        now=None,
        audit=None,
    ):
        if not valid_user_agent(user_agent):
            raise ValueError(
                "SEC_USER_AGENT must include a plausible contact email, e.g. 'Your Name you@example.com'. No SEC API key is needed."
            )
        if timeout <= 0 or max_retries < 0:
            raise ValueError("Timeout must be positive and retries nonnegative.")
        self.user_agent, self.transport = user_agent, transport or UrllibTransport()
        self.limiter, self.sleep = limiter or RateLimiter(), sleep
        self.timeout, self.max_retries = timeout, max_retries
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.audit, self.fetch_metadata = audit, []

    def _event(self, event, payload):
        if self.audit:
            self.audit.record(event, after=payload)

    def get(self, url, *, as_json=True):
        validate_url(url)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            response = self.transport.get(url, headers, self.timeout)
            metadata = {
                "url": url,
                "status": response.status,
                "retrieved_at": self.now().isoformat(),
                "attempt": attempt + 1,
            }
            self.fetch_metadata.append(metadata)
            if response.status == 429 and attempt < self.max_retries:
                retry = next(
                    (
                        v
                        for k, v in response.headers.items()
                        if k.lower() == "retry-after"
                    ),
                    None,
                )
                try:
                    delay = max(0, float(retry)) if retry is not None else 1 + attempt
                except ValueError:
                    try:
                        delay = max(
                            0,
                            (parsedate_to_datetime(retry) - self.now()).total_seconds(),
                        )
                    except (ValueError, TypeError, OverflowError):
                        delay = 1 + attempt
                if not 0 <= delay < float("inf"):
                    delay = 1 + attempt
                self.sleep(delay)
                continue
            if response.status in {403, 429}:
                raise EdgarError(
                    f"SEC refused the request (HTTP {response.status}); check contact identification or wait before retrying."
                )
            if response.status != 200:
                raise EdgarError(f"SEC request failed (HTTP {response.status}): {url}")
            body = response.body
            if any(
                k.lower() == "content-encoding" and v.lower() == "gzip"
                for k, v in response.headers.items()
            ):
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                        body = stream.read(MAX_FILE_BYTES + 1)
                except (OSError, EOFError) as exc:
                    raise EdgarError("SEC returned invalid gzip content.") from exc
            if len(body) > MAX_FILE_BYTES:
                raise EdgarError("SEC response exceeds size limit.")
            try:
                return json.loads(body) if as_json else body
            except (ValueError, UnicodeDecodeError) as exc:
                raise EdgarError("SEC returned invalid JSON.") from exc
        raise EdgarError("SEC retry limit exceeded.")

    def lookup_ticker(self, ticker):
        wanted = ticker.strip().upper().replace(".", "-")
        for row in self.get("https://www.sec.gov/files/company_tickers.json").values():
            if row["ticker"].upper() == wanted:
                return {
                    "cik": str(row["cik_str"]),
                    "ticker": row["ticker"],
                    "name": row["title"],
                }
        raise EdgarError(f"Ticker {wanted!r} was not found in the SEC ticker list.")

    def list_filings(self, cik):
        if not re.fullmatch(r"\d{1,10}", str(cik)):
            raise ValueError("Invalid CIK.")
        sub = self.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        blocks = [sub["filings"]["recent"]]
        for page in sub["filings"].get("files", []):
            name = validate_sec_filename(page["name"])
            blocks.append(self.get(f"https://data.sec.gov/submissions/{name}"))
        filings = {}
        for block in blocks:
            for i, form in enumerate(block["form"]):

                def field(name, default=None):
                    return (
                        block[name][i]
                        if name in block and i < len(block[name])
                        else default
                    )

                accession = field("accessionNumber")
                if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession or ""):
                    raise EdgarError("Invalid accession in SEC submissions.")
                filing = SecFiling(
                    str(cik),
                    form,
                    accession,
                    date.fromisoformat(field("filingDate")),
                    date.fromisoformat(field("reportDate"))
                    if field("reportDate")
                    else None,
                    validate_sec_filename(field("primaryDocument")),
                    parse_accepted(field("acceptanceDateTime")),
                    bool(field("isInlineXBRL", 0)),
                )
                filings[filing.accession] = filing
                self._event("sec_filing_discovered", filing.metadata())
        return sorted(
            filings.values(), key=lambda f: (f.filing_date, f.accession), reverse=True
        )

    @staticmethod
    def annual_reports(filings, include_amendments=False):
        return [
            f
            for f in filings
            if f.form in ({"10-K", "10-K/A"} if include_amendments else {"10-K"})
            and f.report_date
        ]

    @staticmethod
    def proxy_for(filings, report):
        proxies = [f for f in filings if f.form == "DEF 14A" and f.primary_document]
        after = [
            f
            for f in proxies
            if report.filing_date
            <= f.filing_date
            <= report.filing_date + timedelta(days=180)
        ]
        before = [
            f
            for f in proxies
            if report.filing_date - timedelta(days=45)
            <= f.filing_date
            < report.filing_date
        ]
        return (
            min(after, key=lambda f: f.filing_date)
            if after
            else max(before, key=lambda f: f.filing_date)
            if before
            else None
        )

    def download_files(self, filing, *, proxy=False):
        base = f"https://www.sec.gov/Archives/edgar/data/{int(filing.cik)}/{filing.accession.replace('-', '')}"
        listing = self.get(base + "/index.json")["directory"]["item"]
        names = select_filing_files(
            [row["name"] for row in listing], filing.primary_document
        )
        if filing.primary_document not in names:
            raise EdgarError("SEC directory omitted the primary document.")
        files = []
        for name in names:
            role = file_role(name, filing.primary_document, proxy)
            url = base + "/" + name
            content = self.get(url, as_json=False)
            file = SecFilingFile(
                filename=name,
                role=role,
                content=content,
                available_at=filing.available_at,
                availability_granularity=filing.availability_granularity,
                source_url=url,
                accession=filing.accession,
                cik=filing.cik,
                form=filing.form,
                filing_date=filing.filing_date,
                report_date=filing.report_date,
                accepted_at=filing.accepted_at,
                retrieved_at=self.now(),
            )
            files.append(file)
            self._event("sec_file_downloaded", file.metadata())
        return files

    def fetch_bundle(self, company, filing, *, proxy=None):
        files = self.download_files(filing)
        if proxy:
            # Proxy is a separate accession with its own official availability.
            files.extend(self.download_files(proxy, proxy=True))
        return SecFilingBundle(
            cik=str(company["cik"]),
            ticker=company.get("ticker"),
            accession=filing.accession,
            form=filing.form,
            report_date=filing.report_date,
            filing_date=filing.filing_date,
            primary_document=filing.primary_document,
            files=files,
        )

    def download_filing(self, company, filing, target, *, proxy=None):
        return write_bundle(self.fetch_bundle(company, filing, proxy=proxy), target)


def file_role(name, primary, proxy=False):
    if name == primary:
        return "proxy" if proxy else "primary_inline_xbrl"
    for suffix, role in [
        (".xsd", "schema"),
        ("_lab.xml", "label_linkbase"),
        ("_pre.xml", "presentation_linkbase"),
        ("_cal.xml", "calculation_linkbase"),
        ("_def.xml", "definition_linkbase"),
    ]:
        if name.lower().endswith(suffix):
            return role
    return (
        "exhibit_21"
        if re.search(r"subsidiar|(?:ex[-_]?21)(?:[^0-9]|$)", name, re.I)
        else "other_exhibit"
    )


def select_filing_files(names, primary):
    selected = []
    for name in names:
        # Index listings also contain folder entries. Only accepted filenames
        # can become local paths or archive URLs.
        if "/" in name or "\\" in name:
            raise EdgarError("Unsafe path in SEC directory listing.")
        low = name.lower()
        if (
            name == primary
            or low.endswith(".xsd")
            or re.search(r"_(?:lab|pre|cal|def)\.xml$", low)
            or (
                low.endswith((".htm", ".html", ".xhtml"))
                and not re.fullmatch(r"r\d+\.htm", low)
                and "-index" not in low
            )
        ):
            validate_sec_filename(name)
            selected.append(name)
    return sorted(set(selected))


def write_bundle(bundle, target: Path):
    """Publish a complete local acquisition atomically, without overwriting one."""
    target = target.resolve()
    if target.exists():
        raise FileExistsError(f"Bundle destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="sec-partial-", dir=target.parent
    ) as staging:
        folder = Path(staging) / "bundle"
        folder.mkdir()
        metadata = bundle.model_dump(mode="json", exclude={"files"})
        metadata["files"] = []
        for file in bundle.files:
            relative = Path(file.accession) / file.filename
            (folder / relative).parent.mkdir(parents=True, exist_ok=True)
            (folder / relative).write_bytes(file.content)
            entry = file.metadata()
            entry.pop("document_id")
            entry["path"] = relative.as_posix()
            metadata["files"].append(entry)
        (folder / "bundle.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        folder.rename(target)
    return target / "bundle.json"
