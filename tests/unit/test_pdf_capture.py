"""OCR boundaries: scanned/vector pages, missing engines, and cache tampering."""

import io
import json
from hashlib import sha256

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.core.hashing import content_hash
from app.services.extraction.base import ParserError
from app.services.extraction.pdf_capture import capture_pdf


def vector_pdf():
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    canvas.rect(20, 20, 80, 80)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def test_vector_only_page_invokes_ocr_and_retains_word_boxes(monkeypatch):
    import app.services.extraction.pdf_capture as module

    def fake_ocr(image):
        assert image.width > 0
        return {
            "engine": "fixture",
            "words": [
                {"text": "Revenue", "x": 10, "y": 10, "width": 70, "height": 15},
                {"text": "1,234", "x": 100, "y": 10, "width": 40, "height": 15},
            ],
        }

    monkeypatch.setattr(module, "ocr_image", fake_ocr)
    result = capture_pdf(vector_pdf(), document_id="vector")
    assert result["coverage"]["ocr_pages"] == 1
    assert result["pages"][0]["text"] == "Revenue 1,234"
    assert result["pages"][0]["words"][1]["x"] == 100
    assert result["pages"][0]["numeric_rows"][0]["tokens"] == ["1,234"]
    assert result["passages"][0]["status"] == "unverified"


def test_unavailable_ocr_is_reported_without_fabricated_text(monkeypatch):
    import app.services.extraction.pdf_capture as module

    def fail(image):
        raise ParserError("OCR unavailable")

    monkeypatch.setattr(module, "ocr_image", fail)
    result = capture_pdf(vector_pdf(), document_id="failed")
    assert result["coverage"]["failed_pages"] == [1]
    assert result["coverage"]["readable_pages"] == 0
    assert not result["passages"]


def test_ocr_cache_is_bound_to_source_and_payload(tmp_path):
    source = vector_pdf()
    pages = [{"page": 1, "text": "Stored OCR 123", "words": [], "engine": "fixture"}]
    packet = {
        "pdf_sha256": sha256(source).hexdigest(),
        "payload_sha256": content_hash(pages),
        "pages": pages,
    }
    path = tmp_path / "ocr.json"
    path.write_text(json.dumps(packet))
    cache = {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
    result = capture_pdf(source, document_id="cached", ocr_cache=cache)
    assert result["pages"][0]["text"] == "Stored OCR 123"
    assert result["pages"][0]["ocr_cache_reused"]
    packet["pages"][0]["text"] = "Tampered 999"
    path.write_text(json.dumps(packet))
    with pytest.raises(ParserError, match="checksum"):
        capture_pdf(source, document_id="cached", ocr_cache=cache)
    cache["sha256"] = sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ParserError, match="altered"):
        capture_pdf(source, document_id="cached", ocr_cache=cache)
