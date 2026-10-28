"""Read the RMS layer's inputs from the recorded FO and BO outputs (parsed JSON, no I/O).

FO: ``financial_overview.json`` (the run payload). BO: ``handoff.json`` (``AcceptedBusinessOverview``), and/or
the combined Module 4 output ``m4_analysis`` (``module_conclusions["M4"]["m4_analysis"]``, or a file such as
``examples/bo_m4/m4_analysis.sample.json``). M3 comes either as the legacy ``m3_assessment`` (scored structural
findings) or as the rebuild's ``m3_rebuild`` (a corporate-structure diagram and a management assessment, read as
typed artifacts: never turned into scored findings). Missing pieces are read as ``None``, never as a favourable
value; the gates decide what a missing piece means.

FO money is in US$; everything here is converted to US$ millions, M4's unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.fo.measures import credit_rating
from app.services.fo.review import override_decisions, override_scores
from app.services.fo.runner import composite_for
from app.services.fo.scoring import headline_score

from .policy import BLOCKING_BO_SEVERITIES, M3_DIMENSIONS

MEASURE_KEYS = ("leverage", "liquidity", "rcf", "dscr", "icr", "margin", "dso")


def _m(value):
    """US$ -> US$ millions (None stays None)."""
    return None if value is None else value / 1e6


@dataclass(frozen=True)
class RmsRequest:
    """The loan being asked for. Money in US$ millions; ``tenor_years`` and ``interest_rate`` default to the
    policy's sizing terms; ``collateral_value_m`` is needed for a secured loan's LTV test. Without
    ``requested_amount_m`` the assessment is a debt capacity report: no verdict, indicative terms at the
    capacity."""

    requested_amount_m: float | None = None
    tenor_years: int | None = None
    interest_rate: float | None = None
    purpose: str | None = None
    collateral_value_m: float | None = None

    def __post_init__(self):
        if self.requested_amount_m is not None and self.requested_amount_m <= 0:
            raise ValueError("requested_amount_m must be greater than 0")
        if self.tenor_years is not None and self.tenor_years <= 0:
            raise ValueError("tenor_years must be greater than 0")


@dataclass(frozen=True)
class FoInputs:
    entity_name: str | None
    period_end: str | None
    composite_score: float | None
    credit_rating: str | None
    gc_status: str | None
    covenant_status: str | None
    acquisitions_status: str | None
    escalations: list = field(default_factory=list)  # undecided ones only
    analyst_decisions: tuple = ()  # the FO review and overrides applied, as text
    scores: dict = field(default_factory=dict)  # measure key -> headline risk score (1-10) or None
    ebitdar_m: float | None = None
    adj_debt_m: float | None = None
    adj_net_debt_m: float | None = None
    debt_m: float | None = None
    leverage_ratio: float | None = None  # Debt / EBITDAR (lease-adjusted)
    cfads_m: float | None = None
    debt_service_m: float | None = None
    interest_m: float | None = None
    principal_m: float | None = None
    refinancing_score: float | None = None
    revolver_m: float | None = None  # undrawn committed revolver FO counts as a liquidity source
    liquidity_sources_m: float | None = None
    liquidity_uses_m: float | None = None
    triggers_fired: frozenset = frozenset()


def _headline_column(m):
    """The column a measure's headline score comes from (FO's ``headline_score``), or None."""
    _, heading = headline_score(m)
    return next((c for c in m["columns"] if c.get("heading") == heading), None) if heading else None


def undecided_escalations(escalations, review, overrides):
    """The FO escalations an analyst hasn't decided yet: a measure with no override, a run decision with no
    review."""
    decided = {o["measure"] for o in overrides}
    return [e for e in escalations
            if (e.get("type") == "measure_override" and e.get("measure") not in decided)
            or (e.get("type") == "run_decision" and review is None)]


def load_fo(payload: dict, review: dict | None = None, overrides: list | None = None) -> FoInputs:
    """The FO figures RMS uses, from a recorded FO payload: the composite, each measure's headline score and the
    headline columns' amounts.

    ``review`` (a going-concern decision, as ``review.json``) and ``overrides`` (measure decisions, as
    ``overrides.json``) are the analyst's decisions on an escalated run. The recorded payload never changes, so
    they are applied here the way FO applies them: an override replaces its measure's score, the composite is
    re-weighted with FO's own ``composite_for``, ``continue`` keeps the going-concern fail (composite 10) and
    ``stop`` leaves the run unscored. Only undecided escalations stay in ``escalations``."""
    overrides = list(overrides or [])
    metrics = {m["key"]: m for m in payload.get("metrics") or [] if m.get("key") != "overall"}
    scores, cols = {}, {}
    for key in MEASURE_KEYS:
        m = metrics.get(key)
        scores[key] = headline_score(m)[0] if m else None
        cols[key] = (_headline_column(m) if m else None) or {}
    lev, dscr, liq = cols["leverage"], cols["dscr"], cols["liquidity"]
    precheck = payload.get("precheck") or {}
    composite = payload.get("composite") or {}
    score, rating = composite.get("score"), composite.get("rating")
    # The verified going-concern result (the gate) where recorded, else the pre-check's.
    gc_status = (payload.get("gate") or {}).get("going_concern") or (precheck.get("going_concern") or {}).get("status")
    decision = (review or {}).get("decision")
    if decision == "stop":
        gc_status, score, rating = "fail", None, None
    elif decision == "continue" or overrides:
        if decision == "continue":
            gc_status = "fail"
        scores.update(override_scores(overrides))
        score = composite_for(payload, override_scores(overrides), override_decisions(overrides),
                              gc_status=gc_status)["columns"][0]["score"]
        rating = credit_rating(score) if score is not None else None
    shown = lambda v: "none" if v is None else format(v, "g")  # noqa: E731
    decisions = ([f"going concern: {decision} ({review.get('reviewer')})"] if decision else []) + [
        f"{o['measure']}: {o['decision']} {shown(o.get('engine_score'))} -> {shown(o['analyst_score'])} "
        f"({o.get('reviewer')})" for o in overrides]
    return FoInputs(
        entity_name=(payload.get("meta") or {}).get("entity_name"),
        period_end=(payload.get("meta") or {}).get("period_end"),
        composite_score=score,
        credit_rating=rating,
        gc_status=gc_status,
        covenant_status=(precheck.get("covenants") or {}).get("status"),
        acquisitions_status=(precheck.get("acquisitions") or {}).get("status"),
        escalations=undecided_escalations(payload.get("escalations") or [], review, overrides),
        analyst_decisions=tuple(decisions),
        scores=scores,
        ebitdar_m=_m(lev.get("ebitdar")),
        adj_debt_m=_m(lev.get("adj_debt")),
        adj_net_debt_m=_m(lev.get("adj_net_debt")),
        debt_m=_m(lev.get("debt")),
        leverage_ratio=(lev.get("debt_to_ebitdar") or {}).get("ratio"),
        cfads_m=_m(dscr.get("cash_flow")),
        debt_service_m=_m(dscr.get("debt_service")),
        interest_m=_m(dscr.get("interest")),
        principal_m=_m(dscr.get("principal")),
        refinancing_score=liq.get("refinancing_score"),
        revolver_m=_m(liq.get("revolver")),
        liquidity_sources_m=_m(liq.get("sources_total")),
        liquidity_uses_m=_m(liq.get("uses_total")),
        triggers_fired=frozenset(k for k, t in (payload.get("triggers") or {}).items() if t.get("fired")),
    )


@dataclass(frozen=True)
class M3Structure:
    """The M3 rebuild's corporate structure, as seen from the borrower down. Ownership is effective (multiplied
    down the chain), in percent."""

    simulated: bool  # the case assumptions are a simulated example, not a verified legal structure
    borrower: str | None
    guarantors: tuple = ()
    non_guarantor_opcos: tuple = ()  # operating companies outside the guarantor group
    partly_owned: tuple = ()  # (name, effective ownership %) of entities held below 100%


@dataclass(frozen=True)
class BoInputs:
    handoff_present: bool
    m4_present: bool
    handoff_review_status: str | None = None  # pending | accepted | rejected (a human's decision on the handoff)
    validators_enforced: bool = True  # False: the BO ran with its validators advisory (an ungoverned draft)
    m4_status: str | None = None  # complete | incomplete
    m4_debt_validated: bool = False  # Agent B (debt structure) passed Validator 8
    m4_verdict: str | None = None  # strong | satisfactory | adequate | weak
    m4_placement: str | None = None  # secured_tier_1 | senior_unsecured_tier_2
    m4_total_debt_m: float | None = None
    m4_legal_borrower: str | None = None  # the legal borrower of the largest share of M4's debt schedules
    subordination_drag_ratio: float | None = None
    distress_events: int | None = None
    refinancing_lead_time_months: float | None = None
    refinancing_spread_delta_bps: float | None = None
    maturity_extension_years: float | None = None
    m4_ladder_aggregated: bool = False  # the senior notes come as one aggregate line: no maturity ladder
    m4_revolver_available_m: float | None = None  # revolver limit less drawn
    m4_cp_outstanding_m: float | None = None
    distributions_vs_leverage: str = ""
    distributions_debt_funded: bool = False  # M4 D3: buybacks or dividends funded partly from debt
    distributions_3y_m: float | None = None  # M4 D3: buybacks + dividends over three years
    adverse_strategy_findings: tuple = ()  # Agent A acquisition / integration findings marked negative or adverse
    business_band: int | None = None  # 1 (lowest risk) - 4
    unproven_mitigants: tuple = ()
    vulnerabilities: tuple = ()  # (claim id, title, text) of the handoff's material risks and weaknesses
    risk_categories: tuple = ()  # (claim id, category) where the BO declares a vulnerability's category
    blocking_escalations: tuple = ()
    m3_source: str | None = None  # assessment (legacy, scored findings) | rebuild (typed artifacts) | None
    m3_findings: tuple = ()  # (finding id, analytical dimension) of the counted M3 structural findings
    m3_structure: M3Structure | None = None
    m3_management: dict | None = None  # status, admitted / expected profiles, overall outcome, model signal
    fo_questions: tuple = ()  # (hypothesis id, variable, question) the BO handed to FO
    open_questions: tuple = ()
    evidence_limitations: tuple = ()


def _m3(handoff):
    return (handoff.get("module_conclusions") or {}).get("M3") or {}


def _m3_findings(handoff):
    m3 = _m3(handoff).get("m3_assessment") or {}
    return tuple(
        (f.get("finding_id"), f.get("analytical_dimension"))
        for f in m3.get("structural_credit_findings") or []
        if f.get("credit_direction") == "negative" and f.get("materiality") == "high"
        and f.get("admissibility") != "audit_only" and f.get("analytical_dimension") in M3_DIMENSIONS)


def _m3_structure(rebuild):
    """The rebuild's structure diagram from the borrower down, or None when it is missing or didn't validate."""
    structure = rebuild.get("structure") or {}
    artifact = structure.get("artifact") or {}
    companies = list((artifact.get("companies") or {}).values())
    if structure.get("validation_status") != "passed" or len(companies) != 1:
        return None
    company = companies[0]
    nodes = {n["id"]: n for n in company.get("nodes") or []}
    children = {}
    for link in company.get("ownership_links") or []:
        children.setdefault(link["parent_id"], []).append((link["child_id"], link.get("ownership_percent")))
    provenance = artifact.get("provenance") or {}
    simulated = "simulated" in f"{provenance.get('assumption_status')} {provenance.get('data_status')}".lower()
    borrower = next((n for n in nodes.values() if "borrower" in (n.get("role") or "").lower()), None)
    if borrower is None:
        return M3Structure(simulated=simulated, borrower=None)
    guarantors, outside, partly = [], [], []
    stack = [(borrower["id"], 100.0)]
    while stack:
        parent, held = stack.pop()
        for child, percent in children.get(parent, []):
            node = nodes.get(child)
            if node is None:
                continue
            effective = held * (percent if percent is not None else 0) / 100
            name = node.get("display_name")
            if node.get("is_guarantor"):
                guarantors.append(name)
            elif node.get("role") == "OpCo":
                outside.append(name)
            if effective < 100:
                partly.append((name, round(effective, 1)))
            stack.append((child, effective))
    return M3Structure(simulated=simulated, borrower=borrower.get("display_name"), guarantors=tuple(guarantors),
                       non_guarantor_opcos=tuple(outside), partly_owned=tuple(partly))


def _m3_management(rebuild):
    management = rebuild.get("management") or {}
    if not management:
        return None
    artifact = management.get("artifact") or {}
    coverage = artifact.get("coverage") or {}
    return {"status": management.get("status"), "admitted": coverage.get("admitted"),
            "expected": coverage.get("expected"),
            "outcome": (artifact.get("overall_assessment") or {}).get("outcome"),
            "signal": (artifact.get("model_assessment") or {}).get("signal")}


DISTRIBUTION_STREAM_WORDS = ("repurchase", "buyback", "dividend", "distribution")
DEBT_FUNDING_WORDS = ("debt", "borrow", "note issuance")
DEBT_FUNDED_IMPACT_WORDS = ("debt-funded", "debt funded", "debt-supported", "debt supported", "debt-financed")


def _distributions(d3):
    """(debt-funded, three-year total US$M) of the shareholder distributions in M4 Deliverable 3's capital
    deployment reconciliation. A row's ``funding_basis`` (debt_funded | mixed | internal), where the specialist
    gives it, is read before the free-text funding source and leverage impact."""
    rows = [r for r in d3.get("capital_deployment_reconciliation") or []
            if any(w in (r.get("capital_allocation_stream") or "").lower() for w in DISTRIBUTION_STREAM_WORDS)]

    def debt_funded(row):
        if row.get("funding_basis") in ("debt_funded", "mixed", "internal"):
            return row["funding_basis"] != "internal"
        return (any(w in (row.get("primary_funding_source") or "").lower() for w in DEBT_FUNDING_WORDS)
                or any(w in (row.get("leverage_impact") or "").lower() for w in DEBT_FUNDED_IMPACT_WORDS))

    amounts = [r["cumulative_amount_3y_m"] for r in rows if r.get("cumulative_amount_3y_m") is not None]
    legacy = "debt-funded" in ((d3.get("debt_utilisation_matrix") or {}).get(
        "shareholder_distributions_vs_leverage_balance") or "").lower()
    return any(debt_funded(r) for r in rows) or legacy, (sum(amounts) if amounts else None)


def _legal_borrower(d1):
    """The legal borrower of the largest share of M4's drawn debt."""
    weights = {}
    for row, amount in [*((r, r.get("principal_amount_m")) for r in d1.get("senior_notes_schedule") or []),
                        *((r, r.get("principal_drawn_m")) for r in d1.get("non_note_obligations_schedule") or [])]:
        if row.get("legal_borrower") and amount and amount > 0:
            weights[row["legal_borrower"]] = weights.get(row["legal_borrower"], 0) + amount
    return max(weights, key=weights.get) if weights else None


