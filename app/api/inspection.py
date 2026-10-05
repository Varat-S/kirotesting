"""Local upload -> deterministic evidence review; no LLM execution route."""

import io
import json
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config import get_settings
from app.core.config_registry import ConfigRegistry
from app.core.hashing import canonical_json, content_hash
from app.models.orm import (
    Case,
    Document,
    Escalation,
    EvidencePreview,
    MetricDefinition,
    Snapshot,
)
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.extraction.base import ParserError
from app.services.extraction.sec.xml import MAX_FILE_BYTES
from app.services.llm.providers import ProviderNotConfiguredError, RealProviderBackend
from app.services.pipeline.artifacts import ArtifactIntegrityError, verified_artifacts
from app.services.pipeline.default_case import (
    DEFAULT_CASE_ID,
    default_package,
    prepare_default_case,
)
from app.services.pipeline.inspection_views import (
    metric_review,
    output_manifest,
    stage_outputs,
)
from app.services.pipeline.review import continue_preview, reviewed_preview
from app.services.pipeline.runner import CreditMemoPipeline, offline_backend
from app.services.pipeline.uploads import (
    MAX_BUNDLE_BYTES,
    UploadOptions,
    upload_package,
)
from app.services.review.finalization import FinalSnapshotAssembler
from app.services.review.workflow import HumanReviewWorkflow, SignOffRequiredError

router = APIRouter(prefix="/inspect", tags=["upload and evidence preview"])
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True
)
_fixture = Path(__file__).resolve().parents[2] / "examples/sec/nvda-2026"


def timestamp(value, offset):
    if not value:
        return None
    if offset not in {"+08:00", "+00:00"}:
        raise ValueError("Choose Singapore or UTC for the entered times.")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        result if result.tzinfo is not None else datetime.fromisoformat(value + offset)
    )


def upload_page(request, *, values=None, error=None, status=200):
    defaults = {
        "case_id": "UPLOAD_" + uuid.uuid4().hex[:8],
        "source_type": "auto",
        "timezone_offset": "+08:00",
        "cutoff": "",
        "currency": "USD",
        "scope": "consolidated",
        "accounting_basis": "GAAP",
        "form": "10-K",
    }
    defaults.update(values or {})
    return HTMLResponse(
        _env.get_template("upload.html.j2").render(
            request=request, values=defaults, error=error
        ),
        status_code=status,
    )


@router.get("", response_class=HTMLResponse)
def upload_form(request: Request):
    return upload_page(request)


@router.get("/example/nvda.zip")
def example_bundle():
    if not (_fixture / "bundle.json").is_file():
        raise HTTPException(
            404, "The bundled NVIDIA example is unavailable in this installation."
        )
    manifest = json.loads((_fixture / "bundle.json").read_text(encoding="utf-8"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(_fixture / "bundle.json", "bundle.json")
        for entry in manifest["files"]:
            archive.write(_fixture / entry["path"], entry["path"])
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="nvda-fy2026-bundle.zip"'
        },
    )


@router.post("/default", response_class=HTMLResponse)
def default_preview(
    request: Request, rerun: str = Form(""), session: Session = Depends(get_session)
):
    settings = get_settings()
    case_id = DEFAULT_CASE_ID + ("_" + uuid.uuid4().hex[:8] if rerun else "")
    try:
        row = prepare_default_case(
            session,
            data_root=settings.data_dir,
            output_root=settings.output_dir,
            case_id=case_id,
        )
    except (ValueError, ParserError, OSError) as exc:
        return upload_page(request, error=str(exc), status=422)
    return RedirectResponse(
        f"/inspect/{row.case_id}/{row.evidence_version}", status_code=303
    )


@router.get("/default/files/{filename}")
def default_source_file(filename: str):
    sources = {source.filename: source for source in default_package().sources}
    if filename not in sources:
        raise HTTPException(404, "Default source file not found.")
    return FileResponse(
        sources[filename].path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
    )


