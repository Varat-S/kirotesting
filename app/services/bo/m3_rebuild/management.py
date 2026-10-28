"""Objective fact checks and date calculations around model-authored credit judgments."""
import calendar
from datetime import date
import re
from .contracts import validate_extraction
from .sources import normalized

class QuotationError(ValueError):
    pass


DIRECTIONS = {"positive", "negative", "neutral", "unavailable"}


def date_interval(value):
    """An incomplete date is an interval, never an invented appointment day."""
    if value == "":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError(f"Invalid disclosed date: {value!r}")
    parts = [int(p) for p in value.split('-')]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    lo = date(year, month, day)
    hi = lo if len(parts) == 3 else date(year, month if len(parts) == 2 else 12,
            calendar.monthrange(year, month)[1] if len(parts) == 2 else 31)
    return lo, hi, ("year", "month", "day")[len(parts)-1]


def review_window(roster_date):
    anchor = date.fromisoformat(roster_date)
    start = anchor.replace(year=anchor.year-3, day=min(anchor.day, calendar.monthrange(anchor.year-3, anchor.month)[1]))
    return {"start": start.isoformat(), "end": anchor.isoformat(),
            "boundary": "inclusive [start, end]", "years": 3}


def recency(value, window):
    interval = date_interval(value)
    if interval is None:
        return "unavailable"
    lo, hi, _ = interval
    start, end = date.fromisoformat(window["start"]), date.fromisoformat(window["end"])
    if lo > end:
        raise ValueError("Appointment occurs after disclosed roster date")
    if hi < start:
        return "stable"
    if lo >= start and hi <= end:
        return "recent"
    return "boundary_uncertain"


def words(text):
    return re.findall(r"[a-z0-9]+", text.casefold())


def phrase_in(phrase, text):
    return " ".join(words(phrase)) in " ".join(words(text))


def date_supported(value, quote):
    interval = date_interval(value)
    if interval is None:
        return True
    lo, _, precision = interval
    # Match exact disclosed precision; do not allow a year elsewhere to support a full date.
    if re.search(r'(?<!\d)'+re.escape(value)+r'(?!\d)',quote):
        return True
    if precision == "year":
        return bool(re.search(rf"\b{lo.year}\b", quote))
    pattern = rf"\b{lo.strftime('%B')}\s+" + (rf"0?{lo.day},?\s+" if precision == "day" else "") + str(lo.year) + r"\b"
    return bool(re.search(pattern, normalized(quote), re.I))


MONTHS = "January February March April May June July August September October November December".split()
DATE_TEXT = r"(?:" + "|".join(MONTHS) + r")\s+(?:\d{1,2},?\s+)?\d{4}|\d{4}(?:-\d{2}(?:-\d{2})?)?"


def natural_date(value):
    value = normalized(value).replace(',', '')
    if value[:1].isdigit():
        date_interval(value)
        return value
    parts = value.split()
    month = MONTHS.index(parts[0].title()) + 1
    return f"{int(parts[-1]):04}-{month:02}" + (f"-{int(parts[1]):02}" if len(parts) == 3 else "")


def prior_role_date_pairs(position, quote):
    q = normalized(quote)
    pattern = r"\b" + r"\W+".join(re.escape(w) for w in words(position)) + r"\b"
    pairs = []
    sentence_break = r"(?<![A-Z])(?<!Mr)(?<!Dr)(?<!Ms)\.\s+(?=[A-Z])"
    range_pattern = re.compile(r"\bfrom (" + DATE_TEXT + r")\s+(?:to|through) (" + DATE_TEXT + r")", re.I)
    for sentence in re.split(sentence_break, q):
        for match in re.finditer(pattern, sentence, re.I):
            after = re.split(r"\band as\b|\band (?:CFO|President|Chief|Vice)\b", sentence[match.end():], maxsplit=1)[0]
            found = range_pattern.search(after)
            if found is None:
                before = sentence[:match.start()]
                ranges = list(range_pattern.finditer(before))
                found = ranges[-1] if ranges else None
            if found:
                pairs.append((natural_date(found[1]), natural_date(found[2])))
    return pairs




