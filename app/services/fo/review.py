"""An analyst's decisions on an escalated FO run.

A going-concern FAIL (``review_run``): ``continue`` completes the run with the gated composite
(overall score 10; the measures stay visible as "superseded by going-concern fail" and are not
counted; agent triggers are skipped); ``stop`` closes it as "Not scored: going-concern fail".
When the agent read FAIL but its verified evidence doesn't support it, ``use_verified`` keeps the
verified result instead; ``continue`` / ``stop`` side with the agent.

An escalated measure (``override_measure``; today liquidity and debt maturity after a covenant
breach, later the measures that trigger an agent): the analyst accepts the engine's score or
overrides it, and the composite is re-weighted on that score.

Every decision is recorded once, bound to the exact payload it was made on; the recorded
payload itself is never changed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, content_hash
from app.models.base import utcnow
from app.models.orm import FinancialOverviewOverrideRow, FinancialOverviewReviewRow, FinancialOverviewRunRow

from .measures import credit_rating
from .runner import AWAITING_REVIEW, NOT_SCORED, composite_for, output_folder

DECISIONS = {"continue": ("completed", "continued"), "stop": ("stopped", "stopped")}


class FoReviewError(ValueError):
    """The decision cannot be recorded (unknown run, not awaiting review, missing reviewer or reason)."""


def review_view(row: FinancialOverviewReviewRow | None) -> dict | None:
    if row is None:
        return None
    return {"review_id": row.review_id, "run_id": row.run_id, "check": row.check, "decision": row.decision,
            "reviewer": row.reviewer, "reason": row.reason, "run_content_hash": row.run_content_hash,
            "decided_at": row.decided_at.isoformat()}


def review_for(session: Session, run_id: str) -> FinancialOverviewReviewRow | None:
    return session.query(FinancialOverviewReviewRow).filter_by(run_id=run_id).one_or_none()


def review_run(session: Session, run_id: str, *, decision: str, reviewer: str, reason: str,
               output_root: str | Path | None = None) -> dict:
    """Record the analyst's going-concern decision. On a going-concern FAIL: ``continue`` or ``stop``. When the
    agent read FAIL but its verified evidence doesn't support it (a disagreement), also ``use_verified``: the
    verified result stands and the run goes on to any measure still escalated; ``continue`` / ``stop`` side with
    the agent's FAIL."""
    decision = (decision or "").strip().lower()
    reviewer, reason = (reviewer or "").strip(), (reason or "").strip()
    run = session.get(FinancialOverviewRunRow, run_id)
    if run is None:
        raise FoReviewError(f"No Financial Overview run {run_id!r}.")
    payload = dict(run.payload)
    escalation = next((e for e in payload.get("escalations") or [] if e["type"] == "run_decision"), None)
    disagreement = bool(escalation and escalation.get("disagreement"))
    allowed = (*DECISIONS, "use_verified") if disagreement else tuple(DECISIONS)
    if decision not in allowed:
        raise FoReviewError("Decision must be " + " or ".join(f"'{d}'" for d in allowed) + ".")
    if not reviewer or not reason:
        raise FoReviewError("A reviewer name and a reason are required.")
    if review_for(session, run_id) is not None:
        raise FoReviewError(f"Run {run_id} already has a recorded decision.")
    if run.status != AWAITING_REVIEW:
        raise FoReviewError(f"Run {run_id} is not awaiting review (status: {run.status}).")
    if content_hash(payload) != run.content_hash:
        raise FoReviewError(f"Run {run_id}'s payload does not match its content hash; not reviewing it.")
    if payload.get("escalations") is not None and escalation is None:
        raise FoReviewError(f"Run {run_id} has no going-concern fail to decide; it is waiting for a measure "
                            "override (fo-override).")

    row = FinancialOverviewReviewRow(
        review_id=f"for-{uuid.uuid4().hex[:16]}", run_id=run_id, check="going_concern", decision=decision,
        reviewer=reviewer, reason=reason, run_content_hash=run.content_hash, decided_at=utcnow())
    session.add(row)
    if decision == "use_verified":
        overrides = overrides_for(session, run_id)
        text = f"Going concern: the verified result ({payload['gate']['going_concern'].upper()}) stands"
        if pending_measures(payload, overrides):
            run.human_review_status = "verified_accepted"
            session.flush()
            text += "; measures still await the analyst (fo-override)"
        else:
            _complete(run, payload, overrides, row.decided_at, "verified_accepted")
            session.flush()
    else:
        run.status, run.human_review_status = DECISIONS[decision]
        if decision == "continue":
            composite = composite_for(payload, gc_status="fail")["columns"][0]["score"]
            run.composite_score, run.credit_rating = composite, credit_rating(composite)
        run.completed_at = row.decided_at
        session.flush()
        text = (f"Overall score {run.composite_score:g} ({run.credit_rating}): measures superseded by "
                "going-concern fail, not counted; agent triggers skipped" if decision == "continue" else NOT_SCORED)
    result = {**review_view(row), "run_status": run.status, "human_review_status": run.human_review_status,
              "composite_score": run.composite_score, "credit_rating": run.credit_rating, "result": text}
    if output_root is not None:
        result["outputs"] = write_review_outputs(run, row, output_root)
    return result


