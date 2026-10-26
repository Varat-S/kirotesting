"""Validator 8 -- deterministic checks for Module 4 output.

Validator 8 is PURE and DETERMINISTIC: it never calls an LLM. It re-checks the
Module 4 specialist's numerical and structural claims against the reported
figures and the internal consistency of the deliverables. Same input always
yields the same report.

The five checks (per the Module 4 methodology):

1. **Reconciliation.** The sum of the itemized debt tranches (senior notes +
   non-note obligations, drawn principal only) matches the reported Total
   Indebtedness in Note 8, within a small absolute tolerance.
2. **Subordination Ratio.** Recompute the Structural Subordination Drag Ratio
   from the tier figures and confirm the claimed ratio matches.
3. **Maturity Order.** The senior-note schedule's maturity years are in
   chronological (non-decreasing) order.
4. **Legal Entity.** Every tranche names a legal borrower, and the OpCo issuer
   vs HoldCo guarantor split is coherent (e.g. ``HCA Inc.`` issuer vs
   ``HCA Healthcare, Inc.`` guarantor) -- a tranche cannot name the same entity
   as both its sole borrower and its only credit support.
5. **Double-Counting.** Undrawn revolver limits (available facility limit, not
   drawn) and any intercompany borrowings are NOT folded into the reconciled
   total.

Each check produces a :class:`DebtStructureValidatorCheck`; the aggregate
:class:`DebtStructureValidatorReport` is ``passed`` only when every check passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.module4 import (
    Deliverable1,
    Module4Output,
    NonNoteDebtObligation,
    PriorityTier,
    SecurityStatus,
    SeniorNoteTranche,
)

# Stable check ids (auditable, referenced by escalations/tests).
CHECK_RECONCILIATION = "V8-01-RECONCILIATION"
CHECK_SUBORDINATION_RATIO = "V8-02-SUBORDINATION-RATIO"
CHECK_MATURITY_ORDER = "V8-03-MATURITY-ORDER"
CHECK_LEGAL_ENTITY = "V8-04-LEGAL-ENTITY"
CHECK_DOUBLE_COUNTING = "V8-05-DOUBLE-COUNTING"

# Marker substrings that flag an obligation as an intercompany borrowing, which
# must be excluded from consolidated total indebtedness.
_INTERCOMPANY_MARKERS = ("intercompany", "inter-company", "affiliate loan")


@dataclass(frozen=True)
class DebtStructureValidatorCheck:
    """The outcome of one deterministic debt-structure validator check."""

    check_id: str
    name: str
    passed: bool
    detail: str
    observed: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DebtStructureValidatorReport:
    """Aggregate debt-structure validator report over all checks."""

    checks: list[DebtStructureValidatorCheck]

    @property
    def passed(self) -> bool:
        """True only when EVERY check passed."""
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[DebtStructureValidatorCheck]:
        return [c for c in self.checks if not c.passed]

    def check(self, check_id: str) -> DebtStructureValidatorCheck:
        for c in self.checks:
            if c.check_id == check_id:
                return c
        raise KeyError(check_id)


def _is_intercompany(description: str) -> bool:
    text = description.lower()
    return any(marker in text for marker in _INTERCOMPANY_MARKERS)


class DebtStructureValidator:
    """Deterministic validator for Module 4 debt-structure output (Agent B).

    ``reconciliation_tolerance_m`` is an absolute tolerance in $M for the sum of
    tranches vs reported total; rounding across a large schedule makes an exact
    match unrealistic. ``ratio_tolerance`` guards the subordination ratio
    recomputation.
    """

    def __init__(
        self,
        *,
        reconciliation_tolerance_m: float = 1.0,
        ratio_tolerance: float = 0.0005,
    ) -> None:
        self._recon_tol = reconciliation_tolerance_m
        self._ratio_tol = ratio_tolerance

    def validate(self, output: Module4Output) -> DebtStructureValidatorReport:
        d1 = output.deliverable_1
        checks = [
            self._check_reconciliation(d1),
            self._check_subordination_ratio(d1),
            self._check_maturity_order(d1.senior_notes_schedule),
            self._check_legal_entity(d1),
            self._check_double_counting(d1),
        ]
        return DebtStructureValidatorReport(checks=checks)

    # -- check 1: reconciliation ---------------------------------------------

    def _check_reconciliation(self, d1: Deliverable1) -> DebtStructureValidatorCheck:
        """Sum of drawn tranche principals matches reported total indebtedness.

        Only DRAWN principal counts. Undrawn revolver limits and intercompany
        borrowings are excluded (see check 5). Debt issuance costs/discounts are
        presentation adjustments, not tranches, and are not reconstructed here.
        """
        reported = d1.creditor_priority_framework.consolidated_total_indebtedness_m
        notes_sum = sum(t.principal_amount_m for t in d1.senior_notes_schedule)
        non_note_sum = sum(
            o.principal_drawn_m
            for o in d1.non_note_obligations_schedule
            if not _is_intercompany(o.obligation_facility)
        )
        tranche_sum = notes_sum + non_note_sum
        delta = abs(tranche_sum - reported)
        passed = delta <= self._recon_tol
        return DebtStructureValidatorCheck(
            check_id=CHECK_RECONCILIATION,
            name="Debt tranche reconciliation to reported total indebtedness",
            passed=passed,
            detail=(
                f"Sum of drawn tranches {tranche_sum:,.3f}M vs reported total "
                f"{reported:,.3f}M (delta {delta:,.3f}M, tolerance "
                f"{self._recon_tol:,.3f}M)."
            ),
            observed={
                "senior_notes_sum_m": notes_sum,
                "non_note_drawn_sum_m": non_note_sum,
                "tranche_sum_m": tranche_sum,
                "reported_total_m": reported,
                "delta_m": delta,
            },
        )

    # -- check 2: subordination ratio ----------------------------------------

    def _check_subordination_ratio(self, d1: Deliverable1) -> DebtStructureValidatorCheck:
        """Recompute Tier1 direct OpCo secured / consolidated total indebtedness."""
        f = d1.creditor_priority_framework
        total = f.consolidated_total_indebtedness_m
        if total <= 0:
            return DebtStructureValidatorCheck(
                check_id=CHECK_SUBORDINATION_RATIO,
                name="Structural subordination drag ratio recomputation",
                passed=False,
                detail="Consolidated total indebtedness is non-positive; ratio undefined.",
                observed={"consolidated_total_m": total},
            )
        recomputed = f.tier_1_direct_opco_secured_m / total
        claimed = f.structural_subordination_drag_ratio
        delta = abs(recomputed - claimed)
        passed = delta <= self._ratio_tol
        return DebtStructureValidatorCheck(
            check_id=CHECK_SUBORDINATION_RATIO,
            name="Structural subordination drag ratio recomputation",
            passed=passed,
            detail=(
                f"Recomputed drag ratio {recomputed:.4f} vs claimed {claimed:.4f} "
                f"(delta {delta:.4f}, tolerance {self._ratio_tol})."
            ),
            observed={
                "tier_1_m": f.tier_1_direct_opco_secured_m,
                "consolidated_total_m": total,
                "recomputed_ratio": recomputed,
                "claimed_ratio": claimed,
                "delta": delta,
            },
        )

    # -- check 3: maturity order ---------------------------------------------

    def _check_maturity_order(
        self, schedule: list[SeniorNoteTranche]
    ) -> DebtStructureValidatorCheck:
        """Senior-note maturities must be listed in chronological order."""
        years = [t.maturity_year for t in schedule]
        ordered = all(a <= b for a, b in zip(years, years[1:]))
        first_violation = None
        for i in range(1, len(years)):
            if years[i] < years[i - 1]:
                first_violation = {
                    "index": i,
                    "prev_year": years[i - 1],
                    "year": years[i],
                }
                break
        return DebtStructureValidatorCheck(
            check_id=CHECK_MATURITY_ORDER,
            name="Senior-note maturity chronological ordering",
            passed=ordered,
            detail=(
                "Maturity years are in chronological order."
                if ordered
                else f"Maturity ordering violated at index {first_violation['index']}: "
                f"{first_violation['prev_year']} then {first_violation['year']}."
            ),
            observed={"maturity_years": years, "first_violation": first_violation},
        )

    # -- check 4: legal entity ------------------------------------------------

    def _check_legal_entity(self, d1: Deliverable1) -> DebtStructureValidatorCheck:
        """Every tranche names a legal borrower with coherent credit support.

        Fails if any tranche omits a legal borrower, or names the SAME entity as
        both its only borrower and its only credit support (which would collapse
        the OpCo-issuer vs HoldCo-guarantor distinction the tier framework relies
        on). A guarantee by the parent of its own subsidiary's debt must name two
        distinct entities.
        """
        problems: list[str] = []
        tranches: list[tuple[str, str, str]] = [
            (t.senior_note_series, t.legal_borrower, t.guarantors_credit_support)
            for t in d1.senior_notes_schedule
        ] + [
            (o.obligation_facility, o.legal_borrower, o.guarantors_credit_support)
            for o in d1.non_note_obligations_schedule
        ]
        for name, borrower, support in tranches:
            if not borrower.strip():
                problems.append(f"{name!r}: missing legal borrower.")
                continue
            # A guaranteed tranche that names the parent as support must name a
            # distinct borrower; same-entity on both sides is incoherent.
            support_entities = _distinct_entities(support)
            if support_entities and borrower.strip() in support_entities and len(
                support_entities
            ) == 1:
                problems.append(
                    f"{name!r}: borrower and sole credit support are the same entity "
                    f"({borrower!r}); OpCo/HoldCo distinction collapses."
                )
        passed = not problems
        return DebtStructureValidatorCheck(
            check_id=CHECK_LEGAL_ENTITY,
            name="Legal entity / borrower-guarantor attribution",
            passed=passed,
            detail=(
                "Every tranche names a legal borrower with coherent credit support."
                if passed
                else " ".join(problems)
            ),
            observed={"problem_count": len(problems), "problems": problems},
        )

    # -- check 5: double-counting --------------------------------------------

    def _check_double_counting(self, d1: Deliverable1) -> DebtStructureValidatorCheck:
        """Undrawn revolver limits and intercompany borrowings stay out of totals.

        The reconciled total must not have absorbed (a) available (undrawn)
        facility limits or (b) intercompany borrowings. We confirm the reported
        total is consistent with DRAWN principal only: adding the undrawn
        headroom would overshoot the reported total beyond tolerance.
        """
        f = d1.creditor_priority_framework
        reported = f.consolidated_total_indebtedness_m
        undrawn_headroom = _undrawn_headroom(d1.non_note_obligations_schedule)
        intercompany = sum(
            o.principal_drawn_m
            for o in d1.non_note_obligations_schedule
            if _is_intercompany(o.obligation_facility)
        )
        drawn_total = sum(
            t.principal_amount_m for t in d1.senior_notes_schedule
        ) + sum(
            o.principal_drawn_m
            for o in d1.non_note_obligations_schedule
            if not _is_intercompany(o.obligation_facility)
        )
        inflated_total = drawn_total + undrawn_headroom + intercompany
        # Pass when the reported total matches the DRAWN total, not the inflated
        # one. If undrawn/intercompany were erroneously included, reported would
        # track the inflated figure instead.
        drawn_delta = abs(reported - drawn_total)
        includes_undrawn_or_intercompany = (
            (undrawn_headroom + intercompany) > self._recon_tol
            and abs(reported - inflated_total) <= self._recon_tol
            and drawn_delta > self._recon_tol
        )
        passed = drawn_delta <= self._recon_tol and not includes_undrawn_or_intercompany
        return DebtStructureValidatorCheck(
            check_id=CHECK_DOUBLE_COUNTING,
            name="No double-counting of undrawn limits / intercompany debt",
            passed=passed,
            detail=(
                f"Reported total {reported:,.3f}M tracks drawn principal "
                f"{drawn_total:,.3f}M; excluded undrawn headroom "
                f"{undrawn_headroom:,.3f}M and intercompany {intercompany:,.3f}M."
                if passed
                else f"Reported total {reported:,.3f}M appears to include undrawn "
                f"headroom {undrawn_headroom:,.3f}M and/or intercompany "
                f"{intercompany:,.3f}M (drawn-only total {drawn_total:,.3f}M)."
            ),
            observed={
                "reported_total_m": reported,
                "drawn_total_m": drawn_total,
                "undrawn_headroom_m": undrawn_headroom,
                "intercompany_m": intercompany,
                "inflated_total_m": inflated_total,
            },
        )


def _undrawn_headroom(obligations: list[NonNoteDebtObligation]) -> float:
    """Sum of available (undrawn) facility limit above drawn principal."""
    total = 0.0
    for o in obligations:
        if o.available_facility_limit_m is None:
            continue
        headroom = o.available_facility_limit_m - o.principal_drawn_m
        if headroom > 0:
            total += headroom
    return total


def _distinct_entities(credit_support: str) -> set[str]:
    """Best-effort split of a credit-support string into entity names.

    Splits on common separators (``and``, ``,``, ``;``, ``&``) and trims noise
    words so "fully and unconditionally guaranteed by HCA Healthcare, Inc."
    yields the guarantor entity. Deterministic and purely textual.
    """
    text = credit_support.strip()
    if not text:
        return set()
    # Remove common guarantee phrasing so the residual is the entity name(s).
    for phrase in (
        "fully and unconditionally guaranteed by",
        "unconditionally guaranteed by",
        "guaranteed on an unsecured basis by",
        "guaranteed by",
        "credit support from",
        "guarantor:",
        "guarantors:",
    ):
        text = re.sub(re.escape(phrase), ",", text, flags=re.IGNORECASE)
    parts = re.split(r"\band\b|,|;|&", text, flags=re.IGNORECASE)
    entities = {p.strip() for p in parts if p.strip()}
    # Drop pure-noise fragments.
    noise = {"none", "n/a", "na", "unsecured basis", "basis"}
    return {e for e in entities if e.lower() not in noise}
