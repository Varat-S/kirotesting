"""FO Pre-check: acquisitions and divestitures disclosed for the year.

Year-end receivables include the whole acquired business, but revenue covers only part of the year: DSO is
overstated after an acquisition and understated after a divestiture. A divestiture after the year-end changes debt
(proceeds repaid) and EBITDAR (the divested EBITDAR). Same three steps as the other pre-checks, so the model never
decides on its own word:

1. ``deal_passages`` (deterministic) collects the acquisitions or dispositions note, the discontinued-operations and
   held-for-sale notes in full (tables as rows), subsequent events that mention a deal, and deal mentions elsewhere.
   Nothing collected means no deal for the year (no model call). ``rule_signals`` is a keyword cross-check.
2. The Pre-check Agent (prompt ``fo_precheck_acquisitions``) ALWAYS reads those passages (no agent, or a rejected
   output, refuses the run) and lists each deal once, with verbatim evidence: the deal, its date, its presentation
   (discontinued operations, held for sale), its materiality, pro forma revenue, the divested revenue, the debt
   repaid from the proceeds and any EBITDAR parts the 10-K discloses.
3. ``validate_agent`` keeps only evidence whose quote is verbatim in the cited passage and says what the fact claims
   (every amount and date must be in its quote), re-derives each deal's timing from its date, and RE-DERIVES each
   deal's outcome, first match:

   - acquisition with pro forma revenue disclosed: DSO uses the pro forma revenue;
   - divestiture presented as discontinued operations: no adjustment (revenue and receivables already exclude it);
   - divestiture not in discontinued operations, revenue disclosed: the divested revenue is removed from DSO;
   - not material (no pro forma required): no adjustment;
   - material, but its revenue isn't disclosed: unresolved, and the measure is escalated to an analyst.

   A divestiture after the year-end adjusts debt (proceeds repaid) and EBITDAR (divested EBITDAR) together, never one
   side: if the divested EBITDAR can't be built from the 10-K, neither is adjusted and Debt/EBITDAR is unresolved.
   ASC 805 pro forma gives revenue and earnings, not EBITDAR: an acquisition's missing months of EBITDAR are added
   only if they can be built from the 10-K.

Each deal is recorded once (``deals``) and the record is shared by Debt/EBITDAR, DSO and, when it is built, the 25%
key-input trigger.
"""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta

from app.prompts.fo_prompts import (
    FO_ACQUISITIONS_PROMPT,
    FO_ACQUISITIONS_SCHEMA,
    FO_ACQUISITIONS_TEMPLATE,
)

from .covenants import NOTE_HEADING, OTHER_NOTE_TITLE, PAGE_HEADER, SUBSEQUENT_TITLE, _dates
from .measures import LEVERAGE_MEASURES, SENTENCE_SPLIT, days_sales_outstanding, pick, plain_values
from .precheck import DATA_NOTICE, PrecheckAgentError, _clip, _heading, _norm, call_agent, require_agent

AD_VERSION = "fo-acquisitions-1.0.0"
SHARED_WITH = ["leverage", "dso", "key_input_trigger"]  # the measures (and trigger) that read the deal record

SECTIONS = [("acquisitions_note", "Acquisitions or dispositions note"),
            ("discontinued_operations", "Discontinued operations"), ("held_for_sale", "Held for sale"),
            ("subsequent_events", "Subsequent events"), ("other", "Elsewhere in the 10-K")]
SECTION_LABELS = dict(SECTIONS)
WHOLE = {"acquisitions_note", "discontinued_operations", "held_for_sale"}  # read in full, tables included
ITEMS = ("8", "F", "")  # the notes are where deals are disclosed; MD&A only repeats them
ACQ_TITLE = re.compile(r"\bacquisitions?\b|business combinations?|dispositions?|divestitures?|\bmergers?\b", re.I)
DISC_TITLE = re.compile(r"discontinued operations?", re.I)
HFS_TITLE = re.compile(r"held for sale", re.I)
DEAL_TERMS = re.compile(r"\bacqui\w*|\bpurchase\w*|\bmerg\w*|business combination|\bdivest\w*|\bdispos\w*|\bsold\b|"
                        r"\bsale of\b|held for sale|discontinued operation|spin-?off|pro forma", re.I)
# Outside the sections read in full, a paragraph is collected only on one of these.
STRONG_TERMS = re.compile(r"pro forma|discontinued operations?|held for sale|\bdivest\w*|business combination|"
                          r"completed (the|its|our) (acquisition|sale|divestiture|merger)", re.I)
PAGE_ARTIFACT = re.compile(r"^F-\d{1,3}$|^page \d+$", re.I)  # a bare "820" may be a table cell: kept
SCALE = re.compile(r"\bin (thousands|millions|billions)\b", re.I)
SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
LIMIT, OTHER_LIMIT, PASSAGE_MAX_CHARS = 60, 15, 2000

# --- what a quote says ---------------------------------------------------------------------------------
ACQUISITION_WORDS = re.compile(r"\bacqui\w*|\bpurchas\w*|\bmerg\w*|business combination", re.I)
DIVESTITURE_WORDS = re.compile(r"\bsold\b|\bsale\b|\bsales of\b|\bdivest\w*|\bdispos\w*|spin-?off|\bseparat\w*|"
                               r"held for sale|discontinued operation", re.I)
