"""The RMS verdict: grade, gates, the verdict matrix, terms, and a feasibility check of the structure.

Order: the adjusted grade (FO composite notched by the M4 verdict and the business band) sets the level used
everywhere after it: the sizing multiples, the tenor, the terms; the capacity is sized at that level and tenor;
the gates run; the matrix (adjusted level x utilisation) gives the verdict; the approved
structure is then run through the existing structuring engine (``app.services.structuring``) as an
independent leverage / DSCR / liquidity / LTV check. Deterministic: no LLM, no I/O.

Every result requires human review: the FO rating methodology is provisional and FO and BO don't share a
verified evidence snapshot.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from app.services.fo.measures import credit_rating
from app.services.structuring import BorrowerForecast, FeasibilityPolicy, StructuringEngine, assemble_candidate

from .capacity import debt_capacity, existing_debt_service, level
from .inputs import RmsRequest, load_bo, load_fo
from .mitigants import mitigants
from .policy import (
    AMORTISATION, BUSINESS_BAND_NOTCH, DEBT_MISMATCH_TOLERANCE, DEFAULT_INTEREST_RATE, DEFAULT_TENOR_YEARS,
    DOWNSIDE_DSCR_MIN, DOWNSIDE_SHOCK, DSCR_COVENANT, LEVERAGE_COVENANT_HEADROOM, LEVERAGE_MULTIPLE,
    M4_VERDICT_NOTCH, MAX_LTV, RMS_POLICY_VERSION, SECURED_LEVELS, SEVERITY_FLOOR, SEVERITY_MEASURES,
    SEVERITY_TRIGGER, SUBORDINATION_DRAG_SECURED, TENOR_CAP_YEARS, UTILISATION_EDGES, VERDICT_MATRIX,
)

RMS_SCHEMA_VERSION = "rms-1.1"  # 1.1: fo_decisions; capacity "level" is the adjusted grade's
APPROVALS = ("approve", "approve_with_conditions", "approve_with_conditions_secured")


def adjusted_grade(fo, bo):
    """The FO composite score notched by the BO's credit-quality judgements (the M4 verdict, the business band),
    then FO's severity floor. None without an FO composite. M3 structural findings are a capacity haircut, not a
    notch: each BO signal is counted once."""
    if fo.composite_score is None:
        return None
    notches = []
    if bo.m4_verdict in M4_VERDICT_NOTCH:
        notches.append({"source": "m4_verdict", "reason": f"M4 credit verdict {bo.m4_verdict}",
                        "notch": M4_VERDICT_NOTCH[bo.m4_verdict]})
    if bo.business_band in BUSINESS_BAND_NOTCH:
        notches.append({"source": "business_band", "reason": f"BO business risk band {bo.business_band}",
                        "notch": BUSINESS_BAND_NOTCH[bo.business_band]})
    score = min(10.0, max(1.0, fo.composite_score + sum(n["notch"] for n in notches)))
    severe = [k for k in SEVERITY_MEASURES if (fo.scores.get(k) or 0) >= SEVERITY_TRIGGER]
    floored = bool(severe) and score < SEVERITY_FLOOR
    if floored:
        score = float(SEVERITY_FLOOR)
    score = round(score, 1)
    return {"fo_composite_score": fo.composite_score, "fo_credit_rating": fo.credit_rating, "notches": notches,
            "severity_floor": {"applied": floored, "measures": severe}, "score": score,
            "rating": credit_rating(score), "level": level(score)}


def gates(fo, bo, capacity):
    """[{gate_id, outcome, reason}] for each hard gate that holds; outcome is decline or refer."""
    out = []

    def hold(gate_id, outcome, reason):
        out.append({"gate_id": gate_id, "outcome": outcome, "reason": reason})

    if fo.gc_status == "fail":
        hold("RMS-G01", "decline", "FO going-concern pre-check: fail")
    if fo.escalations:
        hold("RMS-G02", "refer", f"FO run awaiting an analyst: {len(fo.escalations)} escalation(s) undecided")
    if fo.covenant_status == "breach":
        hold("RMS-G03", "refer", "FO covenant pre-check: breach not waived")
    if fo.composite_score is None:
        hold("RMS-G04", "refer", "No FO composite score")
    if not bo.m4_present:
        hold("RMS-G05", "refer", "No Module 4 output")
    elif bo.m4_status != "complete" or not bo.m4_debt_validated:
        hold("RMS-G05", "refer", f"Module 4 {bo.m4_status or 'status unknown'}; debt-structure specialist "
                                 f"{'validated' if bo.m4_debt_validated else 'not validated (Validator 8)'}")
    if bo.blocking_escalations:
        hold("RMS-G06", "refer", f"{len(bo.blocking_escalations)} unresolved high-severity BO challenge(s)")
    if fo.debt_m and bo.m4_total_debt_m:
        gap = abs(fo.debt_m - bo.m4_total_debt_m) / max(fo.debt_m, bo.m4_total_debt_m)
        if gap > DEBT_MISMATCH_TOLERANCE:
            hold("RMS-G07", "refer", f"FO total debt ${fo.debt_m:,.0f}M and M4 total indebtedness "
                                     f"${bo.m4_total_debt_m:,.0f}M differ by {gap:.0%}")
    if bo.handoff_review_status == "rejected":
        hold("RMS-G10", "refer", "The Business Overview handoff was rejected by its reviewer")
    if capacity is not None and capacity["derived_capacity_m"] is None:
        hold("RMS-G08", "refer", "Debt capacity unavailable: " + ", ".join(capacity["unavailable"]))
    return out


def utilisation_band(utilisation):
    return next((i for i, edge in enumerate(UTILISATION_EDGES) if utilisation <= edge), len(UTILISATION_EDGES))


def _covenants(fo, grade_level, amount):
    pro_forma = None if not fo.ebitdar_m else (fo.adj_debt_m + amount) / fo.ebitdar_m
    headroom = LEVERAGE_COVENANT_HEADROOM[grade_level]
    if headroom is None or pro_forma is None:
        out = [{"covenant": "debt_incurrence", "test": "pro forma Debt / EBITDAR for new debt",
                "max": LEVERAGE_MULTIPLE[grade_level]}]
    else:
        out = [{"covenant": "max_leverage", "test": "Debt / EBITDAR (lease-adjusted), quarterly",
                "max": round(pro_forma * headroom, 2)}]
    if grade_level in DSCR_COVENANT:
        out.append({"covenant": "min_dscr", "test": "CFADS / debt service, quarterly",
                    "min": DSCR_COVENANT[grade_level]})
    return out, None if pro_forma is None else round(pro_forma, 2)


def structure_terms(fo, bo, grade, *, amount, tenor_years, interest_rate, secured_requested):
    drag = bo.subordination_drag_ratio
    secured = (secured_requested or grade["level"] in SECURED_LEVELS or bo.m4_placement == "secured_tier_1"
               or (drag is not None and drag > SUBORDINATION_DRAG_SECURED))
    kind, share, sweep = AMORTISATION[grade["level"]]
    covenants, pro_forma = _covenants(fo, grade["level"], amount)
    return {
        "amount_m": amount,
        "rank": "secured_tier_1" if secured else (bo.m4_placement or "senior_unsecured_tier_2"),
        "secured": secured,
        "tenor_years": tenor_years,
        "interest_rate": interest_rate,
        "amortisation": {"type": kind, "amortised_share": share, "excess_cash_sweep": sweep},
        "guarantees": "the same upstream guarantors as the existing senior notes (per M4 Deliverable 1)",
        "pro_forma_leverage": pro_forma,
        "financial_covenants": covenants,
    }


def feasibility(fo, bo, terms, protections, grade_level, *, collateral_value_m, analysis_run_id):
    """Run the approved structure through the structuring engine. Its CFADS is what is left after the
    existing debt service, so its DSCR covers the new loan alone."""
    existing, _ = existing_debt_service(fo, bo)
    by = lambda cat: [p for p in protections if p["category"] == cat]  # noqa: E731
    candidate = assemble_candidate(
        analysis_run_id=analysis_run_id,
        facility_amount=terms["amount_m"],
        tenor_months=terms["tenor_years"] * 12,
        amortization={"type": "bullet" if terms["amortisation"]["type"] == "bullet" else "straight_line",
                      "interest_rate": terms["interest_rate"]},
        covenant_package=[*terms["financial_covenants"], *by("covenant")],
        collateral=by("collateral"),
        reporting_requirements=by("reporting"),
        other_protections=[*by("structure"), *by("condition_precedent")],
    )
    forecast = BorrowerForecast(
        ebitda=fo.ebitdar_m, cfads=fo.cfads_m - existing, existing_net_debt=fo.adj_net_debt_m,
        available_liquidity=fo.liquidity_sources_m, liquidity_need=fo.liquidity_uses_m,
        collateral_value=collateral_value_m, downside_shock_pct=DOWNSIDE_SHOCK)
    policy = FeasibilityPolicy(max_leverage=LEVERAGE_MULTIPLE[grade_level], min_dscr=1.0,
                               min_downside_dscr=DOWNSIDE_DSCR_MIN, min_liquidity_coverage=1.0, max_ltv=MAX_LTV)
    result = StructuringEngine(policy).evaluate(candidate, forecast)
    detail = result.feasibility_detail
    return {"feasible": result.feasible, "candidate_id": candidate.candidate_id,
            "failures": detail["failures"], "unavailable": detail["unavailable"],
            "stress": detail["stress"], "ltv": detail.get("ltv")}


def _limitations(bo):
    out = ["The FO rating methodology is provisional; RMS thresholds are illustrative, not bank policy.",
           "FO and BO do not share a verified evidence snapshot; overlapping figures are compared, not merged."]
    if bo.handoff_review_status == "pending":
        out.append("The Business Overview handoff is still pending human review.")
    if bo.business_band is None:
        out.append("BO business risk band unavailable: no business-band notch or haircut applied.")
    if not bo.m3_findings:
        out.append("No negative M3 structural finding counted (none found, or the M3 assessment is unavailable).")
    return out


@dataclass
class RmsResult:
    entity_name: str | None
    period_end: str | None
    request: dict
    verdict: str
    approved_amount_m: float | None
    utilisation: float | None
    grade: dict | None
    capacity: dict | None
    gates: list = field(default_factory=list)
    terms: dict | None = None
    mitigants: list = field(default_factory=list)
    feasibility: dict | None = None
    fo_decisions: list = field(default_factory=list)  # the FO analyst decisions applied
    limitations: list = field(default_factory=list)
    requires_human_review: bool = True
    schema_version: str = RMS_SCHEMA_VERSION
    policy_version: str = RMS_POLICY_VERSION

    def to_dict(self):
        return asdict(self)


def assess(request: RmsRequest, fo_payload: dict, *, handoff: dict | None = None, m4_analysis: dict | None = None,
           fo_review: dict | None = None, fo_overrides: list | None = None,
           analysis_run_id: str = "rms") -> RmsResult:
    """The RMS verdict for ``request`` from an FO payload and the BO handoff and/or Module 4 output.
    ``fo_review`` and ``fo_overrides`` are the analyst's decisions on an escalated FO run (``review.json`` and
    ``overrides.json``, or the database rows): their scores replace the engine's and the gate on open FO
    escalations clears once every one is decided."""
    fo, bo = load_fo(fo_payload, fo_review, fo_overrides), load_bo(handoff, m4_analysis)
    grade = adjusted_grade(fo, bo)
    rate = request.interest_rate if request.interest_rate is not None else DEFAULT_INTEREST_RATE
    tenor = request.tenor_years or DEFAULT_TENOR_YEARS
    if grade is not None:
        tenor = min(tenor, TENOR_CAP_YEARS[grade["level"]])
    capacity = (debt_capacity(fo, bo, grade_level=grade["level"], tenor_years=tenor, interest_rate=rate)
                if grade is not None else None)
    held = gates(fo, bo, capacity)
    result = RmsResult(entity_name=fo.entity_name, period_end=fo.period_end, request=asdict(request),
                       verdict="refer", approved_amount_m=None, utilisation=None, grade=grade, capacity=capacity,
                       gates=held, fo_decisions=list(fo.analyst_decisions), limitations=_limitations(bo))
    derived = capacity["derived_capacity_m"] if capacity else None
    # No capacity at all is the worst utilisation band; it is recorded as None (JSON has no infinity).
    utilisation = None if derived is None else request.requested_amount_m / derived if derived > 0 else math.inf
    if utilisation is not None and utilisation != math.inf:
        result.utilisation = round(utilisation, 3)
    if held:
        result.verdict = "decline" if any(g["outcome"] == "decline" for g in held) else "refer"
    else:
        verdict = VERDICT_MATRIX[grade["level"]][utilisation_band(utilisation)]
        amount = request.requested_amount_m
        if verdict == "counter_offer":
            amount = math.floor(derived * 10) / 10
            if amount <= 0:
                verdict = "decline"
        result.verdict = verdict
        if verdict in (*APPROVALS, "counter_offer"):
            result.approved_amount_m = amount
    # Terms are drafted for every verdict a structure can follow (approvals and counter-offers), and as
    # provisional terms for a referral that got as far as a capacity.
    amount = result.approved_amount_m or (request.requested_amount_m if result.verdict == "refer" else None)
    if amount is not None and grade is not None and derived is not None:
        result.terms = structure_terms(fo, bo, grade, amount=amount, tenor_years=tenor,
                                       interest_rate=rate,
                                       secured_requested=result.verdict == "approve_with_conditions_secured")
        result.terms["provisional"] = result.verdict == "refer"
        result.mitigants = mitigants(fo, bo, secured=result.terms["secured"])
        if result.approved_amount_m is not None:
            result.feasibility = feasibility(fo, bo, result.terms, result.mitigants, grade["level"],
                                             collateral_value_m=request.collateral_value_m,
                                             analysis_run_id=analysis_run_id)
            # A missing collateral value only leaves LTV untested: RMS-M09 makes the valuation a condition.
            blocking = [*result.feasibility["failures"],
                        *(u for u in result.feasibility["unavailable"] if u != "ltv")]
            if blocking:
                result.gates.append({"gate_id": "RMS-G09", "outcome": "refer",
                                     "reason": "Structuring engine: " + ", ".join(blocking)})
                result.verdict, result.approved_amount_m = "refer", None
    return result
