"""Evidence retrieval tests (Business Overview Module 1, Task 4).

Deterministic, offline tests over synthetic multi-page PDFs built with reportlab
(native text, read back by pdfplumber via the reused ``capture_pdf``). They
cover corpus construction (page addressing, stable ids, unreadable pages) and
the lexical ``EvidenceSearchService`` (multi-query/intent, multi-document,
permitted-scope restriction, no-match/unavailable outcomes, determinism, text
preservation, and the cross-section discovery scenario).

Scope honesty: these prove the retrieval PLUMBING and lexical ranking only. They
do NOT demonstrate AI query planning or semantic verification -- a lexical match
is not proof a passage supports a hypothesis. Query-planning is a later task.

Two fictional industries are used: a precision-parts manufacturer and a B2B
software provider. No HCA/healthcare/nursing specifics.
"""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.schemas.enums import ExtractionMethod
from app.schemas.evidence import (
    EvidenceSearchIntent,
    EvidenceSearchOutcome,
    EvidenceSearchRequest,
)
from app.services.analysis.evidence_retrieval import (
    RETRIEVAL_VERSION,
    CorpusBuilder,
    EvidenceSearchService,
    build_corpus_from_pdf,
)
from app.services.extraction.base import ParserError


# ---------------------------------------------------------------------------
# Synthetic PDF builders (native text; real sentences for lexical search)
# ---------------------------------------------------------------------------


