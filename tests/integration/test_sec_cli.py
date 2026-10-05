import json

import pytest

from app.cli import main
from app.core.config import get_settings
from app.services.acquisition.sec_edgar import SecEdgarClient
from tests.sec_helpers import ACCESSION, inline_document
from tests.unit.test_sec_edgar import FakeTransport, SpyLimiter, response, submission


def test_mocked_fetch_then_local_run_needs_no_llm_or_api_key(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("SEC_USER_AGENT", "Offline Tests tests@example.com")
    get_settings.cache_clear()
    block = submission(
        ["10-K"], [ACCESSION], ["2025-01-10"], ["2024-12-31"], ["primary.htm"]
    )
    replies = [
        response({"0": {"ticker": "POC", "cik_str": 1, "title": "Offline Company"}}),
        response({"filings": {"recent": block, "files": []}}),
        response({"directory": {"item": [{"name": "primary.htm"}]}}),
        response(inline_document()),
    ]
    transport = FakeTransport(replies)

    def injected(user_agent, **kwargs):
        return SecEdgarClient(
            user_agent, transport=transport, limiter=SpyLimiter(), **kwargs
        )

    monkeypatch.setattr("app.cli.SecEdgarClient", injected)
    database = str(tmp_path / "case.db")
    destination = tmp_path / "filing"
    main(
        [
            "--database",
            database,
            "sec-fetch",
            "POC",
            "--accession",
            ACCESSION,
            "--destination",
            str(destination),
        ]
    )
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["status"] == "downloaded; not admitted" and len(transport.calls) == 4
    main(
        [
            "--database",
            database,
            "--output",
            str(tmp_path / "output"),
            "run-sec-case",
            "POC_2024",
            "--bundle",
            str(destination / "bundle.json"),
            "--cutoff",
            "2025-01-15T00:00:00Z",
        ]
    )
    draft = json.loads(capsys.readouterr().out)
    assert draft["status"] == "draft" and draft["admitted_sources"] == 1
    assert len(transport.calls) == 4  # Local parsing does not invoke the SEC client.
    get_settings.cache_clear()


@pytest.mark.parametrize("selection", [[], ["--fy", "2024", "--accession", ACCESSION]])
def test_live_run_requires_one_unambiguous_filing_selection(selection):
    with pytest.raises(SystemExit) as error:
        main(
            [
                "run-sec-case",
                "CASE",
                "--ticker",
                "POC",
                "--cutoff",
                "2025-01-15T00:00:00Z",
                *selection,
            ]
        )
    assert error.value.code == 2