@router.post("", response_class=HTMLResponse)
def upload_and_prepare(
    request: Request,
    files: list[UploadFile] = File(...),
    case_id: str = Form(...),
    cutoff: str = Form(...),
    source_type: str = Form("auto"),
    timezone_offset: str = Form("+08:00"),
    as_of_date: str = Form(""),
    legal_name: str = Form(""),
    entity_id: str = Form(""),
    available_at: str = Form(""),
    scale: str = Form(""),
    currency: str = Form("USD"),
    scope: str = Form("consolidated"),
    accounting_basis: str = Form("GAAP"),
    sheet_name: str = Form(""),
    fiscal_year_end_month: str = Form(""),
    cik: str = Form(""),
    accession: str = Form(""),
    filing_date: str = Form(""),
    accepted_at: str = Form(""),
    form: str = Form("10-K"),
    session: Session = Depends(get_session),
):
    values = {key: value for key, value in locals().items() if isinstance(value, str)}
    try:
        if session.get(Case, case_id) is not None:
            return upload_page(
                request,
                values=values,
                error="That case ID already exists. Choose a new case ID for this upload.",
                status=409,
            )
        if len(files) > 64:
            raise ValueError("Choose up to 64 files.")
        uploaded = []
        total = 0
        for file in files:
            data = file.file.read(MAX_FILE_BYTES + 1)
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_BUNDLE_BYTES:
                return upload_page(
                    request,
                    values=values,
                    error="Uploads are limited to 32 MiB per file and 128 MiB total.",
                    status=413,
                )
            uploaded.append((file.filename or "", data))
        options = UploadOptions(
            case_id=case_id,
            cutoff=timestamp(cutoff, timezone_offset),
            as_of_date=as_of_date or None,
            parser=source_type,
            entity_id=entity_id.strip() or None,
            legal_name=legal_name.strip() or None,
            available_at=timestamp(available_at, timezone_offset),
            scale=scale or None,
            currency=currency,
            scope=scope,
            accounting_basis=accounting_basis,
            sheet_name=sheet_name or None,
            cik=cik or None,
            accession=accession or None,
            filing_date=filing_date or None,
            accepted_at=timestamp(accepted_at, timezone_offset),
            form=form,
        )
        if fiscal_year_end_month:
            options = options.model_copy(
                update={"fiscal_year_end_month": int(fiscal_year_end_month)}
            )
            options = UploadOptions.model_validate(options.model_dump())
        package = upload_package(uploaded, options)
        settings = get_settings()
        with session.begin_nested():
            result = CreditMemoPipeline(
                session, data_root=settings.data_dir, output_root=settings.output_dir
            ).prepare_case(case_id, package=package)
            payload = json.loads(canonical_json(result.as_payload()))
            session.add(
                EvidencePreview(
                    case_id=case_id,
                    evidence_version=payload["evidence_version"],
                    payload=payload,
                )
            )
            session.flush()
    except ValidationError as exc:
        return upload_page(
            request,
            values=values,
            error="; ".join(item["msg"] for item in exc.errors(include_input=False)),
            status=422,
        )
    except (ValueError, ParserError) as exc:
        return upload_page(request, values=values, error=str(exc), status=422)
    return RedirectResponse(
        f"/inspect/{case_id}/{payload['evidence_version']}", status_code=303
    )


def saved_preview(session, case_id, version):
    row = session.get(EvidencePreview, (case_id, version))
    if row is None:
        raise HTTPException(404, "Evidence preview not found.")
    try:
        artifacts = {r.name: r for r in verified_artifacts(session, case_id, version)}
        for expected in row.payload.get("recorded_stages", []):
            actual = artifacts.get(expected["name"])
            if actual is None or actual.chain_hash != expected["chain_hash"]:
                raise ArtifactIntegrityError(
                    "Recorded processing output is missing or differs from its saved reference."
                )
        if "full_preview" in artifacts and artifacts[
            "full_preview"
        ].sha256 != content_hash(row.payload):
            raise ArtifactIntegrityError(
                "Saved preview differs from its recorded processing output."
            )
    except ArtifactIntegrityError as exc:
        raise HTTPException(409, str(exc)) from exc
    return row.payload


def recorded_manifest(session, preview):
    result = output_manifest(preview)
    rows = verified_artifacts(session, preview["case_id"], preview["evidence_version"])
    if rows:
        base = f"/inspect/{preview['case_id']}/{preview['evidence_version']}"
        result.update(
            schema_version="processing-artifacts-1.0",
            origin="Recorded during processing; payload hashes and the artifact chain are verified on read. SHA-256 detects changes, not financial correctness or approval.",
        )
        result["artifacts"] = [
            {
                "name": r.name,
                "label": r.name.replace("_", " "),
                "sha256": r.sha256,
                "chain_hash": r.chain_hash,
                "previous_hash": r.previous_hash,
                "ordinal": r.ordinal,
                "recorded_at": r.created_at.isoformat(),
                "size_bytes": len(r.payload_json.encode("utf-8")),
                "url": f"{base}/artifacts/{r.name}.json",
            }
            for r in rows
        ]
    return result


