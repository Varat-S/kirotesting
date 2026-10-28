"""M3-v2 management from a linked DEF 14A when the 10-K incorporates Item 10 by reference.

The passages mirror a real proxy layout (fictional names): a pipe-delimited officer table
with a wrapped row, ``Name—Title`` biography headings, a CEO-director biography in the
director section far from the officer table, and a biography split at a passage boundary.
"""
import pytest

from app.core.hashing import content_hash
from app.services.bo.m3_rebuild.claims import assess_claims
from app.services.bo.m3_rebuild.contracts import validate_management
from app.services.bo.m3_rebuild.sources import proxy_bio_starts, sources_from_evidence_packet
from app.services.bo.reporting import _m3_v2_view
from app.services.bo.runner import _linked_proxy

from .review_fixtures import FixtureReviewer

DIRECTORS = (
    "Director Nominees\nAlex Sample\nMr. Sample became our Chief Executive Officer on March 1, 2019. "
    "He previously led our operations.\nJordan Board\nMs. Board is an independent director.\n"
)
OFFICERS = (
    "Other proxy content.\nEXECUTIVE OFFICERS\n"
    "The following table lists all of our executive officers.\n"
    "Name | | Age | | Position | | Since\n"
    "Alex Sample | | 58 | | Chief Executive Officer; Director | | 3/1/2019\n"
    "Jamie Example | | 51\nExecutive Vice President, General Counsel and Secretary\n2/18/2016\n"
    "Riley Test | | 47 | | Chief Financial Officer | | 1/5/2024\n"
    "There are no family relationships or other arrangements between any of the executive officers "
    "listed above and any other person.\n"
    "Executive Officers Who Are Not Also Directors\n"
    "Jamie Example—Executive Vice President, General Counsel and Secretary\nMs. Exam"
)
CONTINUATION = (
    "ple was named Executive Vice President, General Counsel and Secretary effective February 18, 2016.\n"
    "Riley Test—Chief Financial Officer\n"
    "Mr. Test joined Example Corp in 2024 as Chief Financial Officer.\n"
    "GENERAL INFORMATION\nOther business."
)


def proxy_packet(form="DEF 14A", primary_report_date="2025-12-31", texts=None):
    texts = texts or [(0, DIRECTORS), (5000, OFFICERS), (5000 + len(OFFICERS), CONTINUATION)]
    narrative = [{"evidence_id": content_hash([loc, text]), "document_id": "proxy", "location": loc,
                  "section": "DEF 14A", "text": text} for loc, text in texts]
    return {"context": {"company": "Example Corp", "filing_period": "2025-12-31",
                        "management_document_id": "proxy", "management_document_sha256": "sha",
                        "management_document_format": "sec_proxy_html", "management_document_form": form,
                        "management_document_cik": "1", "management_document_accession": "0000000001-26-000001",
                        "management_document_filing_date": "2026-04-06",
                        "management_primary_report_date": primary_report_date},
            "evidence_ids": [n["evidence_id"] for n in narrative], "narrative_evidence": narrative,
            "packet_hash": "hash", "snapshot_version": 1}


def sid(sources, text):
    return next(s for s in sources.section_ids if text in sources.pages[s])


def executive(name, position, date, citations):
    (source_id, quote), *rest = citations
    return {"name": name, "position": position, "appointment_date": date, "source_id": source_id,
            "quote": quote, "supporting_citations": [{"source_id": s, "quote": q} for s, q in rest],
            "prior_internal_roles": [], "external_recruitment_quote": "", "external_source_id": ""}