def compact_citations(result):
    from hashlib import sha256
    table = {}
    physical_pages = result["source"].get("physical_pages", {})
    source_hash = result["source"]["sha256"]
    def register(citation):
        key = sha256((citation["source_sha256"] + "|" + citation["source_id"] + "|" + normalized(citation["quote"])).encode()).hexdigest()[:16]
        cid = "cite:" + key
        table.setdefault(cid, {"citation_id": cid, **citation})
        return cid
    result["roster_citation_id"] = register(result["roster_citation"]) if result["roster_citation"] else None
    del result["roster_citation"]
    for profile in result["profiles"]:
        profile["citation_ids"] = list(dict.fromkeys(register(c) for c in profile.pop("citations")))
        for role in profile["prior_internal_roles"]:
            if "validated_citations" in role:
                role["citation_ids"] = list(dict.fromkeys(
                    register(c) for c in role.pop("validated_citations")
                ))
                role.pop("source_id")
                role.pop("quote")
                continue
            citation = {"source_id": role.pop("source_id"), "quote": role.pop("quote"),
                        "source_sha256": source_hash, "quotation_check": "passed"}
            citation["physical_page"] = physical_pages.get(
                citation["source_id"],
                int(citation["source_id"].split('p')[-1])
                if "pdf:p" in citation["source_id"] else 1,
            )
            role["citation_ids"] = [register(citation)]
    for i, observation in enumerate(result["observations"], 1):
        observation.setdefault("observation_id", f"management:{i:03}")
        observation["citation_ids"] = list(dict.fromkeys(register(c) for c in observation.pop("citations")))
    for fact in result.get("facts", []):
        fact["citation_ids"] = list(dict.fromkeys(register(c) for c in fact.pop("citations")))
    for claim in result.get('validation', {}).get('claim_results', []):
        claim['citation_ids'] = list(dict.fromkeys(register(c) for c in claim['citations']))
    result["citations"] = list(table.values())
    result["source"].pop("physical_pages", None)
    return result


def assess_management(payload, sources, reviewer=None, enforce=True):
    from .claims import assess_claims
    return assess_claims(payload, sources, reviewer=reviewer, enforce=enforce)

def aggregate(observations):
    actual = {o["direction"] for o in observations if o["category"] != "information_gap"}
    gaps = any(o["category"] == "information_gap" for o in observations)
    direction = "mixed" if {"positive", "negative"} <= actual else "positive" if "positive" in actual else "negative" if "negative" in actual else "unavailable"
    return {"outcome": direction, "information_gaps": gaps,
            "policy": "Retain each supported positive and negative; both yield mixed, without netting, weighting or confidence scores. Information gaps are separate.",
            "information_direction": "negative" if any(o["rule"] == "D" for o in observations) else "unavailable" if gaps else "neutral"}


def missing_management(sources):
    observations = [{"executive": None, "rule": "source_unavailable", "direction": "unavailable", "category": "information_gap",
        "text": "No usable executive section in the selected 10-K or its linked proxy statement; a qualitative credit assessment is unavailable.", "citations": []}]
    result = {"schema_version": "m3-management-assessment-1.0", "artifact_type": "senior_management_assessment",
        "company": sources.company, "filing_period": sources.filing_period,
        "source": {"path": sources.source_path, "sha256": sources.sha256,
                   "section_ids": [], "physical_pages": sources.physical_pages},
        "roster_date": None, "roster_citation": None, "review_window": None, "profiles": [],
        "observations": observations, "overall_assessment": aggregate(observations),
        "coverage": {"expected": None, "admitted": 0, "missing": [], "verifiable": False},
        "rejected_profiles": [], "limitations": sources.limitations, "completion_status": "partial"}

    return compact_citations(result)
