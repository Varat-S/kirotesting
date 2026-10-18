"""Offline-only replay of a recorded M3 proposal through current Validator 7.

This diagnostic does not mutate the historical run and must not be described as
a new live-model result. The saved final accepted agent response and its sealed
evidence-id set are the only analytical inputs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import AgentRunRow, EvidencePacketRow
from app.services.bo.m3_validation import validate_m3_assessment


def replay_recorded_m3_assessment(
    session: Session,
    analysis_run_id: str,
    *,
    validated_borrower_entity_ids: set[str] | None = None,
) -> dict[str, Any]:
    rows = session.execute(
        select(AgentRunRow)
        .where(AgentRunRow.analysis_run_id == analysis_run_id)
        .where(AgentRunRow.agent_id == "m3_corporate_management")
        .where(AgentRunRow.validation_status.in_(("valid", "validated")))
        .order_by(AgentRunRow.created_at)
    ).scalars().all()
    if not rows:
        raise ValueError(f"No accepted recorded M3 attempt for {analysis_run_id!r}.")
    row = rows[-1]
    parsed = row.parsed_response or {}
    proposal = parsed.get("m3_assessment")
    if not isinstance(proposal, dict):
        raise ValueError("Latest accepted M3 attempt has no m3_assessment proposal.")

    packet_rows = session.execute(
        select(EvidencePacketRow)
        .where(EvidencePacketRow.analysis_run_id == analysis_run_id)
        .where(EvidencePacketRow.agent_id == "m3_corporate_management")
        .order_by(EvidencePacketRow.created_at)
    ).scalars().all()
    evidence_texts = {
        item["evidence_id"]: item.get("text") or ""
        for packet in packet_rows
        for item in (packet.payload or {}).get("narrative_evidence", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    governed = validate_m3_assessment(
        proposal,
        allowed_evidence_ids=set(row.input_evidence_ids or []),
        validated_borrower_entity_ids=validated_borrower_entity_ids or set(),
        evidence_texts=evidence_texts,
    ).model_dump(mode="json")
    return {
        "replay_kind": "offline_deterministic_validator_replay",
        "analysis_run_id": analysis_run_id,
        "agent_run_id": row.run_id,
        "model_called": False,
        "original_proposal": proposal,
        "governed_assessment": governed,
    }
