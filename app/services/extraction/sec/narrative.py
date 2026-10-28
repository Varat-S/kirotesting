"""SEC Item passages and keyword retrieval are candidate evidence, not conclusions."""

import re
from app.core.hashing import content_hash

TOPICS = {
    "supplier_concentration": r"suppliers?|manufacturers?",
    "customer_concentration": r"customers?|concentration",
    "regulatory_exposure": r"export|regulat|sanction",
    "liquidity": r"liquidity|cash flow|credit facilit",
    "governance": r"board|governance|related.person|succession",
    "management": r"executive|officers|directors|management",
    "executive_compensation": r"compensation|remuneration|salary",
    "beneficial_ownership": r"beneficial|ownership|stockholders?",
    "related_person_transactions": r"related.person|related.party",
    "succession": r"succession",
}

# Business description and results discussion (what changed and why). A 10-K
# chunk matching none of TOPICS is still admitted under this topic: the
# Business Overview needs it. Only a fallback, so chunks admitted under TOPICS
# keep their topics and therefore their evidence ids.
BUSINESS_RESULTS_TOPIC = "business_results"
BUSINESS_RESULTS = (r"revenues?|admissions|volumes?|compared to|increased|decreased|"
                    r"primarily (?:due|attributable) to|compet|pric(?:e|es|ing)|margins?|"
                    r"segments?")


def split_items(text):
    matches = list(
        re.finditer(
            r"(?im)^\s*(?:PART\s+[IVX]+\s*[|:]?\s*)?ITEM\s+(\d{1,2}[AB]?)\b[.\s:—-]*(.*)$",
            text,
        )
    )
    # A filing repeats Items in its table of contents. Keep the longest body per Item.
    items = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        passage = {
            "section": "Item " + match[1].upper(),
            "heading": match[2].strip(),
            "text": text[match.start() : end].strip(),
            "location": match.start(),
        }
        key = passage["section"]
        if key not in items or len(passage["text"]) > len(items[key]["text"]):
            items[key] = passage
    return list(items.values())


def find_excerpts(text, *, max_length=2000, business_results=False):
    excerpts = []
    for start in range(0, len(text), max_length):
        part = text[start : start + max_length]
        topics = [
            topic for topic, pattern in TOPICS.items() if re.search(pattern, part, re.I)
        ]
        if not topics and business_results and re.search(BUSINESS_RESULTS, part, re.I):
            topics = [BUSINESS_RESULTS_TOPIC]
        if topics:
            excerpts.append(
                {"text": part, "location": start, "candidate_topics": topics}
            )
    return excerpts


def narrative_evidence(text, *, document_id, accession, kind="10-K"):
    passages = split_items(text) if kind == "10-K" else []
    if not passages:
        passages = [{"section": kind, "heading": kind, "text": text, "location": 0}]
    results = []
    for passage in passages:
        chunks = find_excerpts(passage["text"], business_results=kind == "10-K")
        if kind == "Exhibit 21":
            chunks = [
                {"text": passage["text"][start:start + 2000], "location": start,
                 "candidate_topics": ["legal_entities"]}
                for start in range(0, len(passage["text"]), 2000)
            ]
        for chunk in chunks:
            evidence = {
                **chunk,
                "location": passage["location"] + chunk["location"],
                "document_id": document_id,
                "sec_accession": accession,
                "section": passage["section"],
                "heading": passage["heading"],
                "status": "unverified",
                "evidence_kind": "candidate_passage",
            }
            evidence["evidence_id"] = content_hash(evidence)
            results.append(evidence)
    return results