def _facility(d1, words):
    return next((r for r in d1.get("non_note_obligations_schedule") or []
                 if any(w in (r.get("obligation_facility") or "").lower() for w in words)), None)


def load_bo(handoff: dict | None = None, m4_analysis: dict | None = None) -> BoInputs:
    """The BO signals RMS uses. ``m4_analysis`` defaults to the one inside the handoff's M4 conclusion."""
    handoff = handoff or {}
    if m4_analysis is None:
        m4_analysis = ((handoff.get("module_conclusions") or {}).get("M4") or {}).get("m4_analysis")
    m4 = m4_analysis or {}
    d1, d2, d3, d4 = (m4.get(f"deliverable_{i}") or {} for i in range(1, 5))
    framework = d1.get("creditor_priority_framework") or {}
    box = d4.get("executive_credit_summary_box") or {}
    agent_b = (m4.get("contributors") or {}).get("agent_b_debt_structure") or {}
    adverse = tuple(
        f.get("hypothesis_id") for f in m4.get("strategy_context") or []
        if f.get("section") in ("acquisition_strategy", "integration_risk")
        and f.get("direction") in ("negative", "adverse"))
    raw_vulnerabilities = handoff.get("operational_vulnerabilities") or []
    vulnerabilities = tuple((v.get("claim_id"), v.get("title") or "", v.get("text") or "")
                            for v in raw_vulnerabilities)
    metrics = d2.get("metrics") or {}
    notes = d1.get("senior_notes_schedule") or []
    revolver, cp = _facility(d1, ("revolv",)), _facility(d1, ("commercial paper",))
    revolver_available = (None if revolver is None or revolver.get("available_facility_limit_m") is None
                          else revolver["available_facility_limit_m"] - (revolver.get("principal_drawn_m") or 0))
    debt_funded, distributions_3y = _distributions(d3)
    m3 = _m3(handoff)
    rebuild = m3.get("m3_rebuild") or {}
    return BoInputs(
        handoff_present=bool(handoff),
        m4_present=bool(m4),
        handoff_review_status=handoff.get("human_review_status") if handoff else None,
        validators_enforced=handoff.get("validators_enforced", True) is not False,
        m4_status=m4.get("status"),
        m4_debt_validated=bool(agent_b.get("validated")),
        m4_verdict=box.get("overall_credit_rating_verdict"),
        m4_placement=box.get("recommended_loan_placement"),
        m4_total_debt_m=framework.get("consolidated_total_indebtedness_m"),
        m4_legal_borrower=_legal_borrower(d1),
        subordination_drag_ratio=framework.get("structural_subordination_drag_ratio"),
        distress_events=metrics.get("historical_refinancing_distress_events"),
        refinancing_lead_time_months=metrics.get("weighted_avg_refinancing_lead_time_months"),
        refinancing_spread_delta_bps=metrics.get("refinancing_cost_spread_delta_bps"),
        maturity_extension_years=metrics.get("weighted_avg_maturity_extension_years"),
        m4_ladder_aggregated=len(notes) == 1,
        m4_revolver_available_m=revolver_available,
        m4_cp_outstanding_m=None if cp is None else cp.get("principal_drawn_m"),
        distributions_vs_leverage=(d3.get("debt_utilisation_matrix") or {}).get(
            "shareholder_distributions_vs_leverage_balance") or "",
        distributions_debt_funded=debt_funded,
        distributions_3y_m=distributions_3y,
        adverse_strategy_findings=adverse,
        business_band=handoff.get("business_risk_band"),
        unproven_mitigants=tuple(u.get("hypothesis_id") for u in handoff.get("unproven_mitigants") or []),
        vulnerabilities=vulnerabilities,
        risk_categories=tuple((v.get("claim_id"), v["risk_category"]) for v in raw_vulnerabilities
                              if v.get("risk_category")),
        blocking_escalations=tuple(e for e in handoff.get("escalations") or []
                                   if e.get("severity") in BLOCKING_BO_SEVERITIES),
        m3_source="assessment" if "m3_assessment" in m3 else "rebuild" if rebuild else None,
        m3_findings=_m3_findings(handoff),
        m3_structure=_m3_structure(rebuild) if rebuild else None,
        m3_management=_m3_management(rebuild) if rebuild else None,
        fo_questions=tuple((q.get("hypothesis_id"), q.get("variable"), q.get("question"))
                           for q in handoff.get("fo_handoffs") or []),
        open_questions=tuple(handoff.get("open_questions") or []),
        evidence_limitations=tuple(handoff.get("evidence_limitations") or []),
    )
