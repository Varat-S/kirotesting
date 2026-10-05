"""Capture native text and scanned pages without promoting OCR to verified facts."""

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from hashlib import sha256
from pathlib import Path

import pdfplumber

from app.core.hashing import content_hash
from app.services.extraction.base import ParserError

NUMERIC_TOKEN = re.compile(r"(?<![\w])\(?[-+]?\d[\d,]*(?:\.\d+)?\)?%?(?![\w])")


def ocr_image(image):
    """Tesseract when installed; native Windows OCR otherwise. No network calls."""
    if shutil.which("tesseract"):
        import pytesseract
        from pytesseract import Output

        raw = pytesseract.image_to_data(
            image, config="--psm 6", output_type=Output.DICT
        )
        words = [
            {
                "text": t,
                "x": raw["left"][i],
                "y": raw["top"][i],
                "width": raw["width"][i],
                "height": raw["height"][i],
                "confidence": float(raw["conf"][i]),
            }
            for i, t in enumerate(raw["text"])
            if t.strip()
        ]
        return {
            "engine": f"tesseract:{pytesseract.get_tesseract_version()}",
            "words": words,
        }
    if os.name != "nt":
        raise ParserError(
            "Scanned PDF needs OCR: install Tesseract and the project's ocr extra."
        )
    with tempfile.TemporaryDirectory(prefix="credit-ocr-") as directory:
        path = Path(directory) / "page.png"
        image.save(path)
        process = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "& ([scriptblock]::Create([IO.File]::ReadAllText($env:CREDIT_OCR_SCRIPT))) -ImagePath $env:CREDIT_OCR_IMAGE",
            ],
            env={
                **os.environ,
                "CREDIT_OCR_SCRIPT": str(Path(__file__).with_name("windows_ocr.ps1")),
                "CREDIT_OCR_IMAGE": str(path),
            },
            capture_output=True,
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if process.returncode:
            raise ParserError(
                "Windows OCR failed; install the English OCR language or Tesseract."
            )
        return json.loads(process.stdout.decode("utf-8-sig"))


def ordered_lines(words):
    """Recover table rows from OCR boxes, preserving tokens and source coordinates."""
    lines = []
    for word in sorted(words, key=lambda w: (w["y"] + w["height"] / 2, w["x"])):
        center = word["y"] + word["height"] / 2
        line = next(
            (
                ln
                for ln in lines
                if abs(ln[0] - center) <= max(4, word["height"] * 0.55)
            ),
            None,
        )
        if line is None:
            line = [center, []]
            lines.append(line)
        line[1].append(word)
    return [
        " ".join(w["text"] for w in sorted(words, key=lambda w: w["x"]))
        for _, words in sorted(lines, key=lambda ln: ln[0])
    ]


