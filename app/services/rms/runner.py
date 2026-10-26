"""Recording RMS assessments, and re-assessing a case from its recorded BO and FO runs.

An assessment is written to ``<output>/rms/<case>/<run>/rms.json``: the ``RmsResult`` plus the runs it was built
from, when it was made, and the earlier assessment of the case it supersedes. RMS writes nothing to the
database; it reads it.

``assess_case`` re-assesses a case from the database: the FO run's payload with the analyst's recorded
decisions (``financial_overview_reviews`` / ``financial_overview_overrides``) applied, and the BO handoff.
``reassess_after_fo_review`` is called after each FO decision: when RMS has already assessed that FO run, it
assesses it again with the same request, so the verdict follows the analyst's scores.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.hashing import canonical_json
from app.models.orm import AcceptedBusinessOverviewRow, FinancialOverviewRunRow
from app.services.fo.review import overrides_for, review_for, review_view

from .inputs import RmsRequest
from .verdict import assess


class RmsRunError(ValueError):
    """The assessment cannot be made (no FO run, an unknown BO run, no request to reuse)."""


def new_run_id() -> str:
    return f"rms-{uuid.uuid4().hex[:16]}"


def records(output_root, case_id: str) -> list[dict]:
    """The case's recorded assessments, oldest first, each with its ``_path``."""
    out = []
    for path in (Path(output_root) / "rms" / case_id).glob("*/rms.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        out.append({**record, "_path": str(path)})
    return sorted(out, key=lambda r: r.get("assessed_at") or "")


def write_record(result, *, run_id: str, case_id: str, sources: dict, output_root) -> tuple[dict, Path]:
    """Write one assessment's ``rms.json``; it supersedes the case's latest earlier assessment, if any."""
    earlier = records(output_root, case_id)
    record = {"run_id": run_id, "case_id": case_id, "assessed_at": datetime.now(timezone.utc).isoformat(),
              "supersedes": earlier[-1]["run_id"] if earlier else None, "sources": sources, **result.to_dict()}
    folder = Path(output_root) / "rms" / case_id / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "rms.json"
    path.write_text(canonical_json(record) + "\n", encoding="utf-8", newline="\n")
    return record, path


def assess_case(session, case_id: str, *, output_root, request: RmsRequest | None = None,
                fo_run_id: str | None = None, bo_run_id: str | None = None) -> dict:
    """Assess ``case_id`` from its recorded runs (by default its latest FO run and latest BO handoff) and return
    the summary. Without ``request``, the case's latest assessment's request is reused."""
    fo_row = (session.get(FinancialOverviewRunRow, fo_run_id) if fo_run_id else
              session.query(FinancialOverviewRunRow).filter_by(case_id=case_id)
              .order_by(FinancialOverviewRunRow.started_at.desc()).first())
    if fo_row is None or fo_row.case_id != case_id:
        raise RmsRunError(f"No Financial Overview run {fo_run_id!r} for case {case_id!r}." if fo_run_id
                          else f"Case {case_id!r} has no Financial Overview run.")
    handoffs = session.query(AcceptedBusinessOverviewRow).filter_by(case_id=case_id)
    bo_row = (handoffs.filter_by(analysis_run_id=bo_run_id).one_or_none() if bo_run_id else
              handoffs.order_by(AcceptedBusinessOverviewRow.created_at.desc()).first())
    if bo_run_id and bo_row is None:
        raise RmsRunError(f"No Business Overview handoff for run {bo_run_id!r} in case {case_id!r}.")
    if request is None:
        earlier = records(output_root, case_id)
        if not earlier:
            raise RmsRunError(f"Case {case_id!r} has no earlier RMS assessment to reuse: give a requested amount.")
        request = RmsRequest(**earlier[-1]["request"])
    review = review_view(review_for(session, fo_row.run_id))
    overrides = overrides_for(session, fo_row.run_id)
    run_id = new_run_id()
    result = assess(request, dict(fo_row.payload), handoff=dict(bo_row.payload) if bo_row else None,
                    fo_review=review, fo_overrides=overrides, analysis_run_id=run_id)
    sources = {**_inputs(fo_row, bo_row, review, overrides), "fo_run_status": fo_row.status}
    record, path = write_record(result, run_id=run_id, case_id=case_id, sources=sources, output_root=output_root)
    return rms_summary(record, path)


def _inputs(fo_row, bo_row, review, overrides) -> dict:
    """What an assessment was made from: the runs, and the human decisions on them."""
    return {"bo_analysis_run_id": bo_row.analysis_run_id if bo_row else None,
            "bo_handoff_id": bo_row.handoff_id if bo_row else None,
            "bo_review_status": bo_row.human_review_status if bo_row else None,
            "fo_run_id": fo_row.run_id, "fo_content_hash": fo_row.content_hash,
            "fo_decisions": {"review": review["review_id"] if review else None,
                             "overrides": [o["override_id"] for o in overrides]}}


def current_inputs(session, fo_row, bo_row) -> dict:
    """The inputs an assessment of these runs would be made from now."""
    return _inputs(fo_row, bo_row, review_view(review_for(session, fo_row.run_id)),
                   overrides_for(session, fo_row.run_id))


def stale_reasons(record: dict, current: dict) -> list[str]:
    """Why an assessment no longer matches its runs' current state (empty when it does)."""
    made = record.get("sources") or {}
    if "fo_decisions" not in made or "bo_review_status" not in made:
        return ["it was made by an earlier RMS version that didn't record the BO and FO reviews"]
    out = []
    if made.get("bo_review_status") != current["bo_review_status"]:
        out.append(f"the BO handoff is now {current['bo_review_status']} (was {made.get('bo_review_status')})")
    if (made.get("fo_decisions") or {}) != current["fo_decisions"]:
        out.append("FO analyst decisions were recorded since")
    return out


def reassess_after_bo_review(session, analysis_run_id: str, output_root) -> dict | None:
    """Re-assess with the same request after a reviewer's decision on a BO handoff, when RMS has already
    assessed that BO run; otherwise None."""
    row = session.query(AcceptedBusinessOverviewRow).filter_by(analysis_run_id=analysis_run_id).one_or_none()
    if row is None:
        return None
    earlier = [r for r in records(output_root, row.case_id)
               if (r.get("sources") or {}).get("bo_analysis_run_id") == analysis_run_id]
    if not earlier:
        return None
    last = earlier[-1]
    return assess_case(session, row.case_id, output_root=output_root, request=RmsRequest(**last["request"]),
                       fo_run_id=last["sources"].get("fo_run_id"), bo_run_id=analysis_run_id)


def reassess_after_fo_review(session, fo_run_id: str, output_root) -> dict | None:
    """Re-assess the FO run's case with the same request after an analyst decision on that run, when RMS has
    already assessed it; otherwise None."""
    run = session.get(FinancialOverviewRunRow, fo_run_id)
    if run is None:
        return None
    earlier = [r for r in records(output_root, run.case_id) if (r.get("sources") or {}).get("fo_run_id") == fo_run_id]
    if not earlier:
        return None
    last = earlier[-1]
    return assess_case(session, run.case_id, output_root=output_root, request=RmsRequest(**last["request"]),
                       fo_run_id=fo_run_id, bo_run_id=last["sources"].get("bo_analysis_run_id"))


def capacity_breakdown(capacity: dict | None) -> dict | None:
    """The debt capacity as it was worked out, labelled for display: FO inputs, the four tests, the BO haircut."""
    if not capacity:
        return None
    a, tests = capacity["assumptions"], capacity["tests_m"]
    shock = f"{-a['downside_shock']:.0%}"
    labels = {
        "base_leverage": f"Leverage, base: {a['leverage_multiple']}x EBITDAR - lease-adjusted debt",
        "downside_leverage": f"Leverage, downside (EBITDAR -{shock}): {a['leverage_multiple']}x EBITDAR "
                             "- lease-adjusted net debt",
        "base_dscr": f"DSCR, base: (CFADS / {a['dscr_min']}x - existing debt service) / "
                     f"(1/{a['tenor_years']} + {a['interest_rate']:.2%})",
        "downside_dscr": f"DSCR, downside (CFADS -{shock}): (CFADS / {a['downside_dscr_min']}x - existing debt "
                         f"service) / (1/{a['tenor_years']} + {a['interest_rate']:.2%})",
    }
    return {
        "level": capacity.get("level") or capacity.get("fo_level"),  # rms-1.0 sized at the FO level
        "inputs_m": {"ebitdar": None, "adj_debt": None, "adj_net_debt": None, "cfads": None,
                     **(capacity.get("inputs_m") or {}), "existing_debt_service": a["existing_debt_service_m"]},
        "existing_debt_service_basis": a["existing_debt_service_basis"],
        "tests": [{"test": k, "label": labels[k], "capacity_m": v, "binding": k == capacity["binding_test"]}
                  for k, v in tests.items()],
        "base_capacity_m": capacity["base_capacity_m"],
        "haircut": capacity["haircut"],
        "haircuts": [f"{h['reason']}: {h['share']:.0%}" for h in capacity["haircuts"]],
        "derived_capacity_m": capacity["derived_capacity_m"],
    }


def rms_summary(record: dict, path: Path) -> dict:
    grade, capacity = record["grade"] or {}, record["capacity"] or {}
    return {
        "run_id": record["run_id"],
        "supersedes": record.get("supersedes"),
        "verdict": record["verdict"],
        "requested_amount_m": record["request"]["requested_amount_m"],
        "approved_amount_m": record["approved_amount_m"],
        "derived_capacity_m": capacity.get("derived_capacity_m"),
        "capacity": capacity_breakdown(capacity),
        "utilisation": record["utilisation"],
        "rating": grade.get("rating"),
        "level": grade.get("level"),
        "gates": [f"{g['gate_id']}: {g['reason']}" for g in record["gates"]],
        "mitigants": [m["rule_id"] for m in record["mitigants"]],
        "fo_decisions": record.get("fo_decisions") or [],
        "requires_human_review": record["requires_human_review"],
        "output": str(path),
    }