def extraction(sources):
    return {
        "roster_date": "", "roster_source_id": "", "roster_quote": "",
        "executives": [
            executive("Alex Sample", "Chief Executive Officer", "2019-03-01",
                      [(sid(sources, "Mr. Sample became"),
                        "Mr. Sample became our Chief Executive Officer on March 1, 2019.")]),
            executive("Jamie Example", "Executive Vice President, General Counsel and Secretary", "2016-02-18",
                      [(sid(sources, "Ms. Exam"), "Ms. Exam"),
                       (sid(sources, "ple was named"), "ple was named Executive Vice President, General Counsel "
                                                       "and Secretary effective February 18, 2016.")]),
            executive("Riley Test", "Chief Financial Officer", "2024",
                      [(sid(sources, "Mr. Test joined"),
                        "Mr. Test joined Example Corp in 2024 as Chief Financial Officer.")]),
        ],
        "facts": [],
        "judgments": [{"judgment_id": "j1", "executive": "Riley Test", "signal": "neutral",
                       "category": "transition", "supporting_fact_ids": ["executive:3:appointment"],
                       "explanation": "A 2024 appointment; effects are not yet disclosed.", "uncertainties": []}],
        "overall_assessment": {"signal": "neutral", "explanation": "Neutral.", "supporting_judgment_ids": ["j1"],
                               "uncertainties": [], "missing_information": []},
    }


def test_proxy_roster_and_biographies_are_discovered():
    found = sources_from_evidence_packet(proxy_packet())
    assert found.document_kind == "DEF 14A"
    assert found.source_path == "sec-proxy:proxy"
    assert [(r["name"], r["position"]) for r in found.expected_roster] == [
        ("Alex Sample", "Chief Executive Officer; Director"),
        ("Jamie Example", "Executive Vice President, General Counsel and Secretary"),  # wrapped row
        ("Riley Test", "Chief Financial Officer")]
    assert found.coverage_verifiable
    # The CEO-director biography lives in the director section and is added as its own passage.
    assert any("Mr. Sample became" in found.pages[s] for s in found.section_ids)
    assert any("DEF 14A proxy statement" in item for item in found.limitations)


def test_proxy_claims_skip_the_roster_date_and_complete():
    sources = sources_from_evidence_packet(proxy_packet())
    result = assess_claims(extraction(sources), sources, reviewer=FixtureReviewer())
    validate_management(result)
    claims = {c["claim_id"]: c["outcome"] for c in result["validation"]["claim_results"]}
    assert "roster" not in claims
    assert {claims[f"executive:{i}:appointment"] for i in (1, 2, 3)} == {"accepted"}
    assert result["roster_date"] is None and result["review_window"] is None
    assert {p["recency"] for p in result["profiles"]} == {"unavailable"}
    assert result["coverage"] == {"expected": 3, "admitted": 3, "missing": [], "verifiable": True}
    assert result["completion_status"] == "complete"
    assert any("no as-of date" in item for item in result["limitations"])
    assert not any("unsupported" in item for item in result["limitations"])


def test_proxy_quote_from_another_biography_is_rejected():
    sources = sources_from_evidence_packet(proxy_packet())
    payload = extraction(sources)
    payload["facts"] = [{"fact_id": "f1", "executive": "Riley Test",
                         "statement": "Mr. Sample became our Chief Executive Officer",
                         "citations": [{"source_id": sid(sources, "Mr. Sample became"),
                                        "quote": "Mr. Sample became our Chief Executive Officer"}]}]
    result = assess_claims(payload, sources, reviewer=FixtureReviewer())
    fact = next(c for c in result["validation"]["claim_results"] if c["claim_id"] == "f1")
    assert fact["outcome"] == "unsupported"
    assert result["completion_status"] == "partial"


@pytest.mark.parametrize("form,report_date", [("DEFA14A", "2025-12-31"), ("DEF 14A", "2024-12-31")])
def test_proxy_must_be_a_def14a_linked_to_the_selected_period(form, report_date):
    with pytest.raises(ValueError, match="DEF 14A|period"):
        sources_from_evidence_packet(proxy_packet(form=form, primary_report_date=report_date))


def test_proxy_without_an_officer_section_is_an_information_gap():
    found = sources_from_evidence_packet(proxy_packet(texts=[(0, DIRECTORS)]))
    assert found.section_ids == [] and not found.coverage_verifiable
    assert any("No executive officers section" in item for item in found.limitations)


def test_bio_heading_must_repeat_the_surname():
    text = "Committee Members\nMr. Sample chairs the committee.\nAlex Sample\nMr. Sample became CEO."
    assert proxy_bio_starts(text) == [(text.index("Alex Sample"), "Alex Sample")]