def write_review_outputs(run: FinancialOverviewRunRow, review: FinancialOverviewReviewRow | None,
                         output_root: str | Path, overrides: list[dict] | None = None) -> dict[str, str]:
    """Add ``review.json`` (a continue / stop decision) or ``overrides.json`` (measure scores) to the run's output
    folder and re-render its HTML with the decisions."""
    from .reporting import render_overview_html

    folder = output_folder(output_root, run.case_id, run.run_id)
    folder.mkdir(parents=True, exist_ok=True)
    html_path = folder / "financial_overview.html"
    out = {}
    if review is not None:
        out["review.json"] = folder / "review.json"
        out["review.json"].write_text(canonical_json(review_view(review)) + "\n", encoding="utf-8", newline="\n")
    if overrides:
        out["overrides.json"] = folder / "overrides.json"
        out["overrides.json"].write_text(canonical_json(overrides) + "\n", encoding="utf-8", newline="\n")
    html_path.write_text(render_overview_html(dict(run.payload), run=run_header(run), review=review_view(review),
                                              overrides=overrides or []),
                         encoding="utf-8", newline="\n")
    from .memo import build_memo, filing_source, render_memo_html

    memo = build_memo(dict(run.payload), run=run_header(run), review=review_view(review), overrides=overrides or [],
                      source=filing_source(run.filing_folder))
    out["financial_memo.json"] = folder / "financial_memo.json"
    out["financial_memo.json"].write_text(canonical_json(memo) + "\n", encoding="utf-8", newline="\n")
    out["financial_memo.html"] = folder / "financial_memo.html"
    out["financial_memo.html"].write_text(render_memo_html(memo), encoding="utf-8", newline="\n")
    return {**{k: str(v) for k, v in out.items()}, "financial_overview.html": str(html_path)}


# --- measure overrides (an escalated measure's score) ------------------------------------------------------

OVERRIDE_DECISIONS = ("accept", "override")


def override_view(row: FinancialOverviewOverrideRow) -> dict:
    return {"override_id": row.override_id, "run_id": row.run_id, "measure": row.measure, "source": row.source,
            "decision": row.decision, "engine_score": row.engine_score, "analyst_score": row.analyst_score,
            "reviewer": row.reviewer, "reason": row.reason, "run_content_hash": row.run_content_hash,
            "decided_at": row.decided_at.isoformat()}


def overrides_for(session: Session, run_id: str) -> list[dict]:
    rows = session.query(FinancialOverviewOverrideRow).filter_by(run_id=run_id).order_by(
        FinancialOverviewOverrideRow.decided_at).all()
    return [override_view(r) for r in rows]


def override_scores(overrides: list[dict]) -> dict:
    """{measure: score} the composite uses: the analyst's score, or the engine's when accepted."""
    return {o["measure"]: o["analyst_score"] for o in overrides if o["analyst_score"] is not None}


def override_decisions(overrides: list[dict]) -> dict:
    """{measure: "override" or "accept"}: whether the analyst replaced the engine's score or kept it."""
    return {o["measure"]: o["decision"] for o in overrides}


