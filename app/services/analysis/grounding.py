"""Claim-grounding evaluator: citation presence vs entailment (task 6.5).

The HEADLINE guarantee of Milestone 6 (Req 13.6-13.8): a claim is NEVER treated
as grounded merely because a citation ID is attached. Two INDEPENDENT signals
are computed for every claim:

* **Check A -- citation presence.** Is ``evidence_ids`` non-empty AND does every
  cited ID reference a real evidence object in the supplied evidence index?
  This is a pure boolean about the presence of a citation.
* **Check B -- entailment.** Does the cited evidence actually SUPPORT the claim?
  This is a separate judgment yielding one of ``supported | partially_supported
  | unsupported | contradictory | not_verifiable`` (Req 13.7).

``ClaimGroundingResult.is_grounded`` is True ONLY when a citation is present AND
entailment is ``supported``. A claim with a citation but failing entailment
comes out ``unsupported``/``contradictory``/``not_verifiable`` -- NOT grounded.

Entailment uses DETERMINISTIC checks where possible (Req 13.8): numeric claims
are checked against the deterministic metric/fact value in the evidence index; a
contradicting value yields ``contradictory``. An optional second structured LLM
judge MAY be supplied for narrative claims; manual adjudication is available for
high-severity factual claims (``adjudicate``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.orm import ClaimGrounding as ClaimGroundingRow
from app.schemas.llm import (
    AnalysisResponse,
    AnalyticalClaim,
    ClaimGroundingResult,
    EntailmentState,
    JudgeKind,
)

# A pluggable structured LLM judge: (claim, evidence) -> EntailmentState.
LLMJudge = Callable[[AnalyticalClaim, list[dict]], EntailmentState]


@dataclass
class EvidenceItem:
    """A single supplied evidence object available for grounding.

    ``value`` is the authoritative numeric value for numeric evidence (a fact or
    deterministic metric); ``text`` carries the statement for qualitative
    evidence. ``supports`` optionally pre-states the deterministic verdict for a
    narrative item (used when no numeric check applies).
    """

    evidence_id: str
    value: float | None = None
    text: str | None = None
    supports: EntailmentState | None = None
    unit: str | None = None


class EvidenceIndex:
    """Lookup of supplied evidence objects by id (for citation-presence check)."""

    def __init__(self, items: list[EvidenceItem]) -> None:
        self._items = {item.evidence_id: item for item in items}

    def exists(self, evidence_id: str) -> bool:
        return evidence_id in self._items

    def get(self, evidence_id: str) -> EvidenceItem | None:
        return self._items.get(evidence_id)

    def all_present(self, evidence_ids: list[str]) -> bool:
        return bool(evidence_ids) and all(self.exists(e) for e in evidence_ids)


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_number(text: str) -> float | None:
    """Return the first numeric literal in ``text`` (e.g. a claimed figure)."""
    match = _NUMBER_RE.search(text.replace(",", ""))
    return float(match.group()) if match else None


class GroundingEvaluator:
    """Compute the two-part grounding verdict for analysis claims (Req 13.6-13.8)."""

    def __init__(
        self,
        evidence: EvidenceIndex,
        *,
        llm_judge: LLMJudge | None = None,
        session: Session | None = None,
        case_id: str | None = None,
        numeric_tolerance: float = 1e-9,
    ) -> None:
        self._evidence = evidence
        self._llm_judge = llm_judge
        self._session = session
        self._case_id = case_id
        self._tol = numeric_tolerance

    # -- the two independent checks -------------------------------------------

    def citation_present(self, claim: AnalyticalClaim) -> bool:
        """Check A: non-empty citation AND every id references real evidence."""
        return self._evidence.all_present(claim.evidence_ids)

    def entailment(self, claim: AnalyticalClaim) -> tuple[EntailmentState, JudgeKind]:
        """Check B: does the cited evidence SUPPORT the claim? (independent).

        Deterministic first: if the claim text states a number and a cited
        numeric evidence object exists, compare them -- match => ``supported``,
        mismatch => ``contradictory``. Pre-stated ``supports`` on an evidence
        item is honoured for narrative items. Falls back to an optional LLM
        judge, else ``not_verifiable``.
        """
        # No real citation at all -> cannot be entailed by cited evidence.
        if not self._evidence.all_present(claim.evidence_ids):
            return EntailmentState.NOT_VERIFIABLE, JudgeKind.DETERMINISTIC

        items = [self._evidence.get(e) for e in claim.evidence_ids]
        items = [i for i in items if i is not None]

        # Deterministic numeric entailment where the claim asserts a figure.
        numbers = list(_NUMBER_RE.finditer(claim.text.replace(",", "")))
        claimed = _extract_number(claim.text)
        numeric_items = [i for i in items if i.value is not None]
        if claimed is not None and numeric_items:
            # No number-to-evidence linkage exists in this schema. Multiple
            # assertions or candidates cannot be deterministically entailed.
            if len(numbers) != 1 or len(numeric_items) != 1:
                return EntailmentState.NOT_VERIFIABLE, JudgeKind.DETERMINISTIC
            item = numeric_items[0]
            text = claim.text.replace(",", "")
            before = text[: numbers[0].start()].rstrip()
            after = text[numbers[0].end() :].lstrip().lower()
            if after.startswith("%") or after.startswith("percent"):
                if item.unit not in {"ratio", "fraction", "percent"}:
                    return EntailmentState.NOT_VERIFIABLE, JudgeKind.DETERMINISTIC
                if item.unit != "percent":
                    claimed /= 100
            elif before.endswith("$") or before.upper().endswith("USD"):
                if item.unit not in {
                    "USD",
                    "USD_million",
                    "USD_millions",
                    "USD_billion",
                }:
                    return EntailmentState.NOT_VERIFIABLE, JudgeKind.DETERMINISTIC
                scale = (
                    1e9
                    if after.startswith("billion")
                    else 1e6
                    if after.startswith("million")
                    else 1
                )
                target = (
                    1e9
                    if item.unit == "USD_billion"
                    else 1e6
                    if item.unit in {"USD_million", "USD_millions"}
                    else 1
                )
                claimed *= scale / target
            elif after.startswith("x") and item.unit not in {None, "x", "ratio"}:
                return EntailmentState.NOT_VERIFIABLE, JudgeKind.DETERMINISTIC
            if abs(item.value - claimed) <= self._tol:
                return EntailmentState.SUPPORTED, JudgeKind.DETERMINISTIC
            return EntailmentState.CONTRADICTORY, JudgeKind.DETERMINISTIC

        # Deterministic narrative verdict pre-stated on the evidence item.
        stated = [i.supports for i in items if i.supports is not None]
        if stated:
            # Any contradiction dominates; else the weakest stated support.
            if EntailmentState.CONTRADICTORY in stated:
                return EntailmentState.CONTRADICTORY, JudgeKind.DETERMINISTIC
            if all(s is EntailmentState.SUPPORTED for s in stated):
                return EntailmentState.SUPPORTED, JudgeKind.DETERMINISTIC
            if EntailmentState.UNSUPPORTED in stated:
                return EntailmentState.UNSUPPORTED, JudgeKind.DETERMINISTIC
            return EntailmentState.PARTIALLY_SUPPORTED, JudgeKind.DETERMINISTIC

        # Optional second structured LLM judge for narrative claims (Req 13.8).
        if self._llm_judge is not None:
            evidence_payload = [
                {"evidence_id": i.evidence_id, "text": i.text, "value": i.value}
                for i in items
            ]
            return self._llm_judge(claim, evidence_payload), JudgeKind.LLM

        return EntailmentState.NOT_VERIFIABLE, JudgeKind.DETERMINISTIC

    # -- combined verdict -----------------------------------------------------

    def evaluate_claim(self, claim: AnalyticalClaim) -> ClaimGroundingResult:
        """Combine the two independent checks into a grounding verdict.

        ``citation_present`` and ``entailment_state`` are stored SEPARATELY. A
        claim is grounded only if BOTH signals pass; a citation alone is never
        sufficient (Req 13.8).
        """
        cite = self.citation_present(claim)
        state, judge = self.entailment(claim)
        detail = (
            f"citation_present={cite}; entailment={state.value}; judge={judge.value}."
        )
        return ClaimGroundingResult(
            claim_id=claim.claim_id,
            citation_present=cite,
            entailment_state=state,
            evidence_refs=list(claim.evidence_ids),
            judge=judge,
            detail=detail,
        )

    def evaluate_analysis(
        self, analysis: AnalysisResponse
    ) -> list[ClaimGroundingResult]:
        """Evaluate every claim in an analysis (non-destructive; read-only)."""
        return [self.evaluate_claim(c) for c in analysis.all_claims()]

    # -- persistence + manual adjudication ------------------------------------

    def persist(self, result: ClaimGroundingResult) -> ClaimGroundingRow:
        """Persist a grounding result row."""
        if self._session is None:
            raise ValueError("GroundingEvaluator.persist requires a session.")
        row = ClaimGroundingRow(
            case_id=self._case_id,
            claim_id=result.claim_id,
            citation_present=result.citation_present,
            entailment_state=result.entailment_state.value,
            evidence_refs=list(result.evidence_refs),
            judge=result.judge.value,
            adjudicated=result.adjudicated,
            detail=result.detail,
        )
        self._session.add(row)
        self._session.flush()
        return row

    @staticmethod
    def adjudicate(
        result: ClaimGroundingResult,
        *,
        entailment_state: EntailmentState,
        detail: str | None = None,
    ) -> ClaimGroundingResult:
        """Human adjudication of a high-severity factual claim (Req 13.8).

        Returns a NEW result marked ``adjudicated`` with ``judge=human``; the
        original verdict is not mutated in place.
        """
        return ClaimGroundingResult(
            claim_id=result.claim_id,
            citation_present=result.citation_present,
            entailment_state=entailment_state,
            evidence_refs=list(result.evidence_refs),
            judge=JudgeKind.HUMAN,
            adjudicated=True,
            detail=detail or "Human-adjudicated grounding verdict.",
        )
