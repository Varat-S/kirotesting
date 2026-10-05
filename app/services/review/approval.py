"""Bind approval to the draft contents, excluding review/audit bookkeeping."""

from app.core.hashing import content_hash
from app.schemas.snapshots import FinalCaseSnapshot


def approval_hash(snapshot: FinalCaseSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    payload.pop("human_reviews")
    payload.pop("audit_metadata")
    payload["finalized"] = False
    payload["recommendation"] = {**payload["recommendation"], "status": "draft"}
    return content_hash(payload)
