"""Every SEC file, including metadata/exhibits/proxy, passes normal admission."""

from app.core.hashing import canonical_json
from app.models.orm import Document
from app.services.audit.log import EventType
from app.services.ingestion.service import TemporalLeakageError


def admit_sec_bundle(bundle, *, entity_id, case_id, ingestion, session, audit):
    admitted, documents, rejected = [], [], []
    for file in sorted(bundle.files, key=lambda f: (f.filename, f.accession)):
        metadata = file.metadata()
        did = file.evidence_id(case_id)
        try:
            if session.get(Document, did) is None:
                ingestion.ingest_document(
                    case_id,
                    data=file.content,
                    filename=file.filename,
                    entity_id=entity_id,
                    source=canonical_json(metadata),
                    document_type=file.role,
                    available_at=file.available_at,
                    retrieved_at=file.retrieved_at,
                    document_id=did,
                )
        except TemporalLeakageError as exc:
            item = {**metadata, "document_id": did, "reason": str(exc)}
            rejected.append(item)
            audit.record(
                EventType.EVIDENCE_REJECTED,
                case_id=case_id,
                after=item,
                linked_objects=[did],
            )
            continue
        admitted.append(file.model_copy(update={"document_id": did, "admitted": True}))
        documents.append(
            {
                **file.evidence_metadata(),
                "document_id": did,
                "entity_id": entity_id,
                "parser": "inline_xbrl"
                if file.role == "primary_inline_xbrl"
                else "sec_companion",
                "tags": ["sec", "financial_statements"]
                if file.role == "primary_inline_xbrl"
                else ["sec", file.role],
            }
        )
        audit.record(
            EventType.EVIDENCE_ADMITTED,
            case_id=case_id,
            after={**metadata, "document_id": did},
            linked_objects=[did],
        )
    return bundle.model_copy(update={"files": admitted}), documents, rejected
