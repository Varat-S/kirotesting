"""Readable PDF passages, extracted only after the document's admission."""

import io

from app.core.hashing import content_hash
from app.services.extraction.base import ParserError


def pdf_passages(data, *, document_id):
    import pdfplumber

    passages = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > 200:
                raise ParserError(
                    "PDF text preview supports up to 200 pages; upload a smaller report extract."
                )
            for number, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                for start in range(0, len(text), 2000):
                    item = {
                        "document_id": document_id,
                        "page": number,
                        "section": f"Page {number}",
                        "heading": f"PDF page {number}",
                        "text": text[start : start + 2000],
                        "location": start,
                        "status": "unverified",
                        "evidence_kind": "candidate_passage",
                        "candidate_topics": [],
                    }
                    item["evidence_id"] = content_hash(item)
                    passages.append(item)
    except ParserError:
        raise
    except Exception as exc:
        raise ParserError(f"Unable to extract PDF text: {exc}") from exc
    return passages