DISCONTINUED = re.compile(r"discontinued operations?", re.I)
NOT_DISCONTINUED = re.compile(r"\bnot\b[^.]{0,60}discontinued operation|did not (meet|qualify)[^.]{0,80}discontinued",
                              re.I)
HELD_FOR_SALE = re.compile(r"held for sale", re.I)
NOT_MATERIAL = re.compile(r"\bnot\b[^.]{0,30}\b(material|significant)\b|\bimmaterial\b|\binsignificant\b|"
                          r"pro forma[^.]{0,120}\bnot (been )?(presented|provided|included|disclosed)", re.I)
MATERIAL = re.compile(r"\bmaterial\b|\bsignificant\b", re.I)
PRO_FORMA = re.compile(r"pro forma", re.I)
REVENUE_WORDS = re.compile(r"\brevenues?\b|\bnet sales\b|\bsales\b", re.I)
FACT_NEEDS = {  # what a fact's quote must say: (pattern, why it is held back otherwise)
    "discontinued_operations": (DISCONTINUED, "quote doesn't say discontinued operations"),
    "held_for_sale": (HELD_FOR_SALE, "quote doesn't say held for sale"),
    "not_material": (NOT_MATERIAL, "quote doesn't say the deal is not material or not significant"),
    "material": (MATERIAL, "quote doesn't say the deal is material or significant"),
    "pro_forma_revenue": (REVENUE_WORDS, "quote names no revenue"),
    "divested_revenue": (REVENUE_WORDS, "quote names no revenue"),
    "consideration": (re.compile(r"purchase price|consideration|\bpaid\b|\bpay\b|proceeds|acqui|\bsold\b|\bsale\b|"
                                 r"\bcash\b", re.I), "quote shows no price or proceeds"),
    "debt_repaid": (re.compile(r"\brepa(y|id|ying|yment)\w*|\bredeem\w*|redemption|\bprepa\w*|\bretire\w*|"
                               r"extinguish\w*", re.I), "quote shows no repayment of debt"),
    "ebitdar": (re.compile(r"EBITDAR", re.I), "quote names no EBITDAR"),
    "ebitda": (re.compile(r"EBITDA\b", re.I), "quote names no EBITDA"),
    "ebit": (re.compile(r"\bincome\b|\bearnings\b|\bprofit\b|\bloss\b|\bEBIT\b", re.I), "quote names no income"),
    "da": (re.compile(r"depreciation|amorti[sz]ation", re.I), "quote names no depreciation or amortization"),
    "rent": (re.compile(r"\brent\w*|\blease", re.I), "quote names no rent or lease cost"),
}
AMOUNT_FACTS = {"pro_forma_revenue", "divested_revenue", "consideration", "debt_repaid", "ebitdar", "ebitda", "ebit",
                "da", "rent"}
AMOUNT = re.compile(r"^\(?\s*(?:US)?\$?\s*\(?\s*(\d[\d,]*(?:\.\d+)?)\s*\)?\s*(thousand|million|billion)?s?\s*\)?$",
                    re.I)
MIN_QUOTE = 12
# A deal the filing doesn't call material or not: not material when its price (or proceeds) is under this share of
# total assets, else treated as material. Without a price, it is treated as material (conservative).
MATERIALITY_SHARE = 0.05
ASSET_CONCEPTS = ["us-gaap:Assets", "ifrs-full:Assets"]

OUTCOME_ORDER = ["not_for_the_year", "pro_forma_revenue", "discontinued_operations", "remove_divested_revenue",
                 "not_material", "unresolved"]
OUTCOME_TEXT = {
    "not_for_the_year": "Not a deal of this fiscal year (a comparative-year deal, or an acquisition after the "
                        "year-end): no adjustment",
    "pro_forma_revenue": "Acquisition with pro forma revenue disclosed: DSO recomputed using the pro forma revenue",
    "discontinued_operations": "Divestiture presented as discontinued operations: no adjustment (revenue and "
                               "receivables already exclude it)",
    "remove_divested_revenue": "Divestiture not in discontinued operations, revenue disclosed: the divested revenue "
                               "is removed and DSO recomputed",
    "not_material": "Not material (no pro forma required): no adjustment",
    "unresolved": "Material deal, but its revenue isn't disclosed: unresolved",
}
STATUS_REASONS = {
    "none": "No acquisition or divestiture disclosed for the year",
    "no_adjustment": "Acquisitions or divestitures disclosed; none needs an adjustment",
    "adjusted": "Acquisitions or divestitures disclosed; DSO or Debt/EBITDAR recomputed for them",
    "unresolved": "A material acquisition or divestiture whose revenue (or divested EBITDAR) isn't disclosed",
}
EFFECTS = {
    "none": "None: no change.",
    "no_adjustment": "None: no change.",
    "adjusted": "The affected measures are recomputed with the deal figures (see each deal); the record is shared "
                "by Debt/EBITDAR, DSO and the 25% key-input trigger.",
    "unresolved": "The affected measure runs as reported and is escalated to an analyst, who accepts its score or "
                  "overrides it.",
}
MEASURE_NAMES = {"dso": "Days sales outstanding (DSO)", "leverage": "Debt to EBITDAR"}
UNRESOLVED_NOTE = ("Unresolved acquisition or divestiture (pre-check): left as reported and escalated to an analyst, "
                   "who accepts this score or overrides it.")
