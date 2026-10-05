import gzip
import json
from datetime import date, datetime, timezone
from email.utils import format_datetime

import pytest

from app.services.acquisition.models import SecFiling
from app.services.acquisition.sec_edgar import (
    EdgarError,
    HttpResponse,
    RateLimiter,
    SecEdgarClient,
    select_filing_files,
    write_bundle,
    validate_sec_document_path,
)
from app.services.extraction.sec.bundle import SecFilingBundle, SecFilingFile
from tests.sec_helpers import ACCESSION, bundle, filing_file

NOW = datetime(2025, 1, 15, tzinfo=timezone.utc)
URL = "https://www.sec.gov/files/company_tickers.json"


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class SpyLimiter:
    def __init__(self):
        self.count = 0

    def acquire(self):
        self.count += 1


def response(data, status=200, headers=None):
    return HttpResponse(
        status,
        data if isinstance(data, bytes) else json.dumps(data).encode(),
        headers or {},
    )


def client(responses, **kwargs):
    transport, limiter = FakeTransport(responses), SpyLimiter()
    return SecEdgarClient(
        "Offline Tests tests@example.com",
        transport=transport,
        limiter=limiter,
        now=lambda: NOW,
        **kwargs,
    )


@pytest.mark.parametrize(
    "ua", [None, "", "name", "name no-email", "name a@b.com\r\nInjected: yes"]
)
def test_user_agent_required_without_api_key(ua):
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        SecEdgarClient(ua)


def test_lookup_gzip_headers_timeout_and_fetch_metadata():
    body = gzip.compress(
        json.dumps(
            {"0": {"ticker": "NVDA", "cik_str": 1045810, "title": "NVIDIA"}}
        ).encode()
    )
    c = client([response(body, headers={"Content-Encoding": "gzip"})])
    assert c.lookup_ticker("nvda")["cik"] == "1045810"
    url, headers, timeout = c.transport.calls[0]
    assert url == URL and "tests@example.com" in headers["User-Agent"]
    assert headers["Accept-Encoding"] == "gzip" and timeout == 30
    assert "Authorization" not in headers
    assert c.fetch_metadata[0] == {
        "url": URL,
        "status": 200,
        "retrieved_at": NOW.isoformat(),
        "attempt": 1,
    }


@pytest.mark.parametrize(
    "retry,expected",
    [
        ("2", 2),
        (
            format_datetime(
                datetime(2025, 1, 15, 0, 0, 5, tzinfo=timezone.utc), usegmt=True
            ),
            5,
        ),
        ("nonsense", 1),
    ],
)
def test_retry_after_is_honored_and_rate_limiter_applies_to_retries(retry, expected):
    waits = []
    c = client(
        [response(b"", 429, {"Retry-After": retry}), response({"ok": True})],
        sleep=waits.append,
    )
    assert c.get(URL) == {"ok": True}
    assert waits == [expected] and c.limiter.count == 2


@pytest.mark.parametrize("status", [403, 429, 500])
def test_http_failures_are_bounded_and_clear(status):
    c = client([response(b"", status)] * 3, sleep=lambda _: None)
    with pytest.raises(EdgarError, match=f"HTTP {status}"):
        c.get(URL)
    assert len(c.transport.calls) == (3 if status == 429 else 1)


def test_rate_limiter_never_exceeds_eight_per_second():
    clock = [0.0]
    starts = []

    def sleep(delay):
        clock[0] += delay

    limiter = RateLimiter(clock=lambda: clock[0], sleep=sleep)
    for _ in range(20):
        limiter.acquire()
        starts.append(clock[0])
    assert all(b - a >= 0.125 for a, b in zip(starts, starts[1:]))
    with pytest.raises(ValueError):
        RateLimiter(9)


def submission(forms, accessions, dates, reports, primaries, acceptance=None):
    return {
        "form": forms,
        "accessionNumber": accessions,
        "filingDate": dates,
        "reportDate": reports,
        "primaryDocument": primaries,
        "acceptanceDateTime": acceptance or [None] * len(forms),
    }


def test_historical_pages_amendments_and_proxy_have_independent_dates():
    recent = submission(
        ["10-K", "10-K/A", "DEF 14A"],
        [ACCESSION, "0000000001-25-000002", "0000000001-25-000003"],
        ["2025-01-10", "2025-02-10", "2025-05-01"],
        ["2024-12-31"] * 3,
        ["primary.htm", "amendment.htm", "proxy.htm"],
        ["2025-01-10T18:00:00Z", None, None],
    )
    history = submission(
        ["10-K"], ["0000000001-24-000001"], ["2024-01-10"], ["2023-12-31"], ["old.htm"]
    )
    c = client(
        [
            response(
                {
                    "filings": {
                        "recent": recent,
                        "files": [{"name": "CIK0000000001-submissions-001.json"}],
                    }
                }
            ),
            response(history),
        ]
    )
    filings = c.list_filings("1")
    assert len(filings) == 4
    annual = c.annual_reports(filings)
    assert len(annual) == 2 and len(c.annual_reports(filings, True)) == 3
    report = next(f for f in annual if f.accession == ACCESSION)
    assert report.available_at == datetime(2025, 1, 10, 18, tzinfo=timezone.utc)
    proxy = c.proxy_for(filings, report)
    assert proxy.form == "DEF 14A" and proxy.available_at > report.available_at
    assert "submissions-001.json" in c.transport.calls[1][0]


def test_date_only_availability_conservative_across_dst():
    winter = SecFiling(
        "1", "10-K", ACCESSION, date(2025, 1, 10), date(2024, 12, 31), "primary.htm"
    )
    summer = SecFiling("1", "DEF 14A", ACCESSION, date(2025, 5, 1), None, "proxy.htm")
    assert winter.available_at.isoformat() == "2025-01-11T05:00:00+00:00"
    assert summer.available_at.isoformat() == "2025-05-02T04:00:00+00:00"
    assert winter.availability_granularity == "date"


