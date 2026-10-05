"""Explicit local/downloaded bundle model; directory layout is not parser state."""

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.hashing import content_hash
from app.services.ingestion.storage import sanitize_filename
from app.services.acquisition.models import SecFiling
from .xml import MAX_FILE_BYTES


def validate_sec_filename(filename):
    sanitize_filename(filename)
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}", filename):
        raise ValueError("Invalid SEC filename.")
    stem = filename.split(".", 1)[0].upper()
    if filename.endswith(".") or stem in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        raise ValueError("Reserved SEC filename.")
    return filename


FileRole = Literal[
    "primary_inline_xbrl",
    "schema",
    "label_linkbase",
    "presentation_linkbase",
    "calculation_linkbase",
    "definition_linkbase",
    "proxy",
    "exhibit_21",
    "other_exhibit",
]


class SecFilingFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    role: FileRole
    content: bytes = Field(repr=False, exclude=True)
    available_at: datetime
    availability_granularity: Literal["timestamp", "date", "declared"]
    source_url: str | None = None
    accession: str
    cik: str | None = None
    form: str
    filing_date: date
    report_date: date | None = None
    accepted_at: datetime | None = None
    retrieved_at: datetime | None = None
    document_id: str | None = None
    admitted: bool = False

    @model_validator(mode="after")
    def validate_file(self):
        validate_sec_filename(self.filename)
        if len(self.content) > MAX_FILE_BYTES:
            raise ValueError("SEC file exceeds size limit.")
        for timestamp in [self.available_at, self.accepted_at, self.retrieved_at]:
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("SEC timestamps require a timezone.")
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", self.accession):
            raise ValueError("Invalid SEC accession.")
        if self.cik is not None and not re.fullmatch(r"\d{1,10}", self.cik):
            raise ValueError("Invalid SEC file CIK.")
        minimum = SecFiling(
            "0",
            self.form,
            self.accession,
            self.filing_date,
            self.report_date,
            self.filename,
            self.accepted_at,
        ).available_at
        if self.available_at < minimum:
            raise ValueError(
                "SEC availability cannot precede acceptance or the conservative filing-date boundary."
            )
        if self.availability_granularity == "timestamp" and self.accepted_at is None:
            raise ValueError(
                "Timestamp availability requires an official acceptance timestamp."
            )
        return self

    @property
    def sha256(self):
        return hashlib.sha256(self.content).hexdigest()

    def metadata(self):
        return {
            **self.model_dump(mode="json", exclude={"admitted"}),
            "sha256": self.sha256,
        }

    def evidence_metadata(self):
        """Canonical evidence excludes acquisition timestamps; metadata retains them."""
        return {k: v for k, v in self.metadata().items() if k != "retrieved_at"}

    def evidence_id(self, case_id):
        return content_hash(
            {
                "case_id": case_id,
                "accession": self.accession,
                "filename": self.filename,
                "sha256": self.sha256,
                "available_at": self.available_at.isoformat(),
            }
        )


class SecFilingBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cik: str
    ticker: str | None = None
    accession: str
    form: str
    report_date: date
    filing_date: date
    primary_document: str
    files: list[SecFilingFile]

    @model_validator(mode="after")
    def validate_bundle(self):
        if not re.fullmatch(r"\d{1,10}", self.cik):
            raise ValueError("Invalid CIK.")
        for file in self.files:
            if file.cik is not None and int(file.cik) != int(self.cik):
                raise ValueError("Companion file CIK does not match the bundle.")
            file.cik = self.cik
        names = [f.filename for f in self.files]
        identities = [(f.accession, f.filename) for f in self.files]
        if (
            len(identities) != len(set(identities))
            or self.primary_document not in names
        ):
            raise ValueError("Unique files including the primary filing are required.")
        if sum(len(f.content) for f in self.files) > 128 * 1024 * 1024:
            raise ValueError("SEC bundle exceeds 128 MiB limit.")
        primary = next(
            (
                f
                for f in self.files
                if f.filename == self.primary_document and f.accession == self.accession
            ),
            None,
        )
        if sum(f.role == "primary_inline_xbrl" for f in self.files) != 1:
            raise ValueError(
                "A bundle must declare exactly one primary Inline-XBRL file."
            )
        if primary is None or primary.role != "primary_inline_xbrl":
            raise ValueError("Primary file metadata does not match the bundle.")
        if (primary.form, primary.filing_date, primary.report_date) != (
            self.form,
            self.filing_date,
            self.report_date,
        ):
            raise ValueError("Primary filing dates/form do not match the bundle.")
        return self

    @classmethod
    def load(cls, manifest: Path):
        manifest = manifest.resolve()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in data["files"]:
            path = (manifest.parent / entry.pop("path")).resolve()
            if not path.is_relative_to(manifest.parent) or not path.is_file():
                raise ValueError(
                    "Bundle paths must identify files inside the manifest directory."
                )
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("SEC file exceeds size limit.")
            content = path.read_bytes()
            expected = entry.pop("sha256", None)
            if expected and hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Bundle file hash mismatch.")
            # An uploaded manifest cannot claim prior evidence admission.
            if entry.pop("admitted", False) or entry.pop("document_id", None):
                raise ValueError("Local bundles cannot assert ingestion admission.")
            entry["content"] = content
        return cls.model_validate(data)