DECIDED_NOTE = ("Acquisition or divestiture unresolved by the pre-check (left as reported); the analyst has decided "
                "this measure's score.")


def _sentences(text):
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


# --- the passages --------------------------------------------------------------------------------------

def _section_after(heading: str, current: str | None, sub: bool, numbered: bool, in_notes: bool):
    """(section, opened by a sub-heading) after a heading. In numbered notes a sub-heading can open a deal section
    inside another note (e.g. "Discontinued Operations" in Note 2); it closes at the next sub-heading of 3+ words.
    Before the first note (the statements: "Acquisition of hospitals" in the cash flow statement) nothing opens."""
    kind = ("subsequent_events" if SUBSEQUENT_TITLE.search(heading) else
            "discontinued_operations" if DISC_TITLE.search(heading) else
            "held_for_sale" if HFS_TITLE.search(heading) else
            "acquisitions_note" if ACQ_TITLE.search(heading) and len(heading) <= 100 else None)
    if numbered and not NOTE_HEADING.match(heading):  # a sub-heading inside a note
        if not in_notes:
            return None, False
        if kind:
            return kind, current is None or sub
        if sub and len(re.findall(r"[A-Za-z]{2,}", heading)) >= 3 and not re.search(r"\d", heading):
            return None, False
        return current, sub
    if kind:
        return kind, False
    if numbered or OTHER_NOTE_TITLE.search(heading):
        return None, False
    return current, sub


def deal_passages(data: dict) -> dict:
    """The deal passages (ids P01… in document order): the acquisitions or dispositions, discontinued-operations and
    held-for-sale notes in full (consecutive short lines joined as one table, cells separated by " | "), subsequent
    events that mention a deal, and paragraphs elsewhere in the notes with a strong deal term. Each passage
    carries the amount scale ("in millions") in force where it sits."""
    passages, seen, found = [], set(), set()
    entity = (data["meta"].get("entity_name") or "").strip().lower()
    for sec in (s for s in data["document"] if s["item"] in ITEMS):
        item = sec["item"]
        lines = sec["text"].split("\n")
        numbered = any(NOTE_HEADING.match(ln.strip()) and _heading(ln.strip()) for ln in lines)
        state = {"section": None, "sub": False, "scale": None, "cells": [], "in_notes": False}

        def add(where, text, kind):
            key = re.sub(r"\W+", "", text.lower())[:200]
            if not key or key in seen or len(passages) >= LIMIT:
                return
            if where == "other" and sum(p["section"] == "other" for p in passages) >= OTHER_LIMIT:
                return
            seen.add(key)
            passages.append({"item": item, "section": where, "section_label": SECTION_LABELS[where], "kind": kind,
                             "text": _clip(text, PASSAGE_MAX_CHARS), "scale": state["scale"]})

        def flush():
            cells = state["cells"]
            if state["section"] in WHOLE and len(cells) >= 2 and any(ch.isdigit() for c in cells for ch in c):
                add(state["section"], " | ".join(cells), "table")
            state["cells"] = []

        for line in lines:
            s = line.strip()
            if not s or PAGE_ARTIFACT.match(s) or s.lower().rstrip(".") == entity.rstrip("."):
                continue
            if _heading(s) and " | " not in s:
                if PAGE_HEADER.search(s):
                    continue
                if numbered and NOTE_HEADING.match(s):
                    state["in_notes"] = True
                new, sub = _section_after(s, state["section"], state["sub"], numbered, state["in_notes"])
                if new != state["section"]:
                    flush()
                    state.update(section=new, sub=sub, scale=None)
                    if new:
                        found.add(new)
                    continue
                state["sub"] = sub
                if new in WHOLE:
                    state["cells"].append(s)  # a table label or a sub-heading of the note
                continue
            section = state["section"]
            if section and SCALE.search(s):
                state["scale"] = SCALES[SCALE.search(s).group(1).lower().rstrip("s")]
            if len(s) < 40 or s.count(" | ") > 2:
                if section in WHOLE:
                    state["cells"].append(s)
                continue
            flush()
            if section in WHOLE:
                add(section, s, "text")
            elif section == "subsequent_events":
                if DEAL_TERMS.search(s):
                    add(section, s, "text")
            elif STRONG_TERMS.search(s):
                text = s if len(s) <= PASSAGE_MAX_CHARS else " ".join(t for t in _sentences(s) if STRONG_TERMS.search(t))
                add("other", text, "text")
        flush()
    for i, p in enumerate(passages, 1):
        p["passage_id"] = f"P{i:02d}"
    counts = {}
    for p in passages:
        counts[p["section"]] = counts.get(p["section"], 0) + 1
    return {"passages": passages,
            "sections": [{"key": k, "label": label, "located": k in found or k == "other", "passages": counts.get(k, 0)}
                         for k, label in SECTIONS]}


