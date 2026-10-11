"""Bounded, recorded evidence retrieval over ADMITTED narrative passages.

The retriever never fetches a document and never adds anything to the
``CanonicalEvidenceSnapshot``: it searches the passages the existing
``EvidenceRouter`` already selected for the agent from that snapshot. Each query
of the agent's declared plan (and each bounded agent-requested follow-up) is one
:class:`RetrievalAttempt`; a query with no result is recorded as a retrieval gap.

Deterministic: same snapshot + same plan => same passages in the same order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.hashing import content_hash
from app.schemas.bo import RetrievalAttempt
from app.services.bo.plans import PURPOSES, BoAgentSpec, RetrievalQuery

# Phrases that read as instructions to a model. A passage containing one is
# still evidence, but it is flagged so reviewers can see it; nothing in the
# pipeline derives control flow from passage text.
_INSTRUCTION_LIKE = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) instructions|"
    r"disregard (?:the|all|any) (?:above|previous|prior)|you are now|"
    r"system prompt|as an ai\b|respond only with|output the following json|"
    r"assign (?:a |the )?(?:score|rating|band)",
    re.I,
)


TABLE_CELL_RATIO = 40  # more than one cell separator per 40 characters
TABLE_PENALTY = 150


@dataclass(frozen=True)
class RetrievalBudget:
    """Configurable evidence budget (no unbounded search, no whole-filing dump)."""

    max_passages_per_agent: int = 16
    max_chars_per_agent: int = 36_000
    max_followup_rounds: int = 1
    max_followup_requests: int = 3
    max_followup_terms: int = 6
    max_followup_passages: int = 2


@dataclass
class RetrievalResult:
    passages: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[RetrievalAttempt] = field(default_factory=list)

    @property
    def evidence_ids(self) -> list[str]:
        return sorted(p["evidence_id"] for p in self.passages)

    @property
    def gaps(self) -> list[RetrievalAttempt]:
        return [a for a in self.attempts if a.is_gap]


@dataclass(frozen=True)
class _CoverageQuery:
    query_id: str
    terms: tuple[str, ...]
    max_passages: int
    purpose: str = "coverage"


def instruction_like(text: str) -> bool:
    return bool(_INSTRUCTION_LIKE.search(text or ""))


def _reference(passage: dict[str, Any], score: int, matched: list[str]) -> dict[str, Any]:
    return {
        "evidence_id": passage["evidence_id"],
        "document_id": passage.get("document_id"),
        "section": passage.get("section"),
        "heading": passage.get("heading"),
        "location": passage.get("location"),
        "page": passage.get("page"),
        "sec_accession": passage.get("sec_accession"),
        "score": score,
        "matched_terms": matched,
    }


def _compile(terms) -> list[tuple[str, re.Pattern]]:
    compiled = []
    for term in terms:
        try:
            compiled.append((term, re.compile(term, re.I)))
        except re.error:
            compiled.append((term, re.compile(re.escape(term), re.I)))
    return compiled


def _search(passages, terms, limit):
    """Rank passages by distinct terms matched, then total hits, then id."""
    compiled = _compile(terms)
    ranked = []
    for passage in passages:
        text = passage.get("text") or ""
        hits = {term: len(pattern.findall(text)) for term, pattern in compiled}
        matched = sorted(t for t, n in hits.items() if n)
        if matched:
            score = len(matched) * 100 + min(sum(hits.values()), 99)
            # A flattened financial table matches generic words by accident;
            # rank it below prose that matches the same number of terms.
            if text.count("|") * TABLE_CELL_RATIO > len(text):
                score -= TABLE_PENALTY
            ranked.append((score, passage, matched))
    ranked.sort(key=lambda r: (-r[0], *_position(r[1])))
    return ranked[:limit]


def _position(passage: dict[str, Any]) -> tuple:
    """Document order. Evidence ids are case-specific hashes, so ties are broken
    by where the passage sits in the filing: the same filing then yields the
    same packet under any case id."""
    return (str(passage.get("sec_accession") or ""), passage.get("location") or 0,
            passage["evidence_id"])


class BoEvidenceRetriever:
    """Execute a specialist's retrieval plan over its routed passages."""

    def __init__(self, budget: RetrievalBudget | None = None) -> None:
        self.budget = budget or RetrievalBudget()

    def run_plan(
        self,
        spec: BoAgentSpec,
        routed_passages: list[dict[str, Any]],
        *,
        analysis_run_id: str,
    ) -> RetrievalResult:
        result = RetrievalResult()
        selected: dict[str, dict[str, Any]] = {}
        for query in spec.plan:
            self._execute(
                query, routed_passages, result, selected,
                agent_id=spec.agent_id, analysis_run_id=analysis_run_id,
                origin="plan", round_index=0,
            )
        result.passages = self._within_budget(selected)
        return result

    def follow_up(
        self,
        spec: BoAgentSpec,
        requests: list[dict[str, Any]],
        searchable_passages: list[dict[str, Any]],
        already: RetrievalResult,
        *,
        analysis_run_id: str,
        round_index: int = 1,
    ) -> RetrievalResult:
        """One bounded round of agent-requested searches (admitted material only)."""
        result = RetrievalResult(passages=list(already.passages))
        selected = {p["evidence_id"]: p for p in already.passages}
        seen = set(selected)
        new: dict[str, dict[str, Any]] = {}
        for index, request in enumerate(requests[: self.budget.max_followup_requests]):
            purpose = request.get("purpose")
            terms = [t for t in (request.get("terms") or []) if isinstance(t, str) and t.strip()]
            if purpose not in PURPOSES or not terms:
                continue
            query = RetrievalQuery(
                f"{spec.code.lower()}.followup.{index + 1}", purpose,
                tuple(terms[: self.budget.max_followup_terms]),
                self.budget.max_followup_passages,
            )
            self._execute(
                query, [p for p in searchable_passages if p["evidence_id"] not in seen],
                result, new, agent_id=spec.agent_id,
                analysis_run_id=analysis_run_id, origin="agent_request",
                round_index=round_index, hypothesis_id=request.get("hypothesis_id"),
            )
        # Follow-up passages are bounded by the follow-up limits themselves
        # (requests x passages), on top of the plan packet.
        for evidence_id, passage in new.items():
            selected.setdefault(evidence_id, passage)
        result.passages = sorted(selected.values(), key=lambda p: p["evidence_id"])
        return result

    def run_checklist(self, spec: BoAgentSpec, checks, corpus: list[dict[str, Any]],
                      already: RetrievalResult, *, analysis_run_id: str
                      ) -> tuple[RetrievalResult, dict[str, list[str]]]:
        """Guarantee that the passages behind each coverage question reach the agent.

        Searches the whole ADMITTED narrative for each check assigned to this
        specialist and adds its top passages on top of the plan packet (bounded
        by checks x passages). Returns the extended result and, per check, the
        evidence ids located (empty = the filing has nothing on it).
        """
        result = RetrievalResult(passages=list(already.passages),
                                 attempts=list(already.attempts))
        selected = {p["evidence_id"]: p for p in already.passages}
        located: dict[str, list[str]] = {}
        for check in checks:
            if spec.code not in check.agent_codes:
                continue
            query = _CoverageQuery(f"check:{check.check_id}", tuple(check.terms),
                                   check.max_passages)
            before = len(result.attempts)
            self._execute(query, corpus, result, selected, agent_id=spec.agent_id,
                          analysis_run_id=analysis_run_id, origin="checklist",
                          round_index=0)
            located[check.check_id] = [
                r["evidence_id"] for r in result.attempts[before].results]
        result.passages = sorted(selected.values(), key=lambda p: _position(p))
        return result, located

    # -- internals ------------------------------------------------------------

    def _execute(self, query, passages, result, selected, *, agent_id,
                 analysis_run_id, origin, round_index, hypothesis_id=None):
        ranked = _search(passages, query.terms, query.max_passages)
        references = []
        for score, passage, matched in ranked:
            entry = selected.setdefault(passage["evidence_id"], dict(passage))
            entry.setdefault("retrieved_for", [])
            if query.purpose not in entry["retrieved_for"]:
                entry["retrieved_for"].append(query.purpose)
            entry["instruction_like"] = instruction_like(passage.get("text") or "")
            references.append(_reference(passage, score, matched))
        result.attempts.append(
            RetrievalAttempt(
                attempt_id="ra_" + content_hash(
                    [analysis_run_id, agent_id, query.query_id, round_index,
                     list(query.terms)]
                )[:20],
                analysis_run_id=analysis_run_id,
                agent_id=agent_id,
                query_id=query.query_id,
                purpose=query.purpose,
                terms=list(query.terms),
                origin=origin,
                status="performed" if references else "no_results",
                passages_searched=len(passages),
                results=references,
                hypothesis_id=hypothesis_id,
                round_index=round_index,
            )
        )

    def _within_budget(self, selected: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep passages serving the most purposes first, inside the budget."""
        ordered = sorted(
            selected.values(),
            key=lambda p: (-len(p.get("retrieved_for", [])), *_position(p)),
        )
        kept, chars = [], 0
        for passage in ordered:
            size = len(passage.get("text") or "")
            if len(kept) >= self.budget.max_passages_per_agent:
                break
            if kept and chars + size > self.budget.max_chars_per_agent:
                continue
            kept.append(passage)
            chars += size
        return sorted(kept, key=lambda p: p["evidence_id"])


def packet_view(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The bounded passage fields an agent receives (source-located, as data)."""
    return [
        {
            "evidence_id": p["evidence_id"],
            "document_id": p.get("document_id"),
            "section": p.get("section"),
            "heading": p.get("heading"),
            "location": p.get("location"),
            "retrieved_for": sorted(p.get("retrieved_for", [])),
            "instruction_like": bool(p.get("instruction_like")),
            "text": p.get("text"),
        }
        for p in passages
    ]
