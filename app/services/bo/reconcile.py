"""Deterministic cross-agent reconciliation of validated hypotheses.

Detects, without an LLM:

* duplicate groups — substantively the same finding raised by different
  specialists (similar wording, or moderately similar wording over shared
  evidence);
* contradictions — specialists reading the same matter in opposite credit
  directions, or one supporting what another rejected;
* overlapping evidence — passages cited by more than one specialist;
* disconfirming evidence — hypotheses that carry their own counter-evidence.

The orchestrator receives this as structure to merge and to keep visible; the
orchestrator-output validator uses the duplicate groups to stop one risk being
counted twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.hashing import content_hash
from app.schemas.bo import Direction, Disposition, HypothesisRecord

DUPLICATE_SIMILARITY = 0.6
SHARED_EVIDENCE_SIMILARITY = 0.35
CONTRADICTION_SIMILARITY = 0.35

_STOP = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "their this to was were which with may can could would will not no than "
    "company borrower business".split()
)
_RANK = {"high": 0, "medium": 1, "low": 2}
_CONF = {"high": 0, "medium": 1, "medium_low": 2, "low": 3}


def tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z][a-z-]{2,}", (text or "").lower())
    return frozenset(w.rstrip("s") for w in words if w not in _STOP)


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


@dataclass
class Reconciliation:
    duplicate_groups: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    overlapping_evidence: list[dict[str, Any]] = field(default_factory=list)
    disconfirming: list[dict[str, Any]] = field(default_factory=list)

    def group_of(self, hypothesis_id: str) -> str | None:
        for group in self.duplicate_groups:
            if hypothesis_id in group["hypothesis_ids"]:
                return group["group_id"]
        return None

    def as_payload(self) -> dict[str, Any]:
        return {
            "duplicate_groups": self.duplicate_groups,
            "contradictions": self.contradictions,
            "overlapping_evidence": self.overlapping_evidence,
            "disconfirming": self.disconfirming,
        }


def _opposed(a: HypothesisRecord, b: HypothesisRecord) -> bool:
    return {a.direction, b.direction} == {Direction.POSITIVE, Direction.NEGATIVE}


def reconcile(usable: list[HypothesisRecord],
              rejected: list[HypothesisRecord] | None = None) -> Reconciliation:
    out = Reconciliation()
    items = sorted(usable, key=lambda h: h.hypothesis_id)
    parent = {h.hypothesis_id: h.hypothesis_id for h in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.agent_id == b.agent_id:
                continue
            score = similarity(a.claim, b.claim)
            shared = sorted(set(a.evidence_ids()) & set(b.evidence_ids()))
            related = score >= DUPLICATE_SIMILARITY or (
                shared and score >= SHARED_EVIDENCE_SIMILARITY)
            if _opposed(a, b) and (
                    related or (shared and score >= CONTRADICTION_SIMILARITY)):
                out.contradictions.append({
                    "contradiction_id": "con_" + content_hash(
                        [a.hypothesis_id, b.hypothesis_id])[:12],
                    "kind": "opposite_direction",
                    "hypothesis_ids": [a.hypothesis_id, b.hypothesis_id],
                    "agent_ids": [a.agent_id, b.agent_id],
                    "shared_evidence_ids": shared,
                    "similarity": round(score, 3),
                })
            elif related:
                parent[find(b.hypothesis_id)] = find(a.hypothesis_id)

    for a in items:
        for r in rejected or []:
            if a.agent_id == r.agent_id or a.direction is not r.direction:
                continue
            score = similarity(a.claim, r.claim)
            if score >= DUPLICATE_SIMILARITY:
                out.contradictions.append({
                    "contradiction_id": "con_" + content_hash(
                        [a.hypothesis_id, r.hypothesis_id])[:12],
                    "kind": "supported_vs_rejected",
                    "hypothesis_ids": [a.hypothesis_id, r.hypothesis_id],
                    "agent_ids": [a.agent_id, r.agent_id],
                    "shared_evidence_ids": sorted(
                        set(a.evidence_ids()) & set(r.evidence_ids())),
                    "similarity": round(score, 3),
                })

    groups: dict[str, list[HypothesisRecord]] = {}
    for h in items:
        groups.setdefault(find(h.hypothesis_id), []).append(h)
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = min(members, key=lambda h: (
            _RANK[h.materiality.value], _CONF[h.confidence.value], h.hypothesis_id))
        ids = sorted(h.hypothesis_id for h in members)
        shared = set(members[0].evidence_ids())
        for h in members[1:]:
            shared &= set(h.evidence_ids())
        out.duplicate_groups.append({
            "group_id": "dup_" + content_hash(ids)[:12],
            "hypothesis_ids": ids,
            "agent_ids": sorted({h.agent_id for h in members}),
            "canonical_hypothesis_id": canonical.hypothesis_id,
            "shared_evidence_ids": sorted(shared),
        })
    out.duplicate_groups.sort(key=lambda g: g["group_id"])
    out.contradictions.sort(key=lambda c: c["contradiction_id"])

    cited: dict[str, set[str]] = {}
    agents: dict[str, set[str]] = {}
    for h in items:
        for eid in h.evidence_ids():
            cited.setdefault(eid, set()).add(h.hypothesis_id)
            agents.setdefault(eid, set()).add(h.agent_id)
    out.overlapping_evidence = [
        {"evidence_id": eid, "hypothesis_ids": sorted(cited[eid]),
         "agent_ids": sorted(agents[eid])}
        for eid in sorted(cited) if len(agents[eid]) > 1
    ]
    out.disconfirming = [
        {"hypothesis_id": h.hypothesis_id, "agent_id": h.agent_id,
         "disposition": h.disposition.value,
         "contradicting_evidence_ids": list(h.contradicting_evidence_ids)}
        for h in items
        if h.contradicting_evidence_ids
        and h.disposition is not Disposition.REJECTED
    ]
    return out