# --- the keyword cross-check ---------------------------------------------------------------------------

RULE_SIGNALS = [  # (signal, the evidence fact it corresponds to, pattern)
    ("discontinued operations", "discontinued_operations", DISCONTINUED),
    ("held for sale", "held_for_sale", HELD_FOR_SALE),
    ("pro forma not material", "not_material", re.compile(PRO_FORMA.pattern + r"[^.]{0,200}(" + NOT_MATERIAL.pattern
                                                          + ")", re.I)),
    ("pro forma revenue", "pro_forma_revenue", re.compile(r"pro forma[^.]{0,160}\b(revenues?|net sales)\b", re.I)),
]


def rule_signals(passages: list[dict]) -> list[dict]:
    """Sentences stating a deal fact the agent should account for (a cross-check, never the decision)."""
    out = []
    for p in passages:
        for s in _sentences(p["text"]):
            for signal, fact, pattern in RULE_SIGNALS:
                if not pattern.search(s) or (fact == "discontinued_operations" and NOT_DISCONTINUED.search(s)):
                    continue
                if fact == "pro_forma_revenue" and NOT_MATERIAL.search(s):
                    continue
                out.append({"signal": signal, "fact": fact, "passage_id": p["passage_id"], "quote": s})
                break
    return out


# --- the reported figures the deals are measured against -----------------------------------------------

def reported_figures(data: dict) -> dict:
    """The latest fiscal year's revenue (as DSO uses it), its dates and total assets at its year-end."""
    dso = days_sales_outstanding(data["facts"], data["labels"], data["sections"])
    col = dso["columns"][0] if dso["columns"] else {}
    period = col.get("period")
    start = end = None
    if period and "--" in period:
        start, end = (date.fromisoformat(x[:10]) for x in period.split("--"))
    assets = None
    if col.get("as_at"):
        _, assets, _ = pick(plain_values(data["facts"]), ASSET_CONCEPTS, col["as_at"])
    return {"period": period, "period_start": start.isoformat() if start else None,
            "period_end": end.isoformat() if end else data["meta"].get("period_end"),
            "revenue": col.get("revenue"), "revenue_concept": col.get("revenue_concept"), "total_assets": assets}


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


# --- the agent -----------------------------------------------------------------------------------------

def agent_inputs(ad: dict, meta: dict, reported: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_start": reported["period_start"], "period_end": reported["period_end"],
                    "reported_revenue": reported["revenue"]},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in ad["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in ad["passages"]],
    }


def parse_amount(text: str, scale: float | None) -> tuple[float | None, str | None]:
    """(value in the reporting unit, why not): "$1.2 billion" → 1.2e9; "1,234" in a passage "in millions" → 1.234e9."""
    m = AMOUNT.match((text or "").strip())
    if not m:
        return None, f"amount {text!r} isn't a number"
    number = float(m.group(1).replace(",", ""))
    if m.group(2):
        return number * SCALES[m.group(2).lower()], None
    if scale:
        return number * scale, None
    return None, "the amount's unit (thousands, millions) isn't stated in its passage"


def amount_in(amount: str, quote: str) -> bool:
    """Whether the amount, as written, is in the quote as a whole number: "15" is not in "150" or "1.15"."""
    a, q = _norm(amount), _norm(quote)
    return bool(a) and re.search(r"(?<![\d.,])" + re.escape(a) + r"(?![\d]|[.,]\d)", q) is not None


def _date_in_quote(value: str, quote: str) -> tuple[tuple | None, str | None]:
    """((year, month or None, day or None), why not): the stated date, if the quote contains it."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", (value or "").strip())
    if not m:
        return None, f"date {value!r} isn't YYYY-MM-DD, YYYY-MM or YYYY"
    y, mo, d = int(m.group(1)), int(m.group(2)) if m.group(2) else None, int(m.group(3)) if m.group(3) else None
    if mo is None:
        ok = re.search(rf"\b{y}\b", quote) is not None
    else:
        ok = any((x.year, x.month) == (y, mo) for x in _dates(quote))
    return ((y, mo, d), None) if ok else (None, f"the date {value} is not in the quote")


def timing_for(stated: tuple, start: date | None, end: date | None) -> str | None:
    """in_year, after_year_end or prior_year for a date of the precision stated; None without the year's dates."""
    if end is None:
        return None
    start = start or end - timedelta(days=364)
    y, mo, d = stated
    if mo is None:
        return "prior_year" if y < start.year else "after_year_end" if y > end.year else "in_year"
    if d is None:
        key, lo, hi = (y, mo), (start.year, start.month), (end.year, end.month)
    else:
        key, lo, hi = date(y, mo, d), start, end
    return "prior_year" if key < lo else "after_year_end" if key > hi else "in_year"


def _verbatim(quote: str, text: str) -> bool:
    q = _norm(quote).rstrip("…")
    return len(q) >= MIN_QUOTE and q in _norm(text)