def capture_pdf(data, *, document_id, enable_ocr=True, ocr_cache=None):
    pages, passages = [], []
    cached_pages = {}
    if ocr_cache:
        cache_bytes = Path(ocr_cache["path"]).read_bytes()
        if sha256(cache_bytes).hexdigest() != ocr_cache["sha256"]:
            raise ParserError("OCR capture file checksum mismatch.")
        packet = json.loads(cache_bytes)
        if packet["pdf_sha256"] != sha256(data).hexdigest() or packet[
            "payload_sha256"
        ] != content_hash(packet["pages"]):
            raise ParserError(
                "OCR capture does not match this PDF or has been altered."
            )
        cached_pages = {p["page"]: p for p in packet["pages"]}
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > 200:
                raise ParserError("PDF preview supports up to 200 pages.")
            renderer = None
            for number, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                method, engine, words, error = "native", "pdfplumber", [], None
                native_chars = len(page.chars)
                native_inventory = Counter(
                    c
                    for glyph in page.chars
                    for c in glyph.get("text", "")
                    if not c.isspace()
                )
                if not text.strip() and (
                    page.images or page.curves or page.rects or page.lines
                ):
                    method = "ocr"
                    try:
                        if not enable_ocr:
                            raise ParserError("OCR disabled for image-only page.")
                        if number in cached_pages:
                            cached = cached_pages[number]
                            engine, words = cached["engine"], cached["words"]
                            text = cached["text"]
                        else:
                            import pypdfium2

                            if renderer is None:
                                renderer = pypdfium2.PdfDocument(data)
                            rendered_page = renderer[number - 1]
                            bitmap = rendered_page.render(scale=3.5)
                            try:
                                image = bitmap.to_pil()
                                image.thumbnail((9000, 9000))
                                output = ocr_image(image)
                                engine, words = output["engine"], output["words"]
                                text = "\n".join(ordered_lines(words))
                            finally:
                                bitmap.close()
                                rendered_page.close()
                    except (
                        ParserError,
                        subprocess.TimeoutExpired,
                        ImportError,
                        ValueError,
                    ) as exc:
                        error = str(exc)
                elif not text.strip():
                    method = "blank"
                tokens = [
                    {"raw": m.group(), "offset": m.start()}
                    for m in NUMERIC_TOKEN.finditer(text)
                ]
                item = {
                    "page": number,
                    "method": method,
                    "engine": engine,
                    "text": text,
                    "text_sha256": content_hash(text),
                    "characters": len(text),
                    "native_glyphs": native_chars,
                    "numeric_tokens": tokens,
                    "words": words,
                    "error": error,
                    "status": "unverified",
                }
                item["ocr_cache_reused"] = number in cached_pages and method == "ocr"
                item["render_scale"] = 3.5 if method == "ocr" else None
                item["native_nonspace_glyphs"] = sum(native_inventory.values())
                item["retained_native_glyphs"] = sum(
                    (
                        native_inventory & Counter(c for c in text if not c.isspace())
                    ).values()
                )
                item["numeric_rows"] = [
                    {
                        "raw_text": line,
                        "line": index,
                        "tokens": [m.group() for m in NUMERIC_TOKEN.finditer(line)],
                        "status": "unverified",
                        "meaning": "Numeric line candidate; column/period semantics require review.",
                    }
                    for index, line in enumerate(text.splitlines(), 1)
                    if NUMERIC_TOKEN.search(line)
                ]
                pages.append(item)
                for start in range(0, len(text), 2000):
                    passage = {
                        "document_id": document_id,
                        "page": number,
                        "section": f"Page {number}",
                        "heading": f"PDF page {number}",
                        "text": text[start : start + 2000],
                        "location": start,
                        "status": "unverified",
                        "evidence_kind": "candidate_passage",
                        "candidate_topics": [],
                        "extraction_method": method,
                    }
                    passage["evidence_id"] = content_hash(passage)
                    passages.append(passage)
            if renderer is not None:
                renderer.close()
    except ParserError:
        raise
    except Exception as exc:
        raise ParserError(f"Unable to capture PDF: {type(exc).__name__}") from exc
    coverage = {
        "total_pages": len(pages),
        "readable_pages": sum(bool(p["text"].strip()) for p in pages),
        "native_pages": sum(p["method"] == "native" for p in pages),
        "ocr_pages": sum(p["method"] == "ocr" and not p["error"] for p in pages),
        "failed_pages": [p["page"] for p in pages if p["error"]],
        "blank_pages": [p["page"] for p in pages if p["method"] == "blank"],
        "characters": sum(p["characters"] for p in pages),
        "numeric_tokens": sum(len(p["numeric_tokens"]) for p in pages),
        "meaning": "Page/text/token capture measures coverage, not semantic mapping or OCR accuracy. OCR remains unverified.",
    }
    native_total = sum(p["native_nonspace_glyphs"] for p in pages)
    coverage["native_nonspace_glyphs"] = native_total
    coverage["retained_native_glyphs"] = sum(p["retained_native_glyphs"] for p in pages)
    coverage["native_glyph_retention_fraction"] = (
        coverage["retained_native_glyphs"] / native_total if native_total else None
    )
    return {
        "document_id": document_id,
        "pages": pages,
        "passages": passages,
        "coverage": coverage,
    }