@router.get("/{case_id}/{version}", response_class=HTMLResponse)
def evidence_preview(
    request: Request,
    case_id: str,
    version: int,
    session: Session = Depends(get_session),
):
    preview = saved_preview(session, case_id, version)
    keys = {
        (metric["metric_definition_id"], metric["metric_definition_version"])
        for metric in preview["metrics"].values()
    }
    definitions = {
        (row.metric_definition_id, row.version): row.components
        for row in session.scalars(
            select(MetricDefinition).where(
                MetricDefinition.metric_definition_id.in_({key[0] for key in keys})
            )
        )
        if (row.metric_definition_id, row.version) in keys
    }
    return HTMLResponse(
        _env.get_template("inspection.html.j2").render(
            request=request,
            preview=preview,
            evidence=preview["evidence"],
            metric_review=metric_review(preview, definitions),
            manifest=recorded_manifest(session, preview),
            reviews=HumanReviewWorkflow(session, case_id=case_id).reviews_for_case(),
            live_escalations=EscalationEngine(
                session, case_id=case_id
            ).escalations_for_case(),
            mapping_names=sorted(
                {
                    r["canonical_name"]
                    for r in ConfigRegistry(session)
                    .get(
                        "financial_mappings",
                        preview["evidence"]["config_versions"]["financial_mappings"],
                    )
                    .content["rules"]
                }
            ),
            provider=get_settings().llm_provider,
            provider_model=get_settings().llm_model,
            reference_checks=next(
                (
                    json.loads(a.payload_json)
                    for a in verified_artifacts(session, case_id, version)
                    if a.name == "reference_checks"
                ),
                None,
            ),
            drafts=list(
                session.scalars(
                    select(Snapshot)
                    .where(
                        Snapshot.case_id == case_id,
                        Snapshot.snapshot_type == "final_case",
                    )
                    .order_by(Snapshot.snapshot_version.desc())
                )
            ),
        )
    )


def mutation_check(
    request, session, case_id, *, reviewer=None, reason=None, confirmed=None
):
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Submit review actions from this local interface.")
    if reviewer is not None and (
        not reviewer.strip() or not reason or not reason.strip() or confirmed != "yes"
    ):
        raise HTTPException(
            422, "Enter your reviewer name, reason, and explicit confirmation."
        )
    if session.scalars(
        select(Snapshot).where(
            Snapshot.case_id == case_id, Snapshot.finalized.is_(True)
        )
    ).first():
        raise HTTPException(
            409, "This case is finalized. Create a new case for further changes."
        )


def pipeline_for(session, backend=None):
    settings = get_settings()
    return CreditMemoPipeline(
        session,
        data_root=settings.data_dir,
        output_root=settings.output_dir,
        backend=backend,
    )