def check_evidence(e: dict, p: dict | None, kind: str, reported: dict) -> tuple[dict | None, str | None]:
    """(the verified fact, why it is held back)."""
    fact, quote = e.get("fact"), e.get("quote") or ""
    if p is None:
        return None, "cites a passage that was not supplied"
    if not _verbatim(quote, p["text"]):
        return None, "quote is not verbatim in the cited passage"
    if fact == "deal" and not (ACQUISITION_WORDS if kind == "acquisition" else DIVESTITURE_WORDS).search(quote):
        return None, f"quote discloses no {kind}"
    if fact in FACT_NEEDS and not FACT_NEEDS[fact][0].search(quote):
        return None, FACT_NEEDS[fact][1]
    if fact == "discontinued_operations" and NOT_DISCONTINUED.search(quote):
        return None, "quote says it is NOT presented as discontinued operations"
    if fact == "material" and NOT_MATERIAL.search(quote):
        return None, "quote says the deal is not material"
    if fact == "pro_forma_revenue" and not PRO_FORMA.search(p["text"]):
        return None, "the passage is not pro forma information"
    out = {"fact": fact, "passage_id": p["passage_id"], "quote": quote, "section": p["section_label"]}
    if e.get("date"):
        stated, why = _date_in_quote(e["date"], quote)
        if why:
            return None, why
        out["date"], out["date_parts"] = e["date"], list(stated)
    elif fact == "date":
        return None, "no date given"
    if fact in AMOUNT_FACTS:
        if not e.get("amount"):
            return None, "no amount given"
        if not amount_in(e["amount"], quote):
            return None, f"the amount {e['amount']} is not in the quote"
        value, why = parse_amount(e["amount"], p.get("scale"))
        if why:
            return None, why
        revenue = reported.get("revenue")
        if fact == "pro_forma_revenue" and revenue and value < revenue:
            return None, "below the reported revenue, so not the combined company's revenue for the year"
        if fact == "divested_revenue" and revenue and value >= revenue:
            return None, "not less than the company's reported revenue"
        out.update(amount=e["amount"], value=value)
    return out, None


def build_ebitdar(facts: dict) -> tuple[float | None, str]:
    """(EBITDAR, how it was built) from the verified parts, or (None, what is missing). Rent not disclosed counts as 0,
    as the measures treat untagged rent."""
    v = {k: facts[k]["value"] for k in ("ebitdar", "ebitda", "ebit", "da", "rent") if k in facts}
    if "ebitdar" in v:
        return v["ebitdar"], "EBITDAR as disclosed"
    rent, rent_note = v.get("rent", 0.0), "" if "rent" in v else " (rent not disclosed: taken as 0)"
    if "ebitda" in v:
        return v["ebitda"] + rent, "EBITDA + rent" + rent_note
    if "ebit" in v and "da" in v:
        return v["ebit"] + v["da"] + rent, "EBIT + D&A + rent" + rent_note
    missing = [n for k, n in (("ebit", "EBIT"), ("da", "D&A")) if k not in v]
    return None, "no " + " or ".join(missing) + " disclosed"


def derive_outcome(deal: dict) -> str:
    """The first outcome that applies to one verified deal."""
    kind, timing, facts = deal["kind"], deal["timing"], deal["facts"]
    if timing == "prior_year" or (kind == "acquisition" and timing == "after_year_end"):
        return "not_for_the_year"
    if kind == "acquisition" and "pro_forma_revenue" in facts:
        return "pro_forma_revenue"
    if kind == "divestiture" and deal["presentation"] == "discontinued_operations":
        return "discontinued_operations"
    if kind == "divestiture" and "divested_revenue" in facts:
        return "remove_divested_revenue"
    if deal["materiality"] == "not_material":
        return "not_material"
    return "unresolved"


def _materiality(kind: str, facts: dict, reported: dict) -> tuple[str, str]:
    if "not_material" in facts:
        return "not_material", f"stated ({facts['not_material']['passage_id']})"
    if "material" in facts:
        return "material", f"stated ({facts['material']['passage_id']})"
    if "pro_forma_revenue" in facts:
        return "material", "pro forma information is presented"
    assets, price = reported.get("total_assets"), (facts.get("consideration") or {}).get("value")
    if price is not None and assets:
        share = price / assets
        word = "price" if kind == "acquisition" else "proceeds"
        return (("not_material" if share < MATERIALITY_SHARE else "material"),
                f"not stated; by size: {word} {share:.1%} of total assets (not material under "
                f"{MATERIALITY_SHARE:.0%})")
    return "material", "not stated, and no price or proceeds to size it: treated as material"


