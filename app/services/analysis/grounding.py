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
    # Trusted metadata for an approved metric/fact (from the deterministic
    # engine / canonical evidence). Used by consumers that must validate a claim
    # concerns the SAME concept/entity/period/scale/currency/basis before a
    # number match can count as grounding (Task 6C). ``None`` means "unknown";
    # a consumer requiring the attribute must then fail closed.
    concept: str | None = None
    entity: str | None = None
    period: str | None = None
    scale: str | None = None
    currency: str | None = None
    accounting_basis: str | None = None


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


# -- verbatim reported-fact support (BO roster v3, fix R1) ---------------------

_VERBATIM_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "their this to was were which with our we us ours they them these those such "
    "who whose".split())
_NEGATIONS = frozenset({"not", "no", "never", "nor", "without"})
_MIN_VERBATIM_CHARS = 20


def _norm(text: str) -> str:
    text = (text or "").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def _content(text: str) -> tuple[set[str], set[str], set[str]]:
    """(stemmed content words, numbers, negations) of ``text``."""
    words = re.findall(r"[a-z][a-z'-]+", _norm(text))
    stems = {w[:6] for w in words if w not in _VERBATIM_STOP and w not in _NEGATIONS}
    numbers = set(re.findall(r"\d+(?:\.\d+)?", _norm(text).replace(",", "")))
    return stems, numbers, {w for w in words if w in _NEGATIONS}


def _drop_aliases(text: str, aliases) -> str:
    """Remove the borrower's own names: a filing calls itself "we / us / our"."""
    for alias in sorted((a for a in aliases or () if a and len(a) >= 2),
                        key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z]){re.escape(alias)}(?:['\u2019]s)?(?![A-Za-z])",
                      " ", text, flags=re.I)
    return text


def verbatim_fact_support(statement: str, quote: str | None, passage_text: str,
                          *, aliases=()) -> bool:
    """True when ``quote`` is verbatim in the passage AND covers ``statement``.

    Establishes SOURCE AUTHENTICITY of a restated fact, nothing more: every
    content word (light stem), every number and every negation in the statement
    must appear in the verbatim quote. A paraphrase that adds a word, a figure or
    drops/adds a negation does not qualify. Never use it for an inference or an
    outcome: a genuine quotation alone does not establish an inferential step.
    """
    if not quote or len(_norm(quote)) < _MIN_VERBATIM_CHARS:
        return False
    if _norm(quote) not in _norm(passage_text):
        return False
    # Fix R1a: the borrower's names are equivalent to the filing's "we/us/our"
    # (both are dropped); every other content word, figure and negation counts.
    s_words, s_nums, s_neg = _content(_drop_aliases(statement, aliases))
    q_words, q_nums, q_neg = _content(quote)
    return bool(s_words) and s_words <= q_words and s_nums <= q_nums and s_neg == q_neg


def judged_support_guard(premise: str, quote: str | None, passage_text: str,
                         *, aliases=(), repair=None) -> tuple[bool, str]:
    """Deterministic checks on an entailment judge's evidence (fix R5).

    The judge may accept a paraphrase, but its supporting sentence must be
    verbatim in the passage, and every figure and every negation of the premise
    must appear in that sentence. Returns ``(accepted, reason)``.
    """
    if not quote or len(_norm(quote)) < _MIN_VERBATIM_CHARS:
        return False, "no supporting sentence"
    if _norm(quote) not in _norm(passage_text):
        # One layout-gap repair (e.g. a page number inside the sentence), if a
        # repair hook is supplied; the repaired text must be in the passage.
        repaired = repair(quote) if repair is not None else None
        if not repaired or _norm(repaired) not in _norm(passage_text):
            return False, "supporting sentence not verbatim in the passage"
        quote = repaired
    _, p_nums, p_neg = _content(_drop_aliases(premise, aliases))
    _, q_nums, q_neg = _content(quote)
    if q_nums and not p_nums:
        # A premise with no figures read off a sentence with figures is a
        # judgement about those figures ("most", "high", "majority"): that is a
        # calculation, which belongs to deterministic code, not to the judge.
        return False, "premise judges figures it does not state (calculation, not a reading)"
    if not p_nums <= q_nums:
        return False, f"figures {sorted(p_nums - q_nums)} not in the supporting sentence"
    if p_neg != q_neg:
        return False, "negation differs between premise and supporting sentence"
    return True, "accepted"
