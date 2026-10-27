"""Evidence citations: short aliases for the model, and per-citation repair.

Evidence ids are 64-character content hashes. A model that must retype them
occasionally mistypes one character, and a single unknown id used to reject a
specialist's entire answer. Two safeguards, both deterministic:

* :func:`alias_inputs` / :func:`unalias_answer` -- the model sees and cites short
  labels (``E01``, ``E02``, ...) that the application maps back to the real ids
  before any validation. The mapping is derived from the request's own
  ``evidence_ids`` order, so a recorded answer replays to the same ids.
* :func:`repair_citations` -- an id still not admitted for this agent is
  corrected when it is an unambiguous near-miss of an admitted id, otherwise the
  single citation is removed and noted. The validators then judge what remains:
  a hypothesis left without support is downgraded by the existing rules rather
  than discarding the agent's other findings.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# Response keys that hold evidence ids (evidence_id, evidence_ids,
# supporting_evidence_ids, contradicting_evidence_ids, outcome_evidence_ids).
# Aliases are mapped back ONLY under these keys, never in prose or other ids.
_EVIDENCE_KEY = re.compile(r"(^|_)evidence_ids?$")
# Parameter statuses that assert something and therefore need a citation.
_SUBSTANTIVE = {"ok", "provisional", "requires_review"}


def evidence_aliases(evidence_ids: list[str]) -> dict[str, str]:
    """``{real_id: alias}`` in request order: E01, E02, ... (E001 past 99)."""
    unique = list(dict.fromkeys(evidence_ids))
    width = max(2, len(str(len(unique))))
    return {eid: f"E{i:0{width}d}" for i, eid in enumerate(unique, 1)}


def alias_inputs(inputs: Any, aliases: dict[str, str]) -> Any:
    """``inputs`` with every real evidence id (value or key) replaced by its alias."""
    if isinstance(inputs, dict):
        return {aliases.get(k, k) if isinstance(k, str) else k: alias_inputs(v, aliases)
                for k, v in inputs.items()}
    if isinstance(inputs, list):
        return [alias_inputs(v, aliases) for v in inputs]
    if isinstance(inputs, str):
        return aliases.get(inputs, inputs)
    return inputs


def unalias_answer(answer: Any, aliases: dict[str, str]) -> Any:
    """``answer`` with aliases under evidence keys mapped back to real ids."""
    real = {alias: eid for eid, alias in aliases.items()}

    def ids(value):
        if isinstance(value, str):
            return real.get(value.strip(), value)
        if isinstance(value, list):
            return [ids(v) for v in value]
        return value

    def walk(node):
        if isinstance(node, dict):
            return {k: ids(v) if isinstance(k, str) and _EVIDENCE_KEY.search(k)
                    and not isinstance(v, dict) else walk(v)
                    for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(answer)


def _distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, abandoned early once it exceeds ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _near_miss(eid: str, admitted: set[str]) -> str | None:
    """The single admitted id within 2 edits of a long ``eid`` (a typo), if any."""
    if len(eid) < 16:  # short ids are too close together to correct safely
        return None
    close = [a for a in admitted if _distance(eid, a, 2) <= 2]
    return close[0] if len(close) == 1 else None


def repair_citations(parsed: dict[str, Any], admitted: set[str], *,
                     passages: dict[str, str] | None = None
                     ) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Correct or remove citations of evidence not admitted for this agent.

    Returns the repaired answer and validator adjustments
    (``{"hypothesis_id", "note"}``). A quote whose cited id is unknown is
    re-attributed to the admitted passage that contains it verbatim, if exactly
    one does. A substantive parameter left with no citation inherits the
    admitted evidence of its own hypotheses, or becomes ``unavailable``; one
    that cited nothing at all is left for the validator to reject.
    """
    from app.services.bo.hypotheses import quote_in_passage

    out = deepcopy(parsed)
    adjustments: list[dict[str, str]] = []
    passages = passages or {}

    def note(owner, message):
        adjustments.append({"hypothesis_id": str(owner), "note": message})

    def fix(eid, owner):
        """The admitted id ``eid`` stands for, or None to drop the citation."""
        if not isinstance(eid, str) or eid in admitted:
            return eid
        corrected = _near_miss(eid, admitted)
        if corrected is not None:
            note(owner, f"Corrected a mistyped evidence id ({eid[:12]}... -> "
                        f"{corrected[:12]}...).")
            return corrected
        note(owner, f"Removed a citation of evidence not admitted for this agent "
                    f"({eid[:24]}).")
        return None

    def walk(node, owner):
        if isinstance(node, list):
            for item in node:
                walk(item, owner)
            return
        if not isinstance(node, dict):
            return
        owner = (node.get("hypothesis_id") or node.get("parameter_id")
                 or node.get("finding_id") or node.get("observation_id") or owner)
        for key, value in list(node.items()):
            if key == "evidence_quotes" and isinstance(value, list):
                node[key] = [q for q in (_fix_quote(q, owner) for q in value) if q is not None]
            elif isinstance(key, str) and _EVIDENCE_KEY.search(key):
                if isinstance(value, list):
                    fixed = [fix(v, owner) for v in value]
                    node[key] = list(dict.fromkeys(v for v in fixed if v is not None))
                elif isinstance(value, str) and value not in admitted:
                    # A lone source reference is only corrected, never blanked;
                    # its item's own validator judges an unknown source.
                    corrected = _near_miss(value, admitted)
                    if corrected is not None:
                        note(owner, f"Corrected a mistyped evidence id ({value[:12]}... -> "
                                    f"{corrected[:12]}...).")
                        node[key] = corrected
            else:
                walk(value, owner)

    def _fix_quote(quote, owner):
        if not isinstance(quote, dict) or quote.get("evidence_id") in admitted:
            return quote
        eid, text = quote.get("evidence_id"), quote.get("quote") or ""
        corrected = _near_miss(eid, admitted) if isinstance(eid, str) else None
        if corrected is None and text:
            holders = [a for a in admitted if a in passages
                       and quote_in_passage(text, passages[a])]
            corrected = holders[0] if len(holders) == 1 else None
        if corrected is None:
            note(owner, "Removed a quote attributed to evidence not admitted for "
                        "this agent.")
            return None
        note(owner, f"Re-attributed a quote to admitted evidence {corrected[:12]}...")
        return {**quote, "evidence_id": corrected}

    cited = {i for i, p in enumerate(out.get("parameters") or [])
             if isinstance(p, dict) and p.get("evidence_ids")}
    walk(out, "(answer)")

    support = {h.get("hypothesis_id"): h.get("supporting_evidence_ids") or []
               for h in out.get("hypotheses") or [] if isinstance(h, dict)}
    for i, item in enumerate(out.get("parameters") or []):
        if i not in cited or item.get("evidence_ids"):
            continue
        if item.get("status", "ok") not in _SUBSTANTIVE:
            continue
        inherited = list(dict.fromkeys(
            e for h in item.get("hypothesis_ids") or [] for e in support.get(h, [])))
        pid = item.get("parameter_id")
        if inherited:
            item["evidence_ids"] = inherited
            note(pid, "No admitted citation remained; cites the evidence of its "
                      "own hypotheses.")
        else:
            item["status"] = "unavailable"
            note(pid, "No admitted citation remained; status set to unavailable.")
    return out, adjustments