def effects_for(deal: dict) -> dict:
    """What one deal does to DSO and Debt/EBITDAR: an adjustment, nothing (with why), or unresolved."""
    kind, timing, outcome, facts = deal["kind"], deal["timing"], deal["outcome"], deal["facts"]
    in_receivables = timing == "in_year" or deal["presentation"] == "held_for_sale"
    dso = leverage = None
    unresolved, notes = [], []
    if outcome == "pro_forma_revenue":
        pf = facts["pro_forma_revenue"]
        dso = {"action": "pro_forma", "pro_forma_revenue": pf["value"],
               "key": f"{pf['passage_id']}:{_norm(pf['amount'])}"}
        ebitdar, how = build_ebitdar(facts)
        if ebitdar is not None:
            leverage = {"ebitdar": ebitdar, "debt": 0.0, "how": f"acquired EBITDAR for the months before the "
                                                                   f"acquisition added ({how})"}
        else:
            notes.append("EBITDAR not adjusted: ASC 805 pro forma gives revenue and earnings, not EBITDAR, and the "
                         f"acquired business's missing months of EBITDAR can't be built from the 10-K ({how}).")
    elif outcome == "remove_divested_revenue":
        if in_receivables:
            dso = {"action": "remove", "revenue": facts["divested_revenue"]["value"]}
        else:
            notes.append("DSO not adjusted: sold after the year-end and not held for sale at it, so the year's "
                         "receivables and revenue both still include the business.")
    elif outcome == "unresolved" and (in_receivables or kind == "acquisition"):
        unresolved.append("dso")
    if kind == "divestiture" and timing == "after_year_end" and outcome in ("remove_divested_revenue", "unresolved"):
        ebitdar, how = build_ebitdar(facts)
        repaid = (facts.get("debt_repaid") or {}).get("value")
        if ebitdar is not None and repaid is not None:
            leverage = {"ebitdar": -ebitdar, "debt": -repaid,
                        "how": f"after the year-end: debt less the proceeds repaid and EBITDAR less the divested "
                               f"EBITDAR ({how}), together"}
        else:
            unresolved.append("leverage")
            notes.append("Debt/EBITDAR unresolved: a divestiture after the year-end adjusts debt (proceeds repaid) and "
                         "EBITDAR (divested EBITDAR) together, never one side, and "
                         + ("the divested EBITDAR can't be built from the 10-K (" + how + ")" if ebitdar is None
                            else "the debt repaid from the proceeds isn't disclosed")
                         + "; neither is adjusted.")
    return {"dso": dso, "leverage": leverage, "unresolved": unresolved, "notes": notes}


def validate_agent(parsed: dict, ad: dict, reported: dict) -> dict:
    """Keep the evidence that holds up, re-derive each deal's timing, presentation, materiality and outcome, and work
    out the adjustments. A deal without verified evidence of the deal itself is held back whole."""
    by_id = {p["passage_id"]: p for p in ad["passages"]}
    start, end = _date(reported["period_start"]), _date(reported["period_end"])
    deals, held_deals, held_evidence, notes = [], [], [], []
    for d in parsed.get("deals") or []:
        kind, did = d.get("kind"), d.get("deal_id") or f"D{len(deals) + len(held_deals) + 1}"
        facts, kept = {}, []
        for e in d.get("evidence") or []:
            fact, problem = check_evidence(e, by_id.get(e.get("passage_id")), kind, reported)
            if problem:
                held_evidence.append({"deal_id": did, **{k: e.get(k) for k in ("fact", "passage_id", "quote",
                                                                                "amount", "date")},
                                      "reason": problem})
                continue
            kept.append(fact)
            facts.setdefault(fact["fact"], fact)
        if "deal" not in facts:
            held_deals.append({"deal_id": did, "kind": kind, "description": d.get("description"),
                               **{k: d.get(k) for k in ("timing", "presentation", "outcome")},
                               "reason": "no verified quote discloses the deal"})
            continue
        dated = next((f for f in kept if f.get("date_parts")), None)
        timing = timing_for(tuple(dated["date_parts"]), start, end) if dated else None
        timing_basis = f"from the date {dated['date']} ({dated['passage_id']})" if timing else \
            "as the agent read it (no date verified in a quote)"
        timing = timing or d.get("timing")
        if kind == "acquisition":
            presentation = "not_applicable"
        elif "discontinued_operations" in facts:
            presentation = "discontinued_operations"
        elif "held_for_sale" in facts:
            presentation = "held_for_sale"
        else:
            presentation = "continuing"
        if kind == "divestiture" and d.get("presentation") in ("discontinued_operations", "held_for_sale") \
                and presentation != d["presentation"]:
            notes.append(f"{did}: the agent read it as {d['presentation'].replace('_', ' ')}, but no verified quote "
                         f"says so; read as {presentation.replace('_', ' ')}.")
        materiality, materiality_basis = _materiality(kind, facts, reported)
        deal = {"deal_id": did, "kind": kind, "description": d.get("description"), "timing": timing,
                "timing_basis": timing_basis, "presentation": presentation, "materiality": materiality,
                "materiality_basis": materiality_basis, "facts": facts, "evidence": kept,
                "stated": {k: d.get(k) for k in ("timing", "presentation", "materiality", "outcome")},
                "shared_with": SHARED_WITH}
        deal["outcome"] = derive_outcome(deal)
        deal["outcome_text"] = OUTCOME_TEXT[deal["outcome"]]
        deal["effects"] = effects_for(deal)
        if d.get("outcome") != deal["outcome"]:
            notes.append(f"{did}: the agent gave {str(d.get('outcome')).replace('_', ' ')}; the verified evidence "
                         f"gives {deal['outcome'].replace('_', ' ')}.")
        deals.append(deal)
    adjustments, unresolved = combine(deals, reported)
    status = ("unresolved" if unresolved else "adjusted" if any(adjustments.values()) else
              "no_adjustment" if deals else "none")
    return {"deals": deals, "held_back_deals": held_deals, "held_back_evidence": held_evidence,
            "adjustments": adjustments, "unresolved_measures": unresolved, "derived_status": status,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def combine(deals: list[dict], reported: dict) -> tuple[dict, list[str]]:
    """The deals' adjustments to the latest year's DSO revenue and Debt/EBITDAR, and the measures left unresolved.
    Pro forma revenue disclosed once for several acquisitions is counted once."""
    unresolved = sorted({m for d in deals for m in d["effects"]["unresolved"]})
    revenue = reported.get("revenue")
    dso, pro_forma, removed, items = None, {}, 0.0, []
    for d in deals:
        e = d["effects"]["dso"]
        if not e:
            continue
        if e["action"] == "pro_forma":
            pro_forma.setdefault(e["key"], e["pro_forma_revenue"])
        else:
            removed += e["revenue"]
        items.append(d["deal_id"])
    if items and revenue:
        added = sum(pf - revenue for pf in pro_forma.values())
        adjusted = revenue + added - removed
        if adjusted > 0:
            dso = {"period": reported["period"], "revenue_reported": revenue, "added": added, "removed": removed,
                   "revenue": adjusted, "deals": items}
        elif "dso" not in unresolved:
            unresolved.append("dso")
    lev = [(d["deal_id"], d["effects"]["leverage"]) for d in deals if d["effects"]["leverage"]]
    leverage = None
    if lev and "leverage" not in unresolved:
        leverage = {"period": reported["period"], "ebitdar": sum(e["ebitdar"] for _, e in lev),
                    "debt": sum(e["debt"] for _, e in lev), "deals": [i for i, _ in lev],
                    "how": "; ".join(f"{i}: {e['how']}" for i, e in lev)}
    return {"dso": dso, "leverage": leverage}, unresolved


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_ACQUISITIONS_PROMPT, template=FO_ACQUISITIONS_TEMPLATE, response_schema=FO_ACQUISITIONS_SCHEMA,
                      role="analyze", label="Financial Overview Pre-check Agent: acquisitions and divestitures")
    return registry