def test_bio_sentence_needs_a_full_name():
    text = ("Jason K. Johnson serves as our Chief Financial Officer.\n"
            "Mr. Johnson serves as treasurer.\nThe Company serves as sponsor.\nHe has served as CFO.\n"
            "Riley Test has served as Controller since 2020.")
    assert [n for _, n in proxy_bio_starts(text)] == ["Jason K. Johnson", "Riley Test"]


# Another real layout (fictional names): a "Information About Our Executive Officers"
# heading, a table with stacked header cells, "Name serves as" biographies, a pointer
# for the CEO, and a CEO biography both in a summary grid and among director nominees.
SUMMARY_GRID = (
    "Name/Experience/Occupation\nDirector\nSince\n"
    "Alex Sample\nMr. Sample is our Chief Executive Officer.\n2025\n"
)
NOMINEES = (
    "Jordan Board\nDirector since 2019\nMs. Board is an independent director.\n"
    "Alex Sample\nDirector since 2025\n"
    "Mr. Sample has served as our Chief Executive Officer since December 2025.\n"
)
STACKED_OFFICERS = (
    "36\nINFORMATION ABOUT OUR EXECUTIVE OFFICERS\n"
    "Each of our executive officers holds an identical position with our subsidiaries:\n"
    "Name\nAge\nPosition\n"
    "Alex Sample\n60\nChief Executive Officer\n"
    "Riley Test\n51\nExecutive Vice President and Chief Financial Officer\n"
    "Alex Sample — The principal occupation and employment experience of Mr. Sample during the "
    "last five years is set forth on page 27 of this Proxy Statement.\n"
    "Riley Test serves as our Executive Vice President and Chief Financial Officer since 2024.\n"
    "38\nPROPOSAL 1 — ELECTION OF DIRECTORS\n"
    # Read as an unrostered officer biography if the section ran past the heading.
    "Jordan Board serves as an independent director.\n"
)


def test_stacked_proxy_layout_is_discovered():
    texts = [(0, SUMMARY_GRID), (4000, NOMINEES), (9000, STACKED_OFFICERS)]
    found = sources_from_evidence_packet(proxy_packet(texts=texts))
    assert [(r["name"], r["position"]) for r in found.expected_roster] == [
        ("Alex Sample", "Chief Executive Officer"),
        ("Riley Test", "Executive Vice President and Chief Financial Officer")]
    assert found.coverage_verifiable, found.limitations
    pages = [found.pages[s] for s in found.section_ids]
    # The CEO is reconciled to the director-nominee biography, not the summary grid.
    assert any("Mr. Sample has served" in p for p in pages)
    assert not any("Mr. Sample is our" in p for p in pages)


def test_linked_proxy_matches_cik_and_filing_window():
    tenk = {"parser": "inline_xbrl", "cik": "1", "filing_date": "2026-02-20"}

    def proxy(filed, cik="1", form="DEF 14A"):
        return {"role": "proxy", "form": form, "cik": cik, "filing_date": filed}

    assert _linked_proxy([proxy("2026-04-06")], tenk)["filing_date"] == "2026-04-06"
    assert _linked_proxy([proxy("2026-01-10")], tenk)["filing_date"] == "2026-01-10"  # 41 days before
    assert _linked_proxy([proxy("2026-03-01"), proxy("2026-04-06")], tenk)["filing_date"] == "2026-03-01"
    for other in (proxy("2026-04-06", cik="2"), proxy("2026-04-06", form="DEFA14A"),
                  proxy("2025-12-01"), proxy("2026-09-30")):
        assert _linked_proxy([other], tenk) is None
    assert _linked_proxy([proxy("2026-04-06")], {**tenk, "parser": "pdf"}) is None


def test_memo_names_the_management_source():
    def view(path):
        artifact = {"source": {"path": path}}
        return _m3_v2_view({"m3_rebuild": {"management": {"artifact": artifact}}})["management"]["source"]

    assert view("sec-proxy:abc").startswith("DEF 14A proxy statement")
    assert view("sec-inline-xbrl:abc") == "Form 10-K"
    assert _m3_v2_view({"m3_rebuild": {}})["management"]["source"] is None
