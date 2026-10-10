import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.evidence import EntityRecord
from app.services.extraction.sec.bundle import SecFilingBundle


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str
    path: str | None = None
    data: bytes | None = Field(default=None, repr=False, exclude=True)
    parser: Literal["xbrl", "inline_xbrl", "xlsx", "csv", "pdf_table", "pdf_text"]
    tags: list[str] = Field(default_factory=list)
    available_at: datetime
    entity_id: str
    scope: str
    accounting_basis: str
    parse_options: dict = Field(default_factory=dict)
    field_patterns: dict[str, str] = Field(default_factory=dict)
    numeric_fields: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source(self):
        if self.available_at.tzinfo is None:
            raise ValueError("Source availability must have a timezone.")
        if (self.data is None) == (self.path is None):
            raise ValueError("Exactly one of source bytes or path is required.")
        if "document_id" in self.parse_options or "audit_hook" in self.parse_options:
            raise ValueError("Source options cannot override provenance.")
        return self


class SecBundleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: SecFilingBundle
    entity_id: str
    scope: str = "consolidated"
    accounting_basis: str = "GAAP"


class CompanyIdentityInput(BaseModel):
    """Declared company identity used for sector classification.

    ``identity_verification`` is ``sec_verified`` only when the CIK and SIC were
    read from SEC EDGAR for this case; otherwise ``declared_unverified``.
    """

    model_config = ConfigDict(extra="forbid")
    cik: str | None = None
    ticker: str | None = None
    reported_sic: int | None = None
    sic_source: str | None = None
    sic_retrieved_at: date | None = None
    identity_verification: Literal["sec_verified", "declared_unverified"] = (
        "declared_unverified"
    )


class SectorBenchmarkInput(BaseModel):
    """Opt-in sector benchmarking for a case.

    The reference workbook is BENCHMARK REFERENCE DATA: it is never ingested as
    borrower evidence and never enters the CanonicalEvidenceSnapshot.
    """

    model_config = ConfigDict(extra="forbid")
    sector_id: str
    reference_workbook: str
    reference_workbook_sha256: str | None = None
    identity: CompanyIdentityInput = Field(default_factory=CompanyIdentityInput)


class SourcePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of_date: date
    evidence_cutoff_timestamp: datetime
    borrower_entity_id: str
    entities: list[EntityRecord]
    sources: list[SourceInput]
    sec_bundles: list[SecBundleInput] = Field(default_factory=list)
    sector_benchmark: SectorBenchmarkInput | None = None

    @model_validator(mode="after")
    def validate_package(self):
        if self.evidence_cutoff_timestamp.tzinfo is None:
            raise ValueError("Evidence cutoff must have a timezone.")
        ids = [e.entity_id for e in self.entities]
        if len(ids) != len(set(ids)) or self.borrower_entity_id not in ids:
            raise ValueError("Unique entities including the borrower are required.")
        if any(s.entity_id not in ids for s in self.sources):
            raise ValueError("Each source must identify a registered entity.")
        if any(b.entity_id not in ids for b in self.sec_bundles):
            raise ValueError("Each SEC bundle must identify a registered entity.")
        return self

    @classmethod
    def load(cls, manifest: Path):
        manifest = manifest.resolve()
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in raw.get("sec_bundles", []):
            bundle_path = (manifest.parent / entry["bundle"]).resolve()
            if not bundle_path.is_relative_to(manifest.parent):
                raise ValueError(
                    "SEC bundle paths must stay inside the package directory."
                )
            entry["bundle"] = SecFilingBundle.load(bundle_path)
        package = cls.model_validate(raw)
        if package.sector_benchmark is not None:
            workbook = (
                manifest.parent / package.sector_benchmark.reference_workbook
            ).resolve()
            if not workbook.is_relative_to(manifest.parent) or not workbook.is_file():
                raise ValueError(
                    "The reference workbook must be a file inside the package directory."
                )
            package.sector_benchmark.reference_workbook = str(workbook)
        for source in package.sources:
            resolved = (manifest.parent / source.path).resolve()
            if not resolved.is_relative_to(manifest.parent):
                raise ValueError("Source paths must stay inside the package directory.")
            source.path = str(resolved)
            if source.parse_options.get("ocr_cache"):
                cache = (manifest.parent / source.parse_options["ocr_cache"]["path"]).resolve()
                if not cache.is_relative_to(manifest.parent):
                    raise ValueError("OCR capture paths must stay inside the package directory.")
                source.parse_options["ocr_cache"]["path"] = str(cache)
        return package