# --- the pre-check -------------------------------------------------------------------------------------

def acquisitions_precheck(data: dict, *, session=None, backend=None, mode: str = "live",
                          case_id: str | None = None) -> dict:
    """The acquisitions-and-divestitures pre-check result recorded in the run payload.

    Nothing collected: no deal, no model call. Otherwise the agent always interprets the passages; the keyword
    signals only cross-check. Without an agent, or when its output is rejected, ``PrecheckAgentError``."""
    ad = deal_passages(data)
    reported = reported_figures(data)
    signals = rule_signals(ad["passages"])
    result = {"check": "acquisitions", "version": AD_VERSION, "mode": mode if backend is not None else "none",
              "rules": {"signals": signals}, "agent": None, "notes": [], "info_flags": [], "reported": reported,
              "deals": [], "adjustments": {"dso": None, "leverage": None}, "unresolved_measures": [],
              "passages": ad["passages"], "sections": ad["sections"]}
    if not ad["passages"]:
        status, decided_by = "none", "no_hits"
        result["notes"].append("No acquisitions, dispositions, discontinued-operations or held-for-sale note, and no "
                               "deal mention: no deal for the year. The agent was not called (nothing to interpret).")
    else:
        require_agent(backend, "acquisitions")
        status, decided_by = _run_agent(result, ad, data, reported, session, backend, case_id)
    used = {(e["passage_id"], e["fact"]) for d in result["deals"] for e in d["evidence"]}
    used_passages = {pid for pid, _ in used}
    for s in signals:
        if s["passage_id"] not in used_passages and decided_by == "agent":
            result["notes"].append(f"The keyword rules found a {s['signal']} statement in {s['passage_id']} that no "
                                   f"verified deal uses: \"{_clip(s['quote'], 300)}\". Read it to confirm.")
    for d in result["deals"]:
        if d["outcome"] == "not_for_the_year" and d["timing"] == "after_year_end":
            result["info_flags"].append(f"{d['deal_id']}: an acquisition after the year-end ({d['description']}); not "
                                        "in this year's measures. Check later filings.")
        if "as the agent read it" in d["timing_basis"] and d["outcome"] != "not_for_the_year":
            result["info_flags"].append(f"{d['deal_id']}: timing ({d['timing'].replace('_', ' ')}) is the agent's "
                                        "reading; no date was verified in a quote.")
        result["notes"] += [f"{d['deal_id']}: {n}" for n in d["effects"]["notes"]]
    result.update(status=status, decided_by=decided_by, reason=STATUS_REASONS[status], effect=EFFECTS[status],
                  review_required=status == "unresolved")
    return result


