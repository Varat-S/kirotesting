"""PR #3 Task 2: the JSON->PDF exporter never raises on oversized content.

A completed, scored FO artifact with an oversized table cell previously caused a
reportlab ``LayoutError`` (HTTP 500). These tests pin that long strings, nested
JSON, wide tables and unusually large single cells all render to a readable,
complete PDF, that content is preserved (not truncated), and that determinism
and normal (small) rendering are unchanged.
"""

from __future__ import annotations

import pytest

from app.services.registry.json_render import render_json_pdf

_LONG = "The composite reflects " + (
    "a materially elevated leverage profile with sustained refinancing exposure "
    "across multiple senior-note tranches and covenant headroom that compresses "
    "under stress; " * 70)


def _is_pdf(b: bytes) -> bool:
    return b[:4] == b"%PDF"


def _pdf_text(b: bytes) -> str:
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(io.BytesIO(b))
    return "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def test_oversized_scalar_in_dict_renders():
    payload = {"score": 6.4, "reason": _LONG}
    assert _is_pdf(render_json_pdf(payload, title="x"))


def test_oversized_cell_in_records_table_renders():
    payload = {"escalations": [{"id": f"e{i}", "reason": _LONG} for i in range(4)]}
    assert _is_pdf(render_json_pdf(payload, title="x"))


def test_wide_and_tall_table_renders():
    payload = {"rows": [{f"c{i}": _LONG for i in range(12)} for _ in range(6)]}
    assert _is_pdf(render_json_pdf(payload, title="x"))


def test_deeply_nested_large_blob_renders():
    payload = {"nested": {"deep": {"items": [_LONG] * 20}}, "score": 7}
    assert _is_pdf(render_json_pdf(payload, title="x"))


def test_very_long_unbroken_string_renders():
    payload = {"blob": "x" * 60000}
    assert _is_pdf(render_json_pdf(payload, title="x"))


def test_long_list_of_records_renders():
    payload = {"log": [{"step": i, "note": "ok"} for i in range(200)]}
    assert _is_pdf(render_json_pdf(payload, title="x"))


def test_content_is_preserved_not_truncated():
    marker = "UNIQUEMARKER1234567890"
    payload = {"reason": _LONG + marker}
    pdf = render_json_pdf(payload, title="x")
    # The marker (at the END of the oversized value) survives into the extracted
    # PDF text — the long value is paginated, never truncated.
    assert marker in _pdf_text(pdf)


def test_pdf_is_deterministic_for_oversized_input():
    payload = {"escalations": [{"id": f"e{i}", "reason": _LONG} for i in range(4)]}
    assert render_json_pdf(payload, title="x") == render_json_pdf(payload, title="x")


def test_scored_fo_fixture_with_oversized_reason_renders():
    from tests.integration.test_fo_registry_indexing import _fo_payload
    payload = _fo_payload()
    payload["metrics"][1]["columns"][0]["reason"] = _LONG
    pdf = render_json_pdf(payload, title="fo.overview.fo-demo-1", subtitle="Case DEMO")
    assert _is_pdf(pdf)
    # The seven measures survive into the export (not dropped).
    text = _pdf_text(pdf).lower()
    for key in ("dscr", "leverage", "liquidity"):
        assert key in text


def test_normal_small_payload_unchanged_and_deterministic():
    payload = {"schema_version": "bo-memo-1.0", "document": "business_overview_memo",
               "status": {"conclusion_status": "accepted"},
               "sections": [{"code": "BO1", "title": "Revenue"}]}
    a = render_json_pdf(payload, title="bo.memo")
    b = render_json_pdf(payload, title="bo.memo")
    assert _is_pdf(a) and a == b
