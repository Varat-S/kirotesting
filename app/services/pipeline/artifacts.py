"""Record and verify stage outputs at processing time, without rewriting history."""

import json
from hashlib import sha256

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, content_hash
from app.models.orm import StageArtifact


class ArtifactIntegrityError(ValueError):
    pass


@event.listens_for(Session, "before_flush")
def immutable_artifacts(session, *_):
    for row in session.dirty | session.deleted:
        if isinstance(row, StageArtifact) and (
            row in session.deleted or session.is_modified(row)
        ):
            raise ArtifactIntegrityError(
                "Recorded stage outputs cannot be edited or deleted."
            )


def chain_value(name, ordinal, digest, previous):
    return content_hash(
        {"name": name, "ordinal": ordinal, "sha256": digest, "previous_hash": previous}
    )


class StageRecorder:
    def __init__(self, session, case_id, version):
        self.session, self.case_id, self.version = session, case_id, version
        self.rows = verified_artifacts(session, case_id, version)

    def record(self, name, payload):
        encoded = canonical_json(payload)
        digest = sha256(encoded.encode("utf-8")).hexdigest()
        existing = next((r for r in self.rows if r.name == name), None)
        if existing:
            if existing.sha256 != digest:
                raise ArtifactIntegrityError(
                    "A processing stage was recorded with different content."
                )
            return existing
        ordinal = len(self.rows) + 1
        previous = self.rows[-1].chain_hash if self.rows else None
        row = StageArtifact(
            case_id=self.case_id,
            evidence_version=self.version,
            name=name,
            ordinal=ordinal,
            payload_json=encoded,
            sha256=digest,
            previous_hash=previous,
            chain_hash=chain_value(name, ordinal, digest, previous),
        )
        self.session.add(row)
        self.session.flush()
        self.rows.append(row)
        return row

    def references(self):
        return [
            {
                "name": r.name,
                "sha256": r.sha256,
                "chain_hash": r.chain_hash,
                "previous_hash": r.previous_hash,
                "ordinal": r.ordinal,
            }
            for r in self.rows
        ]


def verified_artifacts(session, case_id, version):
    rows = list(
        session.scalars(
            select(StageArtifact)
            .where(
                StageArtifact.case_id == case_id,
                StageArtifact.evidence_version == version,
            )
            .order_by(StageArtifact.ordinal)
        )
    )
    previous = None
    for ordinal, row in enumerate(rows, 1):
        digest = sha256(row.payload_json.encode("utf-8")).hexdigest()
        if (
            digest != row.sha256
            or row.ordinal != ordinal
            or row.previous_hash != previous
            or row.chain_hash != chain_value(row.name, ordinal, digest, previous)
        ):
            raise ArtifactIntegrityError(
                f"Recorded artifact integrity check failed: {row.name}"
            )
        json.loads(row.payload_json)
        previous = row.chain_hash
    return rows
