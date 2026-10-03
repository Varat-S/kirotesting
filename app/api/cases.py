"""Case creation and immutable document upload routes (Requirement 1, 25).

Thin HTTP layer over :class:`~app.services.ingestion.IngestionService`. All core
logic (hashing, versioning, supersedes, cutoff enforcement, filename
sanitization) lives in the service and is unit-testable without HTTP.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_raw_store, get_session
from app.services.audit.log import AuditLog
from app.services.ingestion import (
    CaseAlreadyExistsError,
    IngestionService,
    RawFileStore,
    TemporalLeakageError,
    UnsafeFilenameError,
    UnsupportedFileTypeError,
)

router = APIRouter(prefix="/cases", tags=["cases"])


class CreateCaseRequest(BaseModel):
    """Payload to create a case (Req 1.1, 1.2)."""

    case_id: str
    as_of_date: date
    evidence_cutoff_timestamp: datetime


class CaseResponse(BaseModel):
    case_id: str
    as_of_date: date
    evidence_cutoff_timestamp: datetime
    status: str


class DocumentResponse(BaseModel):
    document_id: str
    case_id: str
    filename: str | None
    sha256: str | None
    version: int
    supersedes: str | None
    newly_stored_bytes: bool


def _service(session: Session, store: RawFileStore) -> IngestionService:
    return IngestionService(session, store, audit=AuditLog(session))


@router.post("", response_model=CaseResponse, status_code=201)
def create_case(
    body: CreateCaseRequest,
    session: Session = Depends(get_session),
    store: RawFileStore = Depends(get_raw_store),
) -> CaseResponse:
    service = _service(session, store)
    try:
        case = service.create_case(
            body.case_id,
            as_of_date=body.as_of_date,
            evidence_cutoff_timestamp=body.evidence_cutoff_timestamp,
        )
    except CaseAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CaseResponse(
        case_id=case.case_id,
        as_of_date=case.as_of_date,
        evidence_cutoff_timestamp=case.evidence_cutoff_timestamp,
        status=case.status,
    )


@router.post(
    "/{case_id}/documents", response_model=DocumentResponse, status_code=201
)
async def upload_document(
    case_id: str,
    file: UploadFile = File(...),
    source: str | None = Form(default=None),
    document_type: str | None = Form(default=None),
    source_authority: str | None = Form(default=None),
    available_at: datetime | None = Form(default=None),
    supersedes: str | None = Form(default=None),
    session: Session = Depends(get_session),
    store: RawFileStore = Depends(get_raw_store),
) -> DocumentResponse:
    service = _service(session, store)
    data = await file.read()
    try:
        result = service.ingest_document(
            case_id,
            data=data,
            filename=file.filename or "",
            source=source,
            document_type=document_type,
            source_authority=source_authority,
            available_at=available_at,
            supersedes=supersedes,
        )
    except (UnsafeFilenameError, UnsupportedFileTypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TemporalLeakageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    doc = result.document
    return DocumentResponse(
        document_id=doc.document_id,
        case_id=doc.case_id,
        filename=doc.filename,
        sha256=doc.sha256,
        version=doc.version,
        supersedes=doc.supersedes,
        newly_stored_bytes=result.newly_stored_bytes,
    )
