"""Deterministic evidence retrieval over document narrative text (Stage 5 infra).

This module provides SEARCHABLE access to the full narrative contents of
ingested documents -- the capability Stage 5 ("Identify Required Evidence &
Retrieve Additional Sources") needs, and which the pattern-based field parsers
do NOT provide (they pull configured fields, not whole-report text).

Two pieces, both deterministic and offline:

* :func:`build_corpus_from_pdf` / :class:`CorpusBuilder` -- turn a document's
  bytes into page-addressable :class:`~app.schemas.evidence.EvidencePassage`
  objects. It REUSES the existing :func:`app.services.extraction.pdf_capture.
  capture_pdf` (native text + OCR, with coverage of unreadable pages) rather
  than re-implementing PDF text extraction. Passage text is copied verbatim;
  stable ``passage_id`` values are content hashes, so repeated processing of the
  same bytes yields identical ids.

* :class:`EvidenceSearchService` -- a lexical retrieval service that scores
  passages against free-text queries with a transparent TF-based overlap score
  (no vector DB, no external deps, no network). It honours an explicit
  permitted-document allow-list, reports distinct per-query outcomes, and orders
  results deterministically so repeated searches reproduce exactly.

GOVERNANCE (critical): a returned passage is SOURCE EVIDENCE, not a verified
fact. Retrieval relevance is not credibility; a match does not prove a
hypothesis or establish causality; and a no-match outcome is NOT proof that a
capability or fact is absent. Text inside documents is untrusted and is only
ranked/returned, never executed or interpreted as instructions. Semantic
verification is a later, separate stage.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from app.core.hashing import content_hash
from app.schemas.enums import ExtractionMethod
from app.schemas.evidence import (
    EvidenceCorpus,
    EvidencePassage,
    EvidenceSearchOutcome,
    EvidenceSearchRequest,
    EvidenceSearchResult,
    RankedPassage,
)
from app.services.extraction.base import ParserError
from app.services.extraction.pdf_capture import capture_pdf

# Bump when the ranking algorithm changes so results stay reproducible/traceable.
RETRIEVAL_VERSION = "lexical-tf-1"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# capture_pdf labels pages "native" | "ocr" | "blank"; map those to the shared
# ExtractionMethod vocabulary. Native page text is PDF_TEXT; OCR is OCR. A
# "blank" page produces no searchable passage, so it never reaches this map.
_CAPTURE_METHOD_TO_EXTRACTION = {
    "native": ExtractionMethod.PDF_TEXT,
    "ocr": ExtractionMethod.OCR,
}


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokenisation (deterministic, dependency-free)."""
    return _TOKEN_RE.findall(text.lower())


# ---------------------------------------------------------------------------
# Corpus construction (reuses capture_pdf)
# ---------------------------------------------------------------------------


@dataclass
class CorpusBuilder:
    """Build an :class:`EvidenceCorpus` from ingested document bytes.

    ``enable_ocr`` is forwarded to :func:`capture_pdf`; an image-only page that
    cannot be read is recorded in ``unreadable_pages`` rather than dropped.
    """

    enable_ocr: bool = True

    def add_pdf(
        self,
        corpus: EvidenceCorpus,
        data: bytes,
        *,
        document_id: str,
        document_metadata: dict | None = None,
    ) -> EvidenceCorpus:
        """Extract and append one PDF's passages into ``corpus`` (in place)."""
        # Stable content hash of the exact source bytes (document version id).
        document_hash = content_hash(data.decode("latin-1"))
        try:
            captured = capture_pdf(
                data, document_id=document_id, enable_ocr=self.enable_ocr
            )
        except ParserError:
            # A document that cannot be opened at all is unsearchable; record it
            # with no passages so search reports source_unsearchable, not silence.
            if document_id not in corpus.document_ids:
                corpus.document_ids.append(document_id)
            corpus.document_hashes[document_id] = document_hash
            corpus.unreadable_pages.setdefault(document_id, [])
            return corpus

        if document_id not in corpus.document_ids:
            corpus.document_ids.append(document_id)
        corpus.document_hashes[document_id] = document_hash
        corpus.unreadable_pages[document_id] = list(captured["coverage"]["failed_pages"])

        metadata = dict(document_metadata or {})
        for raw in captured["passages"]:
            if not raw["text"].strip():
                continue  # blank slice: nothing to search, nothing to invent
            method = _CAPTURE_METHOD_TO_EXTRACTION.get(
                raw.get("extraction_method", "")
            )
            passage = EvidencePassage(
                # Stable id derived from source identity + location + content.
                passage_id=content_hash(
                    {
                        "document_id": document_id,
                        "document_hash": document_hash,
                        "pdf_page": raw["page"],
                        "char_offset": raw["location"],
                        "text": raw["text"],
                    }
                ),
                document_id=document_id,
                document_hash=document_hash,
                pdf_page=raw["page"],
                printed_page_label=None,  # never invent a printed page number
                text=raw["text"],
                char_offset=raw["location"],
                extraction_method=method,
                document_metadata=metadata,
            )
            corpus.passages.append(passage)
        return corpus

    def build(
        self,
        documents: list[tuple[str, bytes]] | list[tuple[str, bytes, dict]],
    ) -> EvidenceCorpus:
        """Build a corpus from ``(document_id, data[, metadata])`` tuples."""
        corpus = EvidenceCorpus()
        for entry in documents:
            if len(entry) == 3:
                document_id, data, metadata = entry  # type: ignore[misc]
            else:
                document_id, data = entry  # type: ignore[misc]
                metadata = {}
            self.add_pdf(
                corpus, data, document_id=document_id, document_metadata=metadata
            )
        return corpus


