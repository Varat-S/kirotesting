"""M3-only, bounded query coverage for already retrieved admitted passages."""

from __future__ import annotations

from app.services.bo.retrieval import RetrievalBudget, instruction_like


M3_SELECTION_VERSION = "bo-m3-query-coverage-1.1"


def select_m3_passages(retrieval, corpus, budget=None):
    """Reserve query coverage before filling by relevance, within the same limits.

    Only IDs actually returned by the existing searches are eligible. In
    particular, retain substantive control-assessment/opinion passages instead
    of crowding out that query with earlier multi-purpose risk paragraphs.
    No source is fetched and no new search/result is invented.
    """
    budget = budget or RetrievalBudget()
    by_id = {p["evidence_id"]: p for p in corpus}
    candidates = {}
    groups = []
    for attempt in retrieval.attempts:
        ids = []
        for ref in attempt.results:
            eid = ref["evidence_id"]
            if eid not in by_id:
                continue
            entry = candidates.setdefault(eid, {**by_id[eid], "retrieved_for": []})
            if attempt.purpose not in entry["retrieved_for"]:
                entry["retrieved_for"].append(attempt.purpose)
            entry["instruction_like"] = instruction_like(entry.get("text") or "")
            ids.append(eid)
        if attempt.query_id == "m3.management_limits":
            def control_priority(eid):
                text = (by_id[eid].get("text") or "").lower()
                return (0 if "opinion on internal control over financial reporting" in text else
                        1 if "management concluded" in text and "financial reporting" in text else 2)
            ids.sort(key=control_priority)
        groups.append((attempt.query_id, ids))
    # Two substantive control passages (management + auditor) when available;
    # then one per other query before spending the remaining slots on extras.
    priority = [eid for name, ids in groups if name == "m3.management_limits" for eid in ids[:2]]
    priority += [eid for name, ids in groups if name == "m3.executive_biographies" for eid in ids[:3]]
    priority += [ids[0] for name, ids in groups if ids and name != "m3.management_limits"]
    priority += [eid for _, ids in groups for eid in ids]
    kept = {}
    chars = 0
    for eid in priority:
        if eid in kept:
            continue
        size = len(candidates[eid].get("text") or "")
        if len(kept) >= budget.max_passages_per_agent or chars + size > budget.max_chars_per_agent:
            continue
        kept[eid] = candidates[eid]
        chars += size
    retrieval.passages = sorted(kept.values(), key=lambda p: (
        str(p.get("document_id") or ""), p.get("location") or 0, p["evidence_id"]))
    return {
        "policy_version": M3_SELECTION_VERSION,
        "max_passages": budget.max_passages_per_agent,
        "max_chars": budget.max_chars_per_agent,
        "selected_passages": len(kept), "selected_chars": chars,
        "queries_without_selected_results": [name for name, ids in groups if not set(ids) & set(kept)],
        "scope_notice": ("Financial-reporting-control assurance is scoped to financial reporting; "
                         "it does not establish personal integrity or broad management competence."),
    }