@router.post("/{case_id}/{version}/review")
def review_evidence(
    request: Request,
    case_id: str,
    version: int,
    action: str = Form(...),
    fact_id: str = Form(...),
    reviewer: str = Form(...),
    reason: str = Form(...),
    confirmed: str = Form(""),
    canonical_name: str = Form(""),
    field: str = Form(""),
    session: Session = Depends(get_session),
):
    mutation_check(
        request, session, case_id, reviewer=reviewer, reason=reason, confirmed=confirmed
    )
    try:
        with session.begin_nested():
            row = reviewed_preview(
                pipeline_for(session),
                case_id,
                version,
                action=action,
                fact_id=fact_id,
                reviewer=reviewer,
                reason=reason,
                canonical_name=canonical_name or None,
                field=field or None,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(
        f"/inspect/{case_id}/{row.evidence_version}", status_code=303
    )


@router.post("/{case_id}/{version}/resolve")
def resolve_issue(
    request: Request,
    case_id: str,
    version: int,
    escalation_id: str = Form(...),
    reviewer: str = Form(...),
    reason: str = Form(...),
    confirmed: str = Form(""),
    session: Session = Depends(get_session),
):
    saved_preview(session, case_id, version)
    mutation_check(
        request, session, case_id, reviewer=reviewer, reason=reason, confirmed=confirmed
    )
    row = session.get(Escalation, escalation_id)
    if not row or row.case_id != case_id:
        raise HTTPException(404, "Choose an escalation from this case.")
    try:
        with session.begin_nested():
            audit = AuditLog(session)
            review = HumanReviewWorkflow(
                session, audit=audit, case_id=case_id
            ).record_action(
                "approve_policy_exception",
                reviewer=reviewer,
                target_type="escalation",
                target_id=escalation_id,
                prior_value={"status": row.status, "reason": row.reason},
                new_value={"status": "resolved", "reason": reason},
                reason=reason,
                linked_evidence=row.evidence_refs,
                signed_off=True,
            )
            EscalationEngine(session, audit=audit, case_id=case_id).resolve(
                escalation_id,
                resolved_by=reviewer,
                resolution={
                    "review_id": review.review_id,
                    "reason": reason,
                    "evidence_version": version,
                },
                reason=reason,
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f"/inspect/{case_id}/{version}#issues", status_code=303)


@router.post("/{case_id}/{version}/continue")
def continue_to_llm(
    request: Request,
    case_id: str,
    version: int,
    mode: str = Form("offline"),
    confirmed: str = Form(""),
    rerun: str = Form(""),
    session: Session = Depends(get_session),
):
    saved_preview(session, case_id, version)
    mutation_check(request, session, case_id)
    if confirmed != "yes" or mode not in {"offline", "live"}:
        raise HTTPException(422, "Choose a mode and confirm draft generation.")
    backend = (
        offline_backend() if mode == "offline" else RealProviderBackend(get_settings())
    )
    try:
        if mode == "live":
            backend.check_configuration()
        with session.begin_nested():
            draft = continue_preview(
                pipeline_for(session, backend), case_id, version, rerun=rerun == "yes"
            )
    except (ValueError, ProviderNotConfiguredError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(
        f"/inspect/{case_id}/drafts/{draft.snapshot_version}", status_code=303
    )


def draft_row(session, case_id, version):
    row = session.scalars(
        select(Snapshot).where(
            Snapshot.case_id == case_id,
            Snapshot.snapshot_type == "final_case",
            Snapshot.snapshot_version == version,
        )
    ).first()
    if not row:
        raise HTTPException(404, "Draft not found.")
    return row


def require_latest_draft(session, row):
    latest = session.scalar(
        select(func.max(Snapshot.snapshot_version)).where(
            Snapshot.case_id == row.case_id, Snapshot.snapshot_type == "final_case"
        )
    )
    if row.snapshot_version != latest:
        raise HTTPException(
            409, "This draft has been superseded; review the latest draft."
        )


@router.get("/{case_id}/drafts/{version}", response_class=HTMLResponse)
def review_draft(
    request: Request,
    case_id: str,
    version: int,
    session: Session = Depends(get_session),
):
    row = draft_row(session, case_id, version)
    return HTMLResponse(
        _env.get_template("draft_review.html.j2").render(
            request=request,
            draft=row,
            reviews=HumanReviewWorkflow(session, case_id=case_id).reviews_for_case(),
            exceptions=EscalationEngine(session, case_id=case_id).open_escalations(),
        )
    )


@router.post("/{case_id}/drafts/{version}/approve")
def approve_draft(
    request: Request,
    case_id: str,
    version: int,
    reviewer: str = Form(...),
    reason: str = Form(...),
    confirmed: str = Form(""),
    session: Session = Depends(get_session),
):
    mutation_check(
        request, session, case_id, reviewer=reviewer, reason=reason, confirmed=confirmed
    )
    row = draft_row(session, case_id, version)
    snapshot = FinalCaseSnapshot.model_validate(row.payload)
    require_latest_draft(session, row)
    latest = session.scalar(
        select(func.max(EvidencePreview.evidence_version)).where(
            EvidencePreview.case_id == case_id
        )
    )
    if latest != snapshot.evidence_snapshot_ref.snapshot_version:
        raise HTTPException(
            409,
            "This draft uses superseded evidence; generate a draft from the latest version.",
        )
    HumanReviewWorkflow(
        session, audit=AuditLog(session), case_id=case_id
    ).sign_off_recommendation(reviewer=reviewer, snapshot=snapshot, reason=reason)
    return RedirectResponse(f"/inspect/{case_id}/drafts/{version}", status_code=303)


@router.post("/{case_id}/drafts/{version}/finalize")
def finalize_draft(
    request: Request,
    case_id: str,
    version: int,
    reviewer: str = Form(...),
    reason: str = Form(...),
    confirmed: str = Form(""),
    session: Session = Depends(get_session),
):
    mutation_check(
        request, session, case_id, reviewer=reviewer, reason=reason, confirmed=confirmed
    )
    row = draft_row(session, case_id, version)
    snapshot = FinalCaseSnapshot.model_validate(row.payload)
    require_latest_draft(session, row)
    latest = session.scalar(
        select(func.max(EvidencePreview.evidence_version)).where(
            EvidencePreview.case_id == case_id
        )
    )
    if latest != snapshot.evidence_snapshot_ref.snapshot_version:
        raise HTTPException(409, "This draft uses superseded evidence.")
    try:
        with session.begin_nested():
            FinalSnapshotAssembler(
                session, ConfigRegistry(session), audit=AuditLog(session)
            ).finalize(snapshot, signed_off_by=reviewer, reason=reason)
    except SignOffRequiredError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/inspect/{case_id}/drafts/{version}", status_code=303)


@router.get("/{case_id}/{version}/payload.json")
def preview_payload(
    case_id: str, version: int, session: Session = Depends(get_session)
):
    return saved_preview(session, case_id, version)


@router.get("/{case_id}/{version}/sources/{document_id}")
def source_document(
    case_id: str,
    version: int,
    document_id: str,
    session: Session = Depends(get_session),
):
    from hashlib import sha256

    preview = saved_preview(session, case_id, version)
    admitted = next(
        (
            d
            for d in preview["evidence"]["documents"]
            if d["document_id"] == document_id
        ),
        None,
    )
    row = session.get(Document, document_id)
    if not admitted or not row or row.case_id != case_id or not row.storage_path:
        raise HTTPException(404, "Admitted source not found.")
    path = Path(row.storage_path).resolve()
    if not path.is_relative_to((Path(get_settings().data_dir) / "raw").resolve()):
        raise HTTPException(
            409, "Source path is outside this application's raw-file store."
        )
    data = path.read_bytes()
    if sha256(data).hexdigest() != admitted["sha256"]:
        raise HTTPException(409, "Original source checksum mismatch.")
    pdf = data.startswith(b"%PDF-")
    return Response(
        data,
        media_type="application/pdf" if pdf else "application/octet-stream",
        headers={
            "Content-Disposition": "inline" if pdf else "attachment",
            "X-Content-SHA256": admitted["sha256"],
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{case_id}/{version}/llm-input.json")
def llm_input(case_id: str, version: int, session: Session = Depends(get_session)):
    return saved_preview(session, case_id, version)["next_llm_input"]


@router.get("/{case_id}/{version}/artifacts.json")
def artifact_manifest(
    case_id: str, version: int, session: Session = Depends(get_session)
):
    return recorded_manifest(session, saved_preview(session, case_id, version))


@router.get("/{case_id}/{version}/artifacts/{name}.json")
def artifact_output(
    case_id: str, version: int, name: str, session: Session = Depends(get_session)
):
    preview = saved_preview(session, case_id, version)
    rows = {r.name: r for r in verified_artifacts(session, case_id, version)}
    if name in rows:
        row = rows[name]
        return Response(
            row.payload_json.encode("utf-8"),
            media_type="application/json",
            headers={
                "ETag": f'"{row.sha256}"',
                "X-Content-SHA256": row.sha256,
                "Content-Disposition": f'attachment; filename="{name}.json"',
            },
        )
    outputs = stage_outputs(preview)
    if name not in outputs:
        raise HTTPException(404, "Saved output not found.")
    from app.core.hashing import content_hash

    value = outputs[name][1]
    digest = content_hash(value)
    return Response(
        canonical_json(value).encode("utf-8"),
        media_type="application/json",
        headers={
            "ETag": f'"{digest}"',
            "X-Content-SHA256": digest,
            "Content-Disposition": f'attachment; filename="{name}.json"',
        },
    )