def build_corpus_from_pdf(
    document_id: str, data: bytes, *, enable_ocr: bool = True, document_metadata=None
) -> EvidenceCorpus:
    """Convenience: build a one-document corpus."""
    return CorpusBuilder(enable_ocr=enable_ocr).add_pdf(
        EvidenceCorpus(), data, document_id=document_id, document_metadata=document_metadata
    )


# ---------------------------------------------------------------------------
# HTML / inline-XBRL (SEC) corpus adapter -- reuses the existing SEC narrative
# extraction (no PDF pages are fabricated for an HTML filing).
# ---------------------------------------------------------------------------


def build_corpus_from_sec_narrative(
    narrative_passages: list[dict],
    *,
    document_id: str,
    document_hash: str | None = None,
    document_metadata: dict | None = None,
) -> EvidenceCorpus:
    """Wrap SEC narrative candidate-passages into a searchable corpus.

    ``narrative_passages`` is the list produced by
    :func:`app.services.extraction.sec.narrative.narrative_evidence` (via
    ``parse_bundle``): dicts with ``document_id``, ``section``, ``heading``,
    ``text``, ``location`` and ``evidence_id``. Each becomes an
    :class:`EvidencePassage` whose location is HTML-native (``section`` +
    ``char_offset``) with NO fabricated ``pdf_page``. Text is copied verbatim;
    passage ids are stable content hashes over source identity + location +
    text, so repeated processing of the same bytes is reproducible.
    """
    corpus = EvidenceCorpus()
    if document_id not in corpus.document_ids:
        corpus.document_ids.append(document_id)
    corpus.document_hashes[document_id] = document_hash or ""
    corpus.unreadable_pages[document_id] = []
    metadata = dict(document_metadata or {})
    for raw in narrative_passages:
        text = raw.get("text") or ""
        if not text.strip():
            continue
        pid = content_hash(
            {
                "document_id": document_id,
                "document_hash": document_hash,
                "section": raw.get("section"),
                "char_offset": raw.get("location"),
                "text": text,
            }
        )
        corpus.passages.append(
            EvidencePassage(
                passage_id=pid,
                document_id=document_id,
                document_hash=document_hash,
                pdf_page=None,  # HTML filing -> never fabricate a PDF page
                section=raw.get("section"),
                source_location=raw.get("heading"),
                text=text,
                char_offset=raw.get("location"),
                extraction_method=ExtractionMethod.INLINE_XBRL,
                document_metadata={
                    **metadata,
                    **({"sec_accession": raw["sec_accession"]}
                       if raw.get("sec_accession") else {}),
                },
            )
        )
    return corpus


# ---------------------------------------------------------------------------
# Deterministic lexical search
# ---------------------------------------------------------------------------