def override_measure(session: Session, run_id: str, *, measure: str, decision: str, reviewer: str, reason: str,
                     score: float | None = None, output_root: str | Path | None = None) -> dict:
    """Record the analyst's score for one escalated measure: ``accept`` keeps the engine's score, ``override``
    replaces it with ``score`` (1-10). When every escalated measure is decided the run completes with the composite
    re-weighted on those scores; the recorded payload is never changed."""
    decision = (decision or "").strip().lower()
    reviewer, reason = (reviewer or "").strip(), (reason or "").strip()
    if decision not in OVERRIDE_DECISIONS:
        raise FoReviewError("Decision must be 'accept' or 'override'.")
    if not reviewer or not reason:
        raise FoReviewError("A reviewer name and a reason are required.")
    if decision == "override":
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise FoReviewError("An override needs a score from 1 to 10.") from None
        if not 1 <= score <= 10:
            raise FoReviewError("An override needs a score from 1 to 10.")
        score = round(score, 1)
    run = session.get(FinancialOverviewRunRow, run_id)
    if run is None:
        raise FoReviewError(f"No Financial Overview run {run_id!r}.")
    if run.status != AWAITING_REVIEW:
        raise FoReviewError(f"Run {run_id} is not awaiting review (status: {run.status}).")
    payload = dict(run.payload)
    if content_hash(payload) != run.content_hash:
        raise FoReviewError(f"Run {run_id}'s payload does not match its content hash; not reviewing it.")
    pending = [e for e in payload.get("escalations") or [] if e["type"] == "measure_override"]
    escalation = next((e for e in pending if e["measure"] == measure), None)
    if escalation is None:
        raise FoReviewError(f"Run {run_id} has no escalated measure {measure!r} to override.")
    decided = {o["measure"] for o in overrides_for(session, run_id)}
    if measure in decided:
        raise FoReviewError(f"Run {run_id} already has a recorded decision for {measure!r}.")

    row = FinancialOverviewOverrideRow(
        override_id=f"foo-{uuid.uuid4().hex[:16]}", run_id=run_id, measure=measure, source=escalation["check"],
        decision=decision, engine_score=escalation["engine_score"],
        analyst_score=score if decision == "override" else escalation["engine_score"],
        reviewer=reviewer, reason=reason, run_content_hash=run.content_hash, decided_at=utcnow())
    session.add(row)
    session.flush()
    overrides = overrides_for(session, run_id)
    composite = composite_for(payload, override_scores(overrides), override_decisions(overrides))
    score_now = composite["columns"][0]["score"]
    run_decision = next((e for e in payload.get("escalations") or [] if e["type"] == "run_decision"), None)
    if not pending_measures(payload, overrides) and (run_decision is None or review_for(session, run_id)):
        _complete(run, payload, overrides, row.decided_at,
                  "overridden" if any(o["decision"] == "override" for o in overrides) else "accepted")
        session.flush()
    engine = "none" if row.engine_score is None else format(row.engine_score, "g")
    how = (f"overridden by analyst to {row.analyst_score:g} (engine {engine})" if decision == "override"
           else f"engine score {engine}, accepted by the analyst")
    composite_text = f"{score_now:g} ({credit_rating(score_now)})" if score_now is not None else "incomplete"
    result = {**override_view(row), "run_status": run.status, "human_review_status": run.human_review_status,
              "composite_score": score_now, "credit_rating": credit_rating(score_now) if score_now is not None else None,
              "result": f"{escalation['measure_name']}: {how}. Composite risk score {composite_text}."}
    if output_root is not None:
        result["outputs"] = write_review_outputs(run, review_for(session, run_id), output_root,
                                                 overrides=overrides)
    return result


def pending_measures(payload: dict, overrides: list[dict]) -> list[str]:
    """The escalated measures with no analyst decision yet."""
    decided = {o["measure"] for o in overrides}
    return [e["measure"] for e in payload.get("escalations") or []
            if e["type"] == "measure_override" and e["measure"] not in decided]


def _complete(run, payload, overrides, decided_at, review_status):
    """Every escalation is decided: complete the run on the composite re-weighted with the analyst's scores."""
    score = composite_for(payload, override_scores(overrides), override_decisions(overrides))["columns"][0]["score"]
    run.status, run.human_review_status = "completed", review_status
    run.composite_score = score
    run.credit_rating = credit_rating(score) if score is not None else None
    run.completed_at = decided_at


def run_header(run: FinancialOverviewRunRow) -> dict:
    return {"case_id": run.case_id, "run_id": run.run_id, "started_at": run.started_at.isoformat(),
            "content_hash": run.content_hash, "run_status": run.status}
