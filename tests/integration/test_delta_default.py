"""The default case uses the supplied PDFs and distinguishes quarter/year/basis."""

import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from app.models.orm import AuditEvent, EvidencePreview, HumanReview, ModelRun
from app.services.extraction.base import ParserError
from app.services.extraction.pdf_table import PdfTableParser
from app.services.pipeline.default_case import DEFAULT_CASE_ID, default_package
from tests.integration.test_upload_preview import client as client
from tests.integration.test_upload_preview import count, payload


def _ocr_available() -> bool:
    """True when an OCR engine (Tesseract, or Windows OCR) is usable.

    The default Delta case includes an image-only non-GAAP supplement whose
    contents can only be recognized with OCR, so this test cannot pass without
    an OCR engine. It is skipped (not failed) in environments that lack one,
    exactly as the PDF-export test skips without a native HTML->PDF backend.
    """
    if shutil.which("tesseract"):
        try:
            import pytesseract  # noqa: F401

            return True
        except Exception:
            return False
    # Native Windows OCR fallback used by app.services.extraction.pdf_capture.
    import sys

    return sys.platform.startswith("win")


pytestmark = pytest.mark.skipif(
    not _ocr_available(),
    reason="No OCR engine (Tesseract/Windows OCR) available; the default Delta "
    "case requires OCR for its image-only supplement.",
)


def test_default_delta_case_and_source_viewer(client, db_session):
    page = client.get("/inspect")
    assert "Default test case: Delta" in page.text
    response = client.post("/inspect/default", follow_redirects=False)
    value = payload(client, response)
    assert value["case_id"] == DEFAULT_CASE_ID
    evidence = value["evidence"]
    assert len(evidence["documents"]) == 3
    assert len(evidence["facts"]) == 358
    assert len(evidence["narrative_evidence"]) > 20
    revenue = [f for f in evidence["facts"] if f["name"] == "revenue"]
    assert {
        (f["period_type"], f["fiscal_year"], f["normalized_value"]) for f in revenue
    } == {
        ("FY", 2025, 63364),
        ("FY", 2024, 61643),
        ("FY", 2023, 58048),
        ("duration", 2025, 16003),
        ("duration", 2024, 15559),
    }
    assert value["metrics"]["operating_margin"]["result"] == pytest.approx(5822 / 63364)
    assert value["metrics"]["revenue_growth"]["result"] == pytest.approx(
        63364 / 61643 - 1
    )
    assert value["metrics"]["free_cash_flow"]["result"] == 3843
    assert any(
        f["name"] == "cfo"
        and f["period_type"] == "FY"
        and f["fiscal_year"] == 2025
        and f["normalized_value"] == 8342
        for f in evidence["facts"]
    )
    assert {f["accounting_basis"] for f in evidence["facts"]} == {"GAAP", "non-GAAP"}
    supplement = next(d for d in evidence["documents"] if "Non-GAAPs" in d["filename"])
    assert supplement["extraction_coverage"]["ocr_pages"] == 6
    assert all(
        d["extraction_coverage"]["readable_pages"]
        == d["extraction_coverage"]["total_pages"]
        for d in evidence["documents"]
    )
    html = client.get(response.headers["location"]).text
    assert "9 of 11 configured metrics" in html
    assert "358 of 358 extracted observations are mapped" in html
    assert "2 unavailable calculations" in html
    assert "9.19%" in html and "2.79%" in html
    manifest = client.get(response.headers["location"] + "/artifacts.json").json()
    checks = client.get(
        next(a["url"] for a in manifest["artifacts"] if a["name"] == "reference_checks")
    ).json()
    assert checks["passed"] == checks["total"] == 71
    assert checks["ocr_recognition"]["recognized_reference_cells"] == 182
    assert checks["ocr_recognition"]["reference_cells"] == 185
    for doc in evidence["documents"]:
        capture = client.get(
            next(
                a["url"]
                for a in manifest["artifacts"]
                if a["name"] == "document_" + doc["document_id"]
            )
        ).json()
        assert not capture["coverage"]["failed_pages"]
        if capture["coverage"]["native_pages"]:
            assert capture["coverage"]["native_glyph_retention_fraction"] > 0.99
        for page in capture["pages"]:
            passages = sorted(
                [
                    p
                    for p in evidence["narrative_evidence"]
                    if p["document_id"] == doc["document_id"]
                    and p["page"] == page["page"]
                ],
                key=lambda p: p["location"],
            )
            assert "".join(p["text"] for p in passages) == page["text"]
    for name, expected in {
        "total_assets": {81185, 81317},
        "other_noncurrent_assets": {4291, 4421},
    }.items():
        quality = next(
            v
            for k, v in evidence["data_quality"].items()
            if f'"{name}"' in k and "2025" in k
        )
        assert quality["state"] == "conflicting" and set(quality["values"]) == expected
    assert value["metrics"]["interest_coverage"]["result"] is None
    assert value["metrics"]["liquidity"]["result"] is None
    assert value["metrics"]["net_debt_to_ebitda"]["review_required"]
    assert count(db_session, ModelRun) == count(db_session, HumanReview) == 0
    before = count(db_session, AuditEvent)
    assert (
        client.post("/inspect/default", follow_redirects=False).headers["location"]
        == response.headers["location"]
    )
    assert count(db_session, AuditEvent) == before
    assert count(db_session, EvidencePreview) == 1
    source = client.get("/inspect/default/files/DeltaAirlines_10k_2025.pdf")
    assert (
        source.status_code == 200
        and source.headers["content-type"] == "application/pdf"
    )
    assert (
        source.content
        == Path(
            next(s.path for s in default_package().sources if "10k" in s.filename)
        ).read_bytes()
    )
    assert client.get("/inspect/default/files/not-a-default.pdf").status_code == 404


@pytest.mark.parametrize("change", ["heading", "columns", "row", "period"])
def test_pdf_recipe_fails_if_its_declared_table_does_not_match(change):
    source = default_package().sources[1]
    recipes = deepcopy(source.parse_options["statement_recipes"][:1])
    if change == "heading":
        recipes[0]["required_text"].append("Wrong statement header")
    elif change == "columns":
        recipes[0]["value_count"] = 7
    elif change == "row":
        recipes[0]["rows"][0]["label"] = "Imaginary revenue"
    else:
        recipes[0]["periods"]["FY_2025"] = {}
    with pytest.raises(ParserError):
        PdfTableParser().parse(
            Path(source.path).read_bytes(),
            document_id="earnings",
            scale="millions",
            currency="USD",
            statement_recipes=recipes,
        )
