"""Read-only Financial Overview inspection views (the FO counterpart of ``bo.workbench``).

Every function is a pure READ of ``financial_overview_runs``. What is shown is the
recorded payload, so a run reads the same however often it is opened.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import FinancialOverviewRunRow

from .reporting import acquisitions_status, overview_view, prepaid_status, trigger_status

ARTIFACT_KINDS = ("overview",)


class FoRunNotFoundError(LookupError):
    pass


def _summary(row: FinancialOverviewRunRow, overrides: list | None = None) -> dict[str, Any]:
    checks = (row.payload or {}).get("precheck") or {}
    return {
        "going_concern": (checks.get("going_concern") or {}).get("status"),
        "covenants": (checks.get("covenants") or {}).get("status"),
        # An unresolved deal reads as the analyst's decision once made ("Overridden by analyst" / "Engine score").
        "acquisitions": acquisitions_status(checks.get("acquisitions"), overrides),
        "prepaid": prepaid_status(checks.get("prepaid"), overrides),
        "dscr_t1": trigger_status(((row.payload or {}).get("triggers") or {}).get("dscr_t1"), overrides),
        "dscr_t2": trigger_status(((row.payload or {}).get("triggers") or {}).get("dscr_t2"), overrides),
        "dscr_t3": trigger_status(((row.payload or {}).get("triggers") or {}).get("dscr_t3"), overrides),
        "icr_t1": trigger_status(((row.payload or {}).get("triggers") or {}).get("icr_t1"), overrides),
        "icr_t2": trigger_status(((row.payload or {}).get("triggers") or {}).get("icr_t2"), overrides),
        "icr_t3": trigger_status(((row.payload or {}).get("triggers") or {}).get("icr_t3"), overrides),
        # The model the Pre-check Agent ran on; older runs have it only on each check's agent record.
        "agent_model": (row.payload or {}).get("agent_model") or next(
            ((c.get("agent") or {}).get("model_id") for c in checks.values() if (c.get("agent") or {}).get("model_id")),
            None),
        "run_id": row.run_id,
        "case_id": row.case_id,
        "filing_folder": row.filing_folder,
        "entity_name": row.entity_name,
        "ticker": row.ticker,
        "period_end": row.period_end,
        "run_status": row.status,
        "composite_score": row.composite_score,
        "credit_rating": row.credit_rating,
        "human_review_status": row.human_review_status,
        "engine_version": row.engine_version,
        "content_hash": row.content_hash,
        "started_at": row.started_at.isoformat(),
    }


def fo_runs(session: Session, case_id: str | None = None) -> list[dict[str, Any]]:
    """FO runs, newest first (optionally for one case)."""
    from .review import overrides_for

    stmt = select(FinancialOverviewRunRow).order_by(FinancialOverviewRunRow.started_at.desc())
    if case_id is not None:
        stmt = stmt.where(FinancialOverviewRunRow.case_id == case_id)
    return [_summary(row, overrides_for(session, row.run_id)) for row in session.scalars(stmt)]


def fo_run(session: Session, run_id: str) -> FinancialOverviewRunRow:
    row = session.get(FinancialOverviewRunRow, run_id)
    if row is None:
        raise FoRunNotFoundError(f"No Financial Overview run {run_id!r}.")
    return row


def fo_artifact(session: Session, run_id: str, kind: str) -> dict[str, Any]:
    """The recorded ``overview`` payload (the scored measures, as the FO project's financial_overview.json)."""
    if kind not in ARTIFACT_KINDS:
        raise FoRunNotFoundError(f"Unknown Financial Overview artifact {kind!r}.")
    return dict(fo_run(session, run_id).payload)


def fo_view(session: Session, run_id: str) -> dict[str, Any]:
    """The inspection view for one run: the run summary plus the scorecard view of its payload."""
    from .review import overrides_for, review_for, review_view

    row = fo_run(session, run_id)
    payload = dict(row.payload)
    review = review_view(review_for(session, run_id))
    overrides = overrides_for(session, run_id)
    return {
        **_summary(row, overrides),
        "integrity_ok": content_hash(payload) == row.content_hash,
        "review": review,
        "overrides": overrides,
        "overview": overview_view(payload, run_status=row.status, review=review, overrides=overrides),
    }
