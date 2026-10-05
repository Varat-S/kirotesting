"""Build controlled source packages from bounded browser uploads."""

import io
import json
import stat
import zipfile
from datetime import date, datetime
from hashlib import sha256
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.evidence import EntityRecord
from app.services.acquisition.models import SecFiling
from app.services.acquisition.sec_edgar import file_role
from app.services.extraction.sec.bundle import SecFilingBundle, SecFilingFile
from app.services.extraction.sec.xml import MAX_FILE_BYTES
from app.services.ingestion.storage import sanitize_filename
from .package import SourceInput, SourcePackage
from .sec import sec_source_package

MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def archive_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Unsafe path in uploaded ZIP.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(
        p in {"", "..", "."} or ":" in p for p in value.rstrip("/").split("/")
    ):
        raise ValueError("Unsafe path in uploaded ZIP.")
    return path


def inspect_zip(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded file is not a readable ZIP archive.") from exc
    try:
        items = archive.infolist()
        if len(items) > 512 or sum(i.file_size for i in items) > MAX_BUNDLE_BYTES:
            raise ValueError(
                "Archive exceeds the 512-entry / 128 MiB expanded-size limit."
            )
        paths = {}
        for item in items:
            name = archive_path(item.filename).as_posix()
            if (
                name in paths
                or stat.S_ISLNK(item.external_attr >> 16)
                or item.flag_bits & 1
            ):
                raise ValueError(
                    "Duplicate, symlink or encrypted archive entries are unsupported."
                )
            if item.file_size > MAX_FILE_BYTES:
                raise ValueError("Archive contains a file larger than 32 MiB.")
            paths[name] = item
        return archive, paths
    except Exception:
        archive.close()
        raise


def bundle_from_zip(data):
    archive, paths = inspect_zip(data)
    with archive:
        manifests = [
            p
            for p, entry in paths.items()
            if not entry.is_dir() and PurePosixPath(p).name == "bundle.json"
        ]
        if len(manifests) != 1:
            raise ValueError(
                "A SEC ZIP must contain exactly one bundle.json. For a standalone filing, choose its primary HTML file and enter the filing metadata."
            )
        manifest = PurePosixPath(manifests[0])
        if paths[manifest.as_posix()].file_size > 1024 * 1024:
            raise ValueError("Bundle manifest exceeds 1 MiB.")
        try:
            payload = json.loads(archive.read(manifest.as_posix()))
            if not isinstance(payload, dict) or not isinstance(
                payload.get("files"), list
            ):
                raise ValueError(
                    "Bundle manifest needs an object containing a files list."
                )
            for entry in payload["files"]:
                if not isinstance(entry, dict):
                    raise ValueError("Each bundle file entry must be an object.")
                relative = archive_path(entry.pop("path"))
                name = (manifest.parent / relative).as_posix()
                if name not in paths or paths[name].is_dir():
                    raise ValueError(
                        "Manifest references a missing file inside its bundle directory."
                    )
                if (
                    entry.pop("admitted", False)
                    or entry.pop("document_id", None)
                    or "content" in entry
                ):
                    raise ValueError(
                        "Uploaded bundles cannot assert prior ingestion admission."
                    )
                expected = entry.pop("sha256", None)
                with archive.open(paths[name]) as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("Archive file exceeds 32 MiB.")
                if expected and sha256(content).hexdigest() != expected:
                    raise ValueError("Bundle file hash mismatch.")
                entry["content"] = content
        except (
            KeyError,
            TypeError,
            RuntimeError,
            NotImplementedError,
            zipfile.BadZipFile,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise ValueError("Unreadable SEC bundle manifest or file content.") from exc
    return SecFilingBundle.model_validate(payload)


class UploadOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    cutoff: datetime
    as_of_date: date | None = None
    parser: str = "auto"
    entity_id: str | None = None
    legal_name: str | None = None
    available_at: datetime | None = None
    scale: str | None = None
    currency: str = "USD"
    scope: str = "consolidated"
    accounting_basis: str = "GAAP"
    sheet_name: str | None = None
    fiscal_year_end_month: int | None = Field(default=None, ge=1, le=12)
    cik: str | None = None
    accession: str | None = None
    filing_date: date | None = None
    accepted_at: datetime | None = None
    form: str = "10-K"

    @model_validator(mode="after")
    def validate_options(self):
        for value in [self.cutoff, self.available_at, self.accepted_at]:
            if value is not None and value.tzinfo is None:
                raise ValueError(
                    "Availability and cutoff timestamps require a timezone."
                )
        if self.scope not in {
            "consolidated",
            "segment",
        } or self.accounting_basis not in {"GAAP", "IFRS"}:
            raise ValueError("Choose a supported scope and accounting basis.")
        if self.scale is not None and self.scale not in {
            "units",
            "thousands",
            "millions",
            "billions",
        }:
            raise ValueError("Choose a supported source amount scale.")
        if (
            len(self.currency) != 3
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise ValueError("Currency must be a three-letter uppercase code.")
        return self


def upload_package(files: list[tuple[str, bytes]], options: UploadOptions):
    if not files or len(files) > 64:
        raise ValueError(
            "Choose one source file or up to 64 SEC primary/sidecar files."
        )
    if (
        any(len(data) > MAX_FILE_BYTES for _, data in files)
        or sum(len(data) for _, data in files) > MAX_BUNDLE_BYTES
    ):
        raise ValueError("Uploads are limited to 32 MiB per file and 128 MiB total.")
    for name, _ in files:
        sanitize_filename(name)
    if len({name for name, _ in files}) != len(files):
        raise ValueError("Upload filenames must be unique.")
    extension = PurePosixPath(files[0][0]).suffix.lower()
    parser = options.parser
    if parser == "auto":
        parsers = {
            ".zip": "sec_bundle",
            ".htm": "sec_inline",
            ".html": "sec_inline",
            ".xhtml": "sec_inline",
            ".csv": "csv",
            ".xlsx": "xlsx",
            ".pdf": "pdf_table",
            ".xml": "xbrl",
            ".xbrl": "xbrl",
        }
        html_files = [
            name
            for name, _ in files
            if PurePosixPath(name).suffix.lower() in {".htm", ".html", ".xhtml"}
        ]
        parser = "sec_inline" if html_files else parsers.get(extension)
    if parser == "sec_bundle":
        if len(files) != 1 or extension != ".zip":
            raise ValueError("Choose one SEC bundle ZIP.")
        source = bundle_from_zip(files[0][1])
        package = sec_source_package(
            source,
            cutoff=options.cutoff,
            entity_id=options.entity_id,
            legal_name=options.legal_name,
            as_of_date=options.as_of_date,
        )
        package.sec_bundles[0].scope = options.scope
        package.sec_bundles[0].accounting_basis = options.accounting_basis
        package.entities[0].expected_consolidation_scope = options.scope
        return package
    if options.as_of_date is None:
        raise ValueError("Enter the report / case as-of date.")
    if parser == "sec_inline":
        if not all([options.cik, options.accession, options.filing_date]):
            raise ValueError(
                "SEC HTML needs its official CIK, accession and filing date. A bundle ZIP supplies these in its manifest."
            )
        primaries = [
            name
            for name, _ in files
            if PurePosixPath(name).suffix.lower() in {".htm", ".html", ".xhtml"}
        ]
        if len(primaries) != 1:
            raise ValueError(
                "Choose one primary SEC HTML plus optional XML/XSD sidecars. Use a bundle ZIP for separately dated proxies/exhibits."
            )
        primary = primaries[0]
        metadata = SecFiling(
            options.cik,
            options.form,
            options.accession,
            options.filing_date,
            options.as_of_date,
            primary,
            options.accepted_at,
        )
        inputs = []
        for name, data in files:
            role = file_role(name, primary)
            if role == "other_exhibit":
                raise ValueError(
                    "Unrecognized sidecar; use a bundle manifest to declare exhibits and their own availability."
                )
            inputs.append(
                SecFilingFile(
                    filename=name,
                    content=data,
                    role=role,
                    accession=options.accession,
                    cik=options.cik,
                    form=options.form,
                    filing_date=options.filing_date,
                    report_date=options.as_of_date,
                    accepted_at=options.accepted_at,
                    available_at=metadata.available_at,
                    availability_granularity=metadata.availability_granularity,
                )
            )
        source = SecFilingBundle(
            cik=options.cik,
            ticker=options.entity_id,
            accession=options.accession,
            form=options.form,
            report_date=options.as_of_date,
            filing_date=options.filing_date,
            primary_document=primary,
            files=inputs,
        )
        package = sec_source_package(
            source,
            cutoff=options.cutoff,
            entity_id=options.entity_id,
            legal_name=options.legal_name,
        )
        package.sec_bundles[0].scope = options.scope
        package.sec_bundles[0].accounting_basis = options.accounting_basis
        package.entities[0].expected_consolidation_scope = options.scope
        return package
    if parser not in {"csv", "xlsx", "pdf_table", "xbrl"} or len(files) != 1:
        raise ValueError(
            "Choose one PDF, CSV, XLSX or standalone XBRL file, or a supported SEC bundle."
        )
    expected_extensions = {
        "csv": {".csv"},
        "xlsx": {".xlsx"},
        "pdf_table": {".pdf"},
        "xbrl": {".xbrl", ".xml"},
    }
    if extension not in expected_extensions[parser]:
        raise ValueError("File extension does not match the selected parser.")
    if options.available_at is None or not options.legal_name:
        raise ValueError(
            "Enter the borrower name and when the source became available."
        )
    if parser in {"csv", "xlsx", "pdf_table"} and options.scale is None:
        raise ValueError("Choose the source amount scale from the statement's header.")
    entity_id = options.entity_id or options.case_id
    parse_options = {}
    if parser in {"csv", "xlsx", "pdf_table"}:
        parse_options.update(scale=options.scale, currency=options.currency)
        if options.fiscal_year_end_month is not None:
            parse_options["fiscal_year_end_month"] = options.fiscal_year_end_month
    if parser == "xlsx":
        archive, _ = inspect_zip(files[0][1])
        archive.close()
        if options.sheet_name:
            parse_options["sheet_name"] = options.sheet_name
    if parser == "pdf_table":
        parse_options["include_text_evidence"] = True
    if parser == "xbrl":
        parse_options["harden_xml"] = True
    entity = EntityRecord(
        entity_id=entity_id,
        legal_name=options.legal_name,
        entity_type="borrower",
        borrower_flag=True,
        expected_consolidation_scope=options.scope,
    )
    return SourcePackage(
        as_of_date=options.as_of_date,
        evidence_cutoff_timestamp=options.cutoff,
        borrower_entity_id=entity_id,
        entities=[entity],
        sources=[
            SourceInput(
                filename=files[0][0],
                data=files[0][1],
                parser=parser,
                tags=["financial_statements"],
                entity_id=entity_id,
                scope=options.scope,
                accounting_basis=options.accounting_basis,
                available_at=options.available_at,
                parse_options=parse_options,
            )
        ],
    )