def _pdf(pages: list[list[str]]) -> bytes:
    """Render one page per inner list; each string is a line of body text."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    for lines in pages:
        y = 800
        for line in lines:
            canvas.drawString(72, y, line)
            y -= 18
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


# A fictional manufacturer: the vulnerability (specialised technicians shortage)
# is on page 1; the company-specific offset (an apprenticeship/training
# programme) is on a SEPARATE page 3. Page 2 is unrelated boilerplate.
MANUFACTURER_PAGES = [
    [
        "Section 1 Operations Overview",
        "The company depends on a shortage of specialised machining technicians.",
        "A scarcity of qualified technicians could constrain production capacity.",
    ],
    [
        "Section 2 Corporate Governance",
        "The board met four times during the fiscal year.",
        "Audit and remuneration committees operate under written charters.",
    ],
    [
        "Section 3 People and Workforce",
        "The company launched an apprenticeship and training initiative for technicians.",
        "The internal talent pipeline aims to improve retention and workforce resilience.",
    ],
]

# A fictional B2B software provider in a different industry.
SOFTWARE_PAGES = [
    [
        "Overview",
        "Subscription revenue is recognised rateably over each contract term.",
        "A single large customer represents a significant share of recurring revenue.",
    ],
    [
        "Risk Factors",
        "Loss of the largest customer would materially reduce recurring revenue.",
        "High gross retention and deep product integration raise switching costs.",
    ],
]


def _manufacturer_pdf() -> bytes:
    return _pdf(MANUFACTURER_PAGES)


def _software_pdf() -> bytes:
    return _pdf(SOFTWARE_PAGES)


# ---------------------------------------------------------------------------
# 1-4, 13-14: corpus construction, provenance, stability, text preservation
# ---------------------------------------------------------------------------


def test_multi_page_text_extraction_and_page_numbers():
    corpus = build_corpus_from_pdf("MFG", _manufacturer_pdf())
    pages = sorted({p.pdf_page for p in corpus.passages})
    assert pages == [1, 2, 3]  # all three pages searchable
    for passage in corpus.passages:
        assert passage.document_id == "MFG"
        assert passage.document_hash  # version/content hash retained
        assert passage.printed_page_label is None  # never invented
        # Native-text pages map to the PDF_TEXT extraction method.
        assert passage.extraction_method is ExtractionMethod.PDF_TEXT


def test_passage_ids_are_stable_across_reprocessing():
    data = _manufacturer_pdf()
    first = {p.pdf_page: p.passage_id for p in build_corpus_from_pdf("MFG", data).passages}
    second = {p.pdf_page: p.passage_id for p in build_corpus_from_pdf("MFG", data).passages}
    assert first == second  # identical bytes -> identical ids


def test_original_text_is_preserved_verbatim():
    corpus = build_corpus_from_pdf("MFG", _manufacturer_pdf())
    joined = "\n".join(p.text for p in corpus.passages)
    assert "apprenticeship and training initiative" in joined
    assert "specialised machining technicians" in joined


def test_long_page_is_split_into_multiple_passages():
    long_line = "resilience " * 400  # ~4400 chars -> >1 passage at 2000-char slices
    corpus = build_corpus_from_pdf("LONG", _pdf([[long_line]]))
    page1 = [p for p in corpus.passages if p.pdf_page == 1]
    assert len(page1) >= 2
    offsets = sorted(p.char_offset for p in page1)
    assert offsets[0] == 0 and offsets[1] == 2000  # deterministic slicing


# ---------------------------------------------------------------------------
# 5-7: queries locate relevant passages across sections and documents
# ---------------------------------------------------------------------------


def test_cross_section_discovery_vulnerability_and_offset():
    """Vulnerability (page 1) and the offset (page 3) are both retrievable.

    NOT a claim of AI discovery -- the queries are supplied; this only shows the
    separate initiative is reachable when a later agent asks for it.
    """
    corpus = build_corpus_from_pdf("MFG", _manufacturer_pdf())
    service = EvidenceSearchService(corpus)
    request = EvidenceSearchRequest(
        queries=[
            "shortage of specialised technicians",
            "apprenticeship training workforce development pipeline",
        ],
        permitted_document_ids=["MFG"],
        intent=EvidenceSearchIntent.OFFSETTING_CAPABILITY,
    )
    results = service.search(request)
    assert len(results) == 2

    vuln = results[0]
    assert vuln.outcome is EvidenceSearchOutcome.MATCHES_FOUND
    assert vuln.ranked_passages[0].passage.pdf_page == 1

    offset = results[1]
    assert offset.outcome is EvidenceSearchOutcome.MATCHES_FOUND
    assert offset.ranked_passages[0].passage.pdf_page == 3
    assert "apprenticeship" in offset.ranked_passages[0].passage.text


def test_multiple_documents_and_scope_restriction():
    corpus = CorpusBuilder().build(
        [("MFG", _manufacturer_pdf()), ("SW", _software_pdf())]
    )
    service = EvidenceSearchService(corpus)

    # Query only within SW; MFG must not be searched even though it is in corpus.
    request = EvidenceSearchRequest(
        queries=["recurring subscription revenue customer concentration"],
        permitted_document_ids=["SW"],
        intent=EvidenceSearchIntent.GENERAL_INVESTIGATION,
        max_results=10,
    )
    [result] = service.search(request)
    assert result.outcome is EvidenceSearchOutcome.MATCHES_FOUND
    assert result.searched_document_ids == ["SW"]
    assert {rp.passage.document_id for rp in result.ranked_passages} == {"SW"}


def test_max_results_is_respected():
    corpus = CorpusBuilder().build([("SW", _software_pdf())])
    service = EvidenceSearchService(corpus)
    request = EvidenceSearchRequest(
        queries=["revenue"],
        permitted_document_ids=["SW"],
        max_results=1,
    )
    [result] = service.search(request)
    assert len(result.ranked_passages) == 1
    assert result.ranked_passages[0].rank == 1


# ---------------------------------------------------------------------------
# 8-10: scope/outcome labels
# ---------------------------------------------------------------------------


def test_document_not_permitted_is_source_not_available():
    corpus = CorpusBuilder().build([("MFG", _manufacturer_pdf())])
    service = EvidenceSearchService(corpus)
    request = EvidenceSearchRequest(
        queries=["anything"],
        permitted_document_ids=["DOES_NOT_EXIST"],
    )
    [result] = service.search(request)
    assert result.outcome is EvidenceSearchOutcome.SOURCE_NOT_AVAILABLE
    assert result.unavailable_document_ids == ["DOES_NOT_EXIST"]
    assert result.ranked_passages == []


def test_no_match_outcome_when_corpus_has_no_relevant_terms():
    corpus = CorpusBuilder().build([("MFG", _manufacturer_pdf())])
    service = EvidenceSearchService(corpus)
    request = EvidenceSearchRequest(
        queries=["zzzxylophonequarkglyphnonsense"],
        permitted_document_ids=["MFG"],
    )
    [result] = service.search(request)
    assert result.outcome is EvidenceSearchOutcome.NO_MATCH_FOUND
    assert result.ranked_passages == []


def test_unreadable_document_is_source_unsearchable(monkeypatch):
    """An image-only page (OCR unavailable) yields no passages -> unsearchable."""
    import app.services.extraction.pdf_capture as capture_module

    def fail(image):
        raise ParserError("OCR unavailable")

    monkeypatch.setattr(capture_module, "ocr_image", fail)

    # A vector-only (no text) page: capture_pdf finds it unreadable.
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    canvas.rect(20, 20, 80, 80)
    canvas.showPage()
    canvas.save()
    vector_only = buffer.getvalue()

    corpus = build_corpus_from_pdf("SCAN", vector_only)
    assert corpus.passages == []
    assert corpus.unreadable_pages["SCAN"] == [1]

    service = EvidenceSearchService(corpus)
    [result] = service.search(
        EvidenceSearchRequest(queries=["revenue"], permitted_document_ids=["SCAN"])
    )
    assert result.outcome is EvidenceSearchOutcome.SOURCE_UNSEARCHABLE


def test_unopenable_document_recorded_without_fabrication(monkeypatch):
    """A document capture_pdf cannot open is recorded as present-but-unsearchable."""
    import app.services.analysis.evidence_retrieval as module

    def boom(*args, **kwargs):
        raise ParserError("cannot open")

    monkeypatch.setattr(module, "capture_pdf", boom)
    corpus = build_corpus_from_pdf("BROKEN", b"not a real pdf")
    assert corpus.passages == []
    assert "BROKEN" in corpus.document_ids
    service = EvidenceSearchService(corpus)
    [result] = service.search(
        EvidenceSearchRequest(queries=["x"], permitted_document_ids=["BROKEN"])
    )
    assert result.outcome is EvidenceSearchOutcome.SOURCE_UNSEARCHABLE


# ---------------------------------------------------------------------------
# 11-12: no fabrication; deterministic ordering
# ---------------------------------------------------------------------------


def test_results_reference_only_real_passages():
    corpus = build_corpus_from_pdf("MFG", _manufacturer_pdf())
    valid_ids = {p.passage_id for p in corpus.passages}
    service = EvidenceSearchService(corpus)
    [result] = service.search(
        EvidenceSearchRequest(
            queries=["technicians workforce"], permitted_document_ids=["MFG"]
        )
    )
    for rp in result.ranked_passages:
        assert rp.passage.passage_id in valid_ids  # no invented passage
        assert rp.passage.document_id == "MFG"


def test_deterministic_result_ordering_and_ids():
    # Same source bytes -> identical corpus -> identical ids/order. (reportlab
    # embeds a timestamp, so two renders differ; determinism is defined over
    # identical bytes, which is the reproducible-retrieval contract.)
    data = _manufacturer_pdf()
    service_a = EvidenceSearchService(CorpusBuilder().build([("MFG", data)]))
    service_b = EvidenceSearchService(CorpusBuilder().build([("MFG", data)]))
    request = EvidenceSearchRequest(
        queries=["technicians resilience retention"],
        permitted_document_ids=["MFG"],
        max_results=10,
    )
    run1 = service_a.search(request)[0]
    run2 = service_b.search(request)[0]
    assert run1.query_id == run2.query_id
    assert run1.retrieval_version == RETRIEVAL_VERSION
    assert [rp.passage.passage_id for rp in run1.ranked_passages] == [
        rp.passage.passage_id for rp in run2.ranked_passages
    ]
    assert [rp.score for rp in run1.ranked_passages] == [
        rp.score for rp in run2.ranked_passages
    ]


def test_metadata_filter_restricts_results():
    corpus = CorpusBuilder().build(
        [
            ("MFG", _manufacturer_pdf(), {"doc_type": "annual_report"}),
            ("SW", _software_pdf(), {"doc_type": "s1"}),
        ]
    )
    service = EvidenceSearchService(corpus)
    request = EvidenceSearchRequest(
        queries=["revenue"],
        permitted_document_ids=["MFG", "SW"],
        metadata_filters={"doc_type": "s1"},
    )
    [result] = service.search(request)
    assert result.searched_document_ids == ["SW"]
    assert all(rp.passage.document_id == "SW" for rp in result.ranked_passages)


# ---------------------------------------------------------------------------
# 15: schema compatibility
# ---------------------------------------------------------------------------


def test_result_round_trips_and_passage_yields_source_ref():
    corpus = build_corpus_from_pdf("MFG", _manufacturer_pdf())
    service = EvidenceSearchService(corpus)
    [result] = service.search(
        EvidenceSearchRequest(
            queries=["technicians"], permitted_document_ids=["MFG"]
        )
    )
    dumped = result.model_dump(mode="json")
    assert dumped["retrieval_version"] == RETRIEVAL_VERSION
    assert dumped["intent"] == "general_investigation"
    ref = result.ranked_passages[0].passage.source_ref()
    assert ref.document_id == "MFG"
    assert ref.page == result.ranked_passages[0].passage.pdf_page
