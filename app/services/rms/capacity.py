"""Derived debt capacity: FO's figures, sized at the adjusted grade, less a structural haircut.

Base capacity is the smallest of four tests, on the FO headline figures (US$ millions):

* base leverage:      L_max x EBITDAR - lease-adjusted debt
* downside leverage:  L_max x EBITDAR x (1 + shock) - lease-adjusted net debt
* base DSCR:          (CFADS / DSCR_min - existing debt service) / (1 / tenor + rate)
* downside DSCR:      (CFADS x (1 + shock) / 1.0 - existing debt service) / (1 / tenor + rate)

``L_max`` and ``DSCR_min`` follow the adjusted grade's level (the FO composite notched by the M4 verdict and
the business band), so those BO judgements reach capacity through the grade alone. The new loan's debt service is its first year as a
straight-line amortiser (1 / tenor of principal + interest): conservative, and the same as the structuring
engine's, so a loan sized here also passes its leverage and DSCR checks. Existing debt service is FO's DSCR
debt service, or interest only when the maturities count as refinanced (FO's refinancing test scores 3 or
better and M4 records no refinancing distress). Large hospital operators roll their bonds, so counting every
maturity as repaid from cash would size them at zero.

The BO haircut then comes off, capped at 40%: only the structural facts that cut what a lender can reach (M3
findings on guarantor coverage, cash-flow access or JV leakage; unproven mitigants). Refinancing distress isn't
a haircut: it already switches the rollover off. A missing input makes its test (and the capacity) unavailable,
never zero.
"""

from __future__ import annotations

from .policy import (
    DOWNSIDE_DSCR_MIN, DOWNSIDE_SHOCK, DSCR_MIN, HAIRCUT_CAP, LEVELS, LEVERAGE_MULTIPLE, M3_FINDING_HAIRCUT,
    M3_FINDINGS_COUNTED, ROLLOVER_MAX_REFINANCING_SCORE, UNPROVEN_MITIGANT_HAIRCUT,
)


def level(score):
    """The risk level (low / moderate / elevated / high) of a 1-10 risk score."""
    return next(name for name, top in LEVELS if score <= top)


def rollover(fo, bo):
    """Whether existing maturities count as refinanced: FO's refinancing test at 3 or better and no M4
    refinancing distress event."""
    return (fo.refinancing_score is not None and fo.refinancing_score <= ROLLOVER_MAX_REFINANCING_SCORE
            and bo.distress_events == 0)


def existing_debt_service(fo, bo):
    """The existing debt service the new loan's coverage is tested on (US$ millions), and its basis."""
    if rollover(fo, bo):
        return fo.interest_m, "interest only: existing maturities count as refinanced"
    return fo.debt_service_m, "FO DSCR debt service: principal due in the next 12 months + interest"


def _sub(a, b):
    return None if a is None or b is None else a - b


def bo_haircuts(bo):
    """[(source, reason, share)] of the BO haircut, in the order applied."""
    out = []
    for finding_id, dimension in bo.m3_findings[:M3_FINDINGS_COUNTED]:
        out.append(("m3_structural", f"M3 negative structural finding {finding_id} ({dimension})",
                    M3_FINDING_HAIRCUT))
    if bo.unproven_mitigants:
        out.append(("unproven_mitigants", f"{len(bo.unproven_mitigants)} BO mitigant(s) unproven",
                    UNPROVEN_MITIGANT_HAIRCUT))
    return out


def debt_capacity(fo, bo, *, grade_level, tenor_years, interest_rate):
    """The derived debt capacity (US$ millions) with every test, the haircut and the assumptions behind it, sized
    at the adjusted grade's level."""
    multiple, dscr_min = LEVERAGE_MULTIPLE[grade_level], DSCR_MIN[grade_level]
    existing, existing_basis = existing_debt_service(fo, bo)
    service_per_unit = 1 / tenor_years + interest_rate  # first-year debt service per $ borrowed
    stressed = None if fo.ebitdar_m is None else fo.ebitdar_m * (1 + DOWNSIDE_SHOCK)
    raw = {
        "base_leverage": _sub(None if fo.ebitdar_m is None else multiple * fo.ebitdar_m, fo.adj_debt_m),
        "downside_leverage": _sub(None if stressed is None else multiple * stressed, fo.adj_net_debt_m),
        "base_dscr": (None if fo.cfads_m is None or existing is None
                      else (fo.cfads_m / dscr_min - existing) / service_per_unit),
        "downside_dscr": (None if fo.cfads_m is None or existing is None
                          else (fo.cfads_m * (1 + DOWNSIDE_SHOCK) / DOWNSIDE_DSCR_MIN - existing)
                          / service_per_unit),
    }
    tests = {k: None if v is None else round(max(0.0, v), 1) for k, v in raw.items()}
    unavailable = [k for k, v in tests.items() if v is None]
    haircuts = bo_haircuts(bo)
    haircut = min(HAIRCUT_CAP, sum(share for _, _, share in haircuts))
    base = None if unavailable else min(tests.values())
    binding = None if unavailable else min(tests, key=tests.get)
    return {
        "level": grade_level,
        "inputs_m": {"ebitdar": fo.ebitdar_m, "adj_debt": fo.adj_debt_m, "adj_net_debt": fo.adj_net_debt_m,
                     "cfads": fo.cfads_m},
        "assumptions": {"leverage_multiple": multiple, "dscr_min": dscr_min,
                        "downside_shock": DOWNSIDE_SHOCK, "downside_dscr_min": DOWNSIDE_DSCR_MIN,
                        "tenor_years": tenor_years, "interest_rate": interest_rate,
                        "existing_debt_service_m": existing, "existing_debt_service_basis": existing_basis,
                        "rollover": rollover(fo, bo)},
        "tests_m": tests,
        "unavailable": unavailable,
        "binding_test": binding,
        "base_capacity_m": base,
        "haircuts": [{"source": s, "reason": r, "share": share} for s, r, share in haircuts],
        "haircut": round(haircut, 4),
        "derived_capacity_m": None if base is None else round(base * (1 - haircut), 1),
    }
