"""The case credit view: the BO handoff, the FO scorecard and the RMS assessment of one case, side by side.

Read-only: it builds the page's data from the recorded runs and the RMS records. The runs default to the case's
latest FO run and latest BO handoff; the RMS panel shows the latest assessment made from those two runs, flagged
out of date when a human decision was recorded on either run after it.
"""

from __future__ import annotations

from app.models.orm import AcceptedBusinessOverviewRow, FinancialOverviewRunRow
from app.services.fo.review import overrides_for, review_for, review_view
from app.services.fo.scoring import COMPOSITE_WEIGHTS

from .inputs import undecided_escalations, load_bo, load_fo
from .policy import DEFAULT_INTEREST_RATE, DEFAULT_TENOR_YEARS
from .runner import current_inputs, records, rms_summary, stale_reasons


def _fo_panel(session, row):
    payload = dict(row.payload)
    review = review_view(review_for(session, row.run_id))
    overrides = overrides_for(session, row.run_id)
    engine, now = load_fo(payload), load_fo(payload, review, overrides)
    decided = {o["measure"]: o for o in overrides}
    return {
        "run_id": row.run_id, "status": row.status, "human_review_status": row.human_review_status,
        "entity_name": now.entity_name, "period_end": now.period_end,
        "engine_composite": engine.composite_score, "engine_rating": engine.credit_rating,
        "composite": now.composite_score, "rating": now.credit_rating, "gc_status": now.gc_status,
        "covenant_status": now.covenant_status,
        "measures": [{"key": key, "name": name, "weight": weight, "engine": engine.scores.get(key),
                      "score": now.scores.get(key), "decision": decided.get(key)}
                     for _, key, name, weight in COMPOSITE_WEIGHTS],
        "pending": undecided_escalations(payload.get("escalations") or [], review, overrides),
        "decisions": list(now.analyst_decisions),
        "review": review,
    }


def _bo_panel(row):
    payload = dict(row.payload)
    bo = load_bo(payload)
    return {
        "analysis_run_id": row.analysis_run_id, "handoff_id": row.handoff_id,
        "human_review_status": row.human_review_status, "reviewer": payload.get("reviewer"),
        "reviewed_at": payload.get("reviewed_at"), "conclusion_status": payload.get("conclusion_status"),
        "can_accept": row.human_review_status == "pending" and payload.get("business_topic_conclusion_id") is not None,
        "can_reject": row.human_review_status == "pending",
        "unresolved_challenges": payload.get("unresolved_challenges") or [],
        "business_band": bo.business_band,
        "m4": {"present": bo.m4_present, "status": bo.m4_status, "validated": bo.m4_debt_validated,
               "verdict": bo.m4_verdict, "placement": bo.m4_placement, "total_debt_m": bo.m4_total_debt_m,
               "distress_events": bo.distress_events},
        "m3_findings": [{"finding_id": f, "dimension": d} for f, d in bo.m3_findings],
        "risks": [{"claim_id": c, "title": t} for c, t, _ in bo.vulnerabilities],
        "unproven_mitigants": [u.get("capability") for u in payload.get("unproven_mitigants") or []],
    }


def credit_view(session, case_id: str, *, output_root, fo_run_id: str | None = None,
                bo_run_id: str | None = None) -> dict:
    fo_rows = (session.query(FinancialOverviewRunRow).filter_by(case_id=case_id)
               .order_by(FinancialOverviewRunRow.started_at.desc()).all())
    bo_rows = (session.query(AcceptedBusinessOverviewRow).filter_by(case_id=case_id)
               .order_by(AcceptedBusinessOverviewRow.created_at.desc()).all())
    fo_row = next((r for r in fo_rows if r.run_id == fo_run_id), None) if fo_run_id else (fo_rows or [None])[0]
    bo_row = (next((r for r in bo_rows if r.analysis_run_id == bo_run_id), None) if bo_run_id
              else (bo_rows or [None])[0])
    history = [r for r in records(output_root, case_id)
               if fo_row is not None and (r.get("sources") or {}).get("fo_run_id") == fo_row.run_id
               and (r.get("sources") or {}).get("bo_analysis_run_id") == (bo_row.analysis_run_id if bo_row else None)]
    latest = history[-1] if history else None
    current = current_inputs(session, fo_row, bo_row) if fo_row is not None else None
    request = (latest or {}).get("request") or {}
    return {
        "case_id": case_id,
        "fo_runs": [{"run_id": r.run_id, "status": r.status, "started_at": r.started_at.isoformat()} for r in fo_rows],
        "bo_runs": [{"analysis_run_id": r.analysis_run_id, "human_review_status": r.human_review_status,
                     "created_at": r.created_at.isoformat()} for r in bo_rows],
        "fo": _fo_panel(session, fo_row) if fo_row is not None else None,
        "bo": _bo_panel(bo_row) if bo_row is not None else None,
        "rms": {
            "latest": rms_summary(latest, latest["_path"]) if latest else None,
            "record": latest,
            "stale": stale_reasons(latest, current) if latest and current else [],
            "history": [{"run_id": r["run_id"], "assessed_at": r.get("assessed_at"), "verdict": r["verdict"],
                         "requested_amount_m": r["request"]["requested_amount_m"],
                         "approved_amount_m": r["approved_amount_m"],
                         "fo_decisions": r.get("fo_decisions") or [],
                         "bo_review_status": (r.get("sources") or {}).get("bo_review_status")}
                        for r in reversed(history)],
            "request": {"requested_amount_m": request.get("requested_amount_m"),
                        "tenor_years": request.get("tenor_years") or DEFAULT_TENOR_YEARS,
                        "interest_rate": request.get("interest_rate") or DEFAULT_INTEREST_RATE,
                        "collateral_value_m": request.get("collateral_value_m")},
        },
    }
