import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.evidence import EntityRecord


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    path: str | None = None
    data: bytes | None = Field(default=None, repr=False, exclude=True)
    parser: Literal["xbrl", "xlsx", "csv", "pdf_table", "pdf_text"]
    tags: list[str] = Field(default_factory=list)
    available_at: datetime
    entity_id: str
    scope: str
    accounting_basis: str
    parse_options: dict = Field(default_factory=dict)
    field_patterns: dict[str, str] = Field(default_factory=dict)
    numeric_fields: list[str] = Field(default_factory=list)
    supersedes: str | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.available_at.tzinfo is None:
            raise ValueError("Source availability must have a timezone.")
        if (self.data is None) == (self.path is None):
            raise ValueError("Exactly one of source bytes or path is required.")
        if "document_id" in self.parse_options or "audit_hook" in self.parse_options:
            raise ValueError("Source options cannot override provenance.")
        return self


class SourcePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of_date: date
    evidence_cutoff_timestamp: datetime
    borrower_entity_id: str
    entities: list[EntityRecord]
    sources: list[SourceInput]

    @model_validator(mode="after")
    def validate_package(self):
        if self.evidence_cutoff_timestamp.tzinfo is None:
            raise ValueError("Evidence cutoff must have a timezone.")
        ids = [e.entity_id for e in self.entities]
        if len(ids) != len(set(ids)) or self.borrower_entity_id not in ids:
            raise ValueError("Unique entities including the borrower are required.")
        if any(s.entity_id not in ids for s in self.sources):
            raise ValueError("Each source must identify a registered entity.")
        return self

    @classmethod
    def load(cls, manifest: Path):
        manifest = manifest.resolve()
        package = cls.model_validate(json.loads(manifest.read_text(encoding="utf-8")))
        for source in package.sources:
            resolved = (manifest.parent / source.path).resolve()
            if not resolved.is_relative_to(manifest.parent):
                raise ValueError("Source paths must stay inside the package directory.")
            source.path = str(resolved)
        return package