def _run_agent(result, ad, data, reported, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_ACQUISITIONS_PROMPT,
                            agent_inputs(ad, data["meta"], reported))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Pre-check Agent's acquisitions-and-divestitures output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_agent(run.parsed, ad, reported)
    agent.update(rationale=run.parsed.get("rationale"),
                 missing_information=run.parsed.get("missing_information") or [],
                 **{k: checked[k] for k in ("held_back_deals", "held_back_evidence", "derived_status",
                                            "stated_status", "status_consistent")},
                 accepted_deals=[d["deal_id"] for d in checked["deals"]])
    result.update(deals=checked["deals"], adjustments=checked["adjustments"],
                  unresolved_measures=checked["unresolved_measures"])
    result["notes"] += checked["validation_notes"]
    held = len(checked["held_back_deals"]) + len(checked["held_back_evidence"])
    if held:
        result["notes"].append(f"{held} agent item(s) held back by validation.")
    if not checked["status_consistent"]:
        result["notes"].append(f"The agent stated {str(checked['stated_status']).upper()} but its verified evidence "
                               f"supports {checked['derived_status'].upper()}; the verified evidence decides.")
    return checked["derived_status"], "agent"


# --- the measures --------------------------------------------------------------------------------------

def apply_deal_adjustments(fo: list[dict], ad: dict | None) -> list[dict]:
    """The unscored measures with the deals' adjustments applied to the latest year (copies; the inputs stay
    untouched): DSO on the adjusted revenue, Debt/EBITDAR on the adjusted debt and EBITDAR. Each adjusted column keeps
    the reported figures; an unresolved measure is left as reported and marked for escalation."""
    if not ad:
        return fo
    adj = ad.get("adjustments") or {}
    unresolved = set(ad.get("unresolved_measures") or [])
    out = []
    for m in fo:
        if m["key"] not in ("dso", "leverage") or not m["columns"] or not (adj.get(m["key"]) or m["key"] in unresolved):
            out.append(m)
            continue
        m = copy.deepcopy(m)
        a, col, notes = adj.get(m["key"]), m["columns"][0], []
        if a and col["period"] == a["period"]:
            (_adjust_dso if m["key"] == "dso" else _adjust_leverage)(col, a, notes)
        if m["key"] in unresolved:
            m["deal_unresolved"] = True
            notes.append(UNRESOLVED_NOTE)
        m["deal_note"] = " ".join(notes)
        out.append(m)
    return out


def _adjust_dso(col, a, notes):
    if col.get("receivables") is None:
        notes.append("Acquisitions or divestitures: no receivables tagged, so nothing to recompute.")
        return
    col["revenue_reported"], col["dso_reported"] = col["revenue"], col["dso"]
    col["revenue"] = a["revenue"]
    col["dso"] = col["receivables"] / (a["revenue"] / 365)
    col["change"] = col["dso"] - col["prior_dso"] if col.get("prior_dso") is not None else None
    col["deal_adjustment"] = {k: a[k] for k in ("revenue_reported", "added", "removed", "revenue", "deals")}
    parts = ([f"+{a['added'] / 1e6:,.1f} million pro forma revenue"] if a["added"] else []) + \
            ([f"−{a['removed'] / 1e6:,.1f} million divested revenue"] if a["removed"] else [])
    notes.append(f"Acquisitions or divestitures (pre-check; {', '.join(a['deals'])}): {col['heading']} revenue "
                 f"{a['revenue_reported'] / 1e6:,.1f} million as reported, {' and '.join(parts)} = "
                 f"{a['revenue'] / 1e6:,.1f} million; DSO recomputed on it. The prior year is as reported.")


def _adjust_leverage(col, a, notes):
    if col.get("ebitdar") is None:
        notes.append("Acquisitions or divestitures: no EBITDAR for the year, so nothing to recompute.")
        return
    if a["debt"] and col.get("debt") is None:
        notes.append("Acquisitions or divestitures: no debt tagged, so debt and EBITDAR are left as reported.")
        return
    col["ebitdar_reported"], col["adj_debt_reported"] = col["ebitdar"], col.get("adj_debt")
    col["ebitdar"] += a["ebitdar"]
    if a["debt"]:
        col["debt"] += a["debt"]
        col["adj_debt"] += a["debt"]
    if col.get("adj_debt") is not None and col.get("cash") is not None:
        col["adj_net_debt"] = col["adj_debt"] - col["cash"]
    for key, _, numerator, denominator in LEVERAGE_MEASURES:
        num, den = col[numerator], col[denominator]
        col[key]["ratio"] = num / den if num is not None and den and den > 0 else None
        col[key]["negative_ebitda"] = den is not None and den <= 0
    col["deal_adjustment"] = {"ebitdar": a["ebitdar"], "debt": a["debt"], "deals": a["deals"]}
    notes.append(f"Acquisitions or divestitures (pre-check): {a['how']}. {col['heading']}: EBITDAR "
                 f"{col['ebitdar_reported'] / 1e6:,.1f} → {col['ebitdar'] / 1e6:,.1f} million"
                 + (f", debt + op. leases {col['adj_debt_reported'] / 1e6:,.1f} → {col['adj_debt'] / 1e6:,.1f} million"
                    if a["debt"] else "") + ".")