def test_nested_unrelated_primary_does_not_block_annual_discovery():
    recent = submission(
        ["10-K", "N-PX", "UPLOAD"],
        [ACCESSION, "0000000001-25-000002", "0000000001-25-000003"],
        ["2025-01-10", "2025-02-10", "2025-02-11"],
        ["2024-12-31", "", ""],
        ["primary.htm", "xslN-PX_X01/primary_doc.xml", ""],
    )
    c = client([response({"filings": {"recent": recent, "files": []}})])
    filings = c.list_filings("1")
    assert len(filings) == 3
    assert c.annual_reports(filings)[0].primary_document == "primary.htm"
    nested = next(f for f in filings if f.form == "N-PX")
    assert nested.primary_document == "xslN-PX_X01/primary_doc.xml"
    before = len(c.transport.calls)
    with pytest.raises(EdgarError, match="nested SEC primary document path is unsupported"):
        c.download_files(nested)
    assert len(c.transport.calls) == before
    missing = next(f for f in filings if f.form == "UPLOAD")
    with pytest.raises(EdgarError, match="no primary document"):
        c.download_files(missing)
    assert len(c.transport.calls) == before
    with pytest.raises(ValueError):
        filing_file(filename=nested.primary_document)


@pytest.mark.parametrize("path", [
    "../primary.xml", "/primary.xml", "dir/../primary.xml",
    "dir/./primary.xml", "dir//primary.xml", "dir/",
    "dir\\primary.xml", "%2e%2e/primary.xml", "dir/%2fprimary.xml",
    "%252e%252e/primary.xml", "https://www.sec.gov/primary.xml",
    "dir/primary.xml?query=1", "dir/primary.xml#fragment", "", None,
])
def test_sec_document_path_rejects_unsafe_components(path):
    with pytest.raises(EdgarError, match="Invalid SEC primary document path"):
        validate_sec_document_path(path)


def test_bundle_cannot_claim_early_or_naive_availability():
    with pytest.raises(ValueError, match="exactly one primary"):
        bundle(
            [
                filing_file(),
                filing_file(
                    "amendment.htm", accession="0000000001-25-000002", form="10-K/A"
                ),
            ]
        )
    original = filing_file().model_dump(exclude={"content"}) | {"content": b"test"}
    for invalid in [datetime(2025, 1, 10, tzinfo=timezone.utc), datetime(2025, 1, 12)]:
        with pytest.raises(ValueError):
            SecFilingFile.model_validate(original | {"available_at": invalid})


@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/test",
        "https://example.com/test",
        "https://data.sec.gov.evil.com/test",
        "https://user:pass@www.sec.gov/test",
    ],
)
def test_transport_cannot_request_non_sec_endpoints(url):
    c = client([])
    with pytest.raises(EdgarError):
        c.get(url)
    assert c.transport.calls == []


@pytest.mark.parametrize(
    "name",
    ["../bad.htm", "folder/bad.htm", "bad:stream.htm", "NUL.htm", "primary.htm."],
)
def test_unsafe_filenames_rejected(name):
    with pytest.raises(ValueError):
        SecFilingFile.model_validate(
            filing_file().model_dump(exclude={"content"})
            | {"filename": name, "content": b"test"}
        )


def test_selected_files_and_atomic_download_failure(tmp_path):
    assert select_filing_files(
        [
            "primary.htm",
            "report.xsd",
            "report_lab.xml",
            "report_cal.xml",
            "report_def.xml",
            "report_pre.xml",
            "R1.htm",
            "filing-index.htm",
            "image.png",
            "ex21.htm",
        ],
        "primary.htm",
    ) == [
        "ex21.htm",
        "primary.htm",
        "report.xsd",
        "report_cal.xml",
        "report_def.xml",
        "report_lab.xml",
        "report_pre.xml",
    ]
    listing = {"directory": {"item": [{"name": "primary.htm"}, {"name": "schema.xsd"}]}}
    c = client(
        [response(listing), response(b"primary"), EdgarError("download interrupted")]
    )
    report = SecFiling(
        "1", "10-K", ACCESSION, date(2025, 1, 10), date(2024, 12, 31), "primary.htm"
    )
    target = tmp_path / "unfinished"
    with pytest.raises(EdgarError, match="interrupted"):
        c.download_filing({"cik": "1", "ticker": "POC"}, report, target)
    assert not target.exists()


def test_bundle_round_trip_hash_validation_and_admission_not_assertable(tmp_path):
    manifest = write_bundle(bundle(), tmp_path / "complete")
    assert SecFilingBundle.load(manifest).files[0].sha256 == bundle().files[0].sha256
    with pytest.raises(FileExistsError):
        write_bundle(bundle(), manifest.parent)
    original = json.loads(manifest.read_text())
    for edits, message in [
        ({"path": "../outside.htm"}, "inside"),
        ({"sha256": "0" * 64}, "hash"),
        ({"admitted": True}, "admission"),
        ({"document_id": "spoofed"}, "admission"),
    ]:
        changed = json.loads(json.dumps(original))
        changed["files"][0].update(edits)
        manifest.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match=message):
            SecFilingBundle.load(manifest)


@pytest.mark.parametrize(
    "body,headers,message",
    [(b"not-json", {}, "JSON"), (b"not-gzip", {"Content-Encoding": "gzip"}, "gzip")],
)
def test_malformed_responses_fail_cleanly(body, headers, message):
    c = client([response(body, headers=headers)])
    with pytest.raises(EdgarError, match=message):
        c.get(URL)