class EvidenceSearchService:
    """Lexical retrieval over an :class:`EvidenceCorpus` (deterministic, offline).

    Scoring is a transparent token-overlap sum: for each distinct query token,
    add the passage's term frequency for that token. No stemming, no external
    index, no network. Ties break by (pdf_page, char_offset, passage_id) so the
    ordering is fully reproducible. The service never mutates passages and never
    searches outside the request's permitted-document allow-list.
    """

    def __init__(self, corpus: EvidenceCorpus) -> None:
        self._corpus = corpus
        # Pre-tokenise once; reused across queries for stable, cheap scoring.
        self._token_counts: dict[str, Counter[str]] = {
            p.passage_id: Counter(_tokenize(p.text)) for p in corpus.passages
        }

    def search(self, request: EvidenceSearchRequest) -> list[EvidenceSearchResult]:
        """Run every query in ``request``; return one result per query (in order)."""
        permitted = list(dict.fromkeys(request.permitted_document_ids))
        known = set(self._corpus.document_ids)
        unavailable = sorted(d for d in permitted if d not in known)
        available = [d for d in permitted if d in known]

        # Candidate passages: permitted documents, matching any metadata filters.
        candidates = [
            p
            for p in self._corpus.passages
            if p.document_id in available
            and self._matches_filters(p, request.metadata_filters)
        ]
        searchable_docs = sorted({p.document_id for p in candidates})

        results: list[EvidenceSearchResult] = []
        for index, query in enumerate(request.queries):
            results.append(
                self._search_one(
                    query,
                    query_index=index,
                    request=request,
                    candidates=candidates,
                    searchable_docs=searchable_docs,
                    available=available,
                    unavailable=unavailable,
                )
            )
        return results

    def _search_one(
        self,
        query: str,
        *,
        query_index: int,
        request: EvidenceSearchRequest,
        candidates: list[EvidencePassage],
        searchable_docs: list[str],
        available: list[str],
        unavailable: list[str],
    ) -> EvidenceSearchResult:
        query_id = content_hash(
            {
                "query": query,
                "intent": request.intent.value,
                "permitted": sorted(request.permitted_document_ids),
                "filters": request.metadata_filters,
                "retrieval_version": RETRIEVAL_VERSION,
                "n": query_index,
            }
        )

        base = dict(
            query_id=query_id,
            query=query,
            intent=request.intent,
            searched_document_ids=searchable_docs,
            unavailable_document_ids=unavailable,
            retrieval_version=RETRIEVAL_VERSION,
        )

        # No permitted document is present in the corpus at all.
        if not available:
            return EvidenceSearchResult(
                outcome=EvidenceSearchOutcome.SOURCE_NOT_AVAILABLE, **base
            )
        # Permitted documents exist but produced no searchable passage.
        if not candidates:
            return EvidenceSearchResult(
                outcome=EvidenceSearchOutcome.SOURCE_UNSEARCHABLE, **base
            )

        query_tokens = set(_tokenize(query))
        scored: list[tuple[float, EvidencePassage]] = []
        for passage in candidates:
            counts = self._token_counts[passage.passage_id]
            score = float(sum(counts[tok] for tok in query_tokens))
            if score > 0:
                scored.append((score, passage))

        if not scored:
            return EvidenceSearchResult(
                outcome=EvidenceSearchOutcome.NO_MATCH_FOUND, **base
            )

        # Deterministic ordering: score desc, then stable source tie-breakers.
        scored.sort(
            key=lambda s: (
                -s[0],
                s[1].document_id,
                # pdf_page is None for HTML / inline-XBRL filings (never
                # fabricated); fall back to the section label then char offset
                # so ordering stays deterministic without a PDF page number.
                s[1].pdf_page if s[1].pdf_page is not None else -1,
                s[1].section or "",
                s[1].char_offset if s[1].char_offset is not None else -1,
                s[1].passage_id,
            )
        )
        ranked = [
            RankedPassage(rank=i + 1, score=score, passage=passage)
            for i, (score, passage) in enumerate(scored[: request.max_results])
        ]
        return EvidenceSearchResult(
            outcome=EvidenceSearchOutcome.MATCHES_FOUND, ranked_passages=ranked, **base
        )

    @staticmethod
    def _matches_filters(passage: EvidencePassage, filters: dict) -> bool:
        """A passage matches when its metadata contains every requested pair."""
        if not filters:
            return True
        return all(passage.document_metadata.get(k) == v for k, v in filters.items())
