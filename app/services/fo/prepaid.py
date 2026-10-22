"""FO Pre-check: prepaid business model (customers pay in advance).

When customers pay in advance, receivables are small and DSO says little about credit quality. The check applies
when the company has a prepaid (or mixed) business model, as the Pre-check Agent reads it from Item 1, or its
contract liabilities are at least its receivables. Same three steps as the other pre-checks:

1. ``prepaid_passages`` (deterministic) collects ONLY what the agent checks: the business description in Item 1
   (B01…) and the revenue note in full, tables as rows (R01…).
2. The Pre-check Agent (prompt ``fo_precheck_prepaid``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run): the business model (prepaid, mixed or billed), revenue by payer and contract liabilities, quoting each.
   Only a filing with neither Item 1 nor a revenue note has nothing to read (no model call).
3. ``validate_agent`` keeps only verbatim evidence whose amount is in its quote, accepts a prepaid model only on an
   Item 1 quote that says customers pay in advance (a mixed model also needs one saying services are billed), and
   RE-DERIVES the outcome, first match:

   - payer split disclosed: DSO recomputed on billed revenue, factor 1;
   - not disclosed, contract liabilities ≥ receivables: factor 0;
   - not disclosed, mixed model: factor 0.5;
   - otherwise (a prepaid model, contract liabilities below receivables, no payer split): unresolved, DSO
     escalated.

   The engine sets the DSO weight = 7.5% × factor and scales every other weight by (1 − DSO weight) ÷ (1 − 7.5%)
   (``scoring.composite_risk``).
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_PREPAID_PROMPT, FO_PREPAID_SCHEMA, FO_PREPAID_TEMPLATE

from .acquisitions import SCALE, SCALES, _verbatim, amount_in, parse_amount
from .covenants import NOTE_HEADING, OTHER_NOTE_TITLE, PAGE_HEADER
from .measures import days_sales_outstanding, pick, plain_values
from .precheck import DATA_NOTICE, PrecheckAgentError, _clip, _heading, _norm, call_agent, require_agent

PP_VERSION = "fo-prepaid-1.1.0"
FACTORS = {"billed_revenue": 1.0, "weight_zero": 0.0, "weight_half": 0.5}

CL_CONCEPTS = ["us-gaap:ContractWithCustomerLiability", "us-gaap:DeferredRevenue"]
CL_PARTS = [["us-gaap:ContractWithCustomerLiabilityCurrent", "us-gaap:DeferredRevenueCurrent"],
            ["us-gaap:ContractWithCustomerLiabilityNoncurrent", "us-gaap:DeferredRevenueNoncurrent"]]
REVENUE_TITLE = re.compile(r"\brevenues?\b", re.I)
# Without numbered notes only an unambiguous revenue-note title opens it (a statement line "Revenues" doesn't).
REVENUE_NOTE_TITLE = re.compile(r"revenue recognition|contracts? with customers|disaggregat\w* of revenue|"
                                r"revenues? and (receivables|contract)", re.I)
CL_WORDS = re.compile(r"contract liabilit|deferred revenue|advance (payments|billings)|customer deposits|"
                      r"resident deposits|unearned revenue|billed in advance|paid in advance|received in advance",
                      re.I)
MODEL_TERMS = re.compile(r"in advance|prepa(id|y)|private[- ]pay|resident|monthly (fee|rent)|entrance fee|"
                         r"subscription|deposit|up-?front|payor|payer|Medicare|Medicaid|reimburse|insur|\bbill",
                         re.I)
# What an Item 1 quote must say for the agent's reading of the model to count.
PREPAID_WORDS = re.compile(r"in advance|\bprepa\w*|advance payments?|up-?front|before (the )?(services?|month)|"
                           r"entrance fees?|subscriptions?|\bdeposits?\b|monthly (service )?(fees?|rent)", re.I)
BILLED_WORDS = re.compile(r"\bbill\w*|reimburs\w*|Medicare|Medicaid|insur\w*|third[- ]party|\bpayers?\b|\bpayors?\b|"
                          r"\bclaims\b|invoic\w*|after (the )?services", re.I)
ITEM1_FIRST, ITEM1_LIMIT, NOTE_LIMIT, PASSAGE_MAX_CHARS = 6, 14, 30, 2000
PAYER_WORDS = re.compile(r"[A-Za-z]{3,}")

OUTCOME_TEXT = {
    "not_applicable": "Not a prepaid or mixed business model, and contract liabilities below receivables: DSO as "
                      "normal",
    "billed_revenue": "Revenue split by payer disclosed: DSO recomputed on billed revenue, normal weight",
    "weight_zero": "Payer split not disclosed, contract liabilities ≥ receivables: DSO weight 0, spread pro rata",
    "weight_half": "Payer split not disclosed, mixed model: DSO weight 50%, spread pro rata",
    "unresolved": "A prepaid business model, but no payer split and contract liabilities below receivables: "
                  "unresolved",
}
EFFECTS = {
    "not_applicable": "None: DSO at its normal weight.",
    "billed_revenue": "DSO is recomputed on billed revenue (both years) and keeps its normal weight.",
    "weight_zero": "DSO weight = 7.5% × 0 = 0; every other weight × (1 − 0) ÷ (1 − 7.5%).",
    "weight_half": "DSO weight = 7.5% × 0.5 = 3.75%; every other weight × (1 − 3.75%) ÷ (1 − 7.5%).",
    "unresolved": "DSO runs as normal and is escalated to an analyst, who accepts its score or overrides it.",
}
UNRESOLVED_NOTE = ("Prepaid-model pre-check unresolved: DSO left as calculated and escalated to an analyst, who "
                   "accepts this score or overrides it.")
DECIDED_NOTE = "Prepaid-model pre-check unresolved (DSO as calculated); the analyst has decided this measure's score."


# --- the passages --------------------------------------------------------------------------------------

def prepaid_passages(data: dict) -> dict:
    """The business description (Item 1: its first paragraphs and those on how the company is paid) and the revenue
    note in full (numbered note "Revenue…", or a revenue sub-heading inside a note), tables joined as rows."""
    business, note, seen = [], [], set()

    def add(out, item, kind, text, scale=None):
        key = re.sub(r"\W+", "", text.lower())[:200]
        if key and key not in seen:
            seen.add(key)
            out.append({"item": item, "kind": kind, "text": _clip(text, PASSAGE_MAX_CHARS), "scale": scale})

    for sec in (s for s in data["document"] if s["item"] == "1"):
        paragraphs = [ln.strip() for ln in sec["text"].split("\n") if len(ln.strip()) >= 40 and " | " not in ln]
        for i, p in enumerate(paragraphs):
            if len(business) >= ITEM1_LIMIT:
                break
            if i < ITEM1_FIRST or MODEL_TERMS.search(p):
                add(business, "1", "business_model", p)
    found = False
    for sec in (s for s in data["document"] if s["item"] in ("8", "F", "")):
        lines = sec["text"].split("\n")
        numbered = any(NOTE_HEADING.match(ln.strip()) and _heading(ln.strip()) for ln in lines)
        state = {"open": False, "sub": False, "in_notes": False, "scale": None, "cells": []}

        def flush(item=sec["item"]):
            if state["open"] and len(state["cells"]) >= 2 and any(ch.isdigit() for c in state["cells"] for ch in c):
                add(note, item, "revenue_note", " | ".join(state["cells"]), state["scale"])
            state["cells"] = []

        for line in lines:
            s = line.strip()
            if not s or re.fullmatch(r"F-\d{1,3}", s) or (_heading(s) and PAGE_HEADER.search(s)):
                continue  # page numbers and running page headers
            if _heading(s) and " | " not in s:
                is_note = numbered and NOTE_HEADING.match(s)
                if is_note:
                    state["in_notes"] = True
                if not numbered and state["open"] and not REVENUE_NOTE_TITLE.search(s) and \
                        OTHER_NOTE_TITLE.search(s) and not REVENUE_TITLE.search(s):
                    flush()
                    state.update(open=False)  # the next note, in a filing without numbered notes
                    continue
                if is_note or (not numbered and REVENUE_NOTE_TITLE.search(s)):
                    flush()
                    state.update(open=bool(REVENUE_TITLE.search(s) if is_note else True), sub=False, scale=None)
                    found = found or state["open"]
                    continue
                if numbered and state["in_notes"] and not state["open"] and (
                        REVENUE_NOTE_TITLE.search(s) or re.search(r"^revenues?\b", s, re.I)):
                    flush()
                    state.update(open=True, sub=True, scale=None)  # a revenue sub-heading inside another note
                    found = True
                    continue
                if state["sub"] and len(re.findall(r"[A-Za-z]{2,}", s)) >= 3 and not REVENUE_TITLE.search(s) \
                        and not CL_WORDS.search(s) and not re.search(r"\d", s):
                    flush()
                    state.update(open=False, sub=False)
                    continue
                if state["open"]:
                    state["cells"].append(s)
                continue
            if not state["open"]:
                continue
            if SCALE.search(s):
                state["scale"] = SCALES[SCALE.search(s).group(1).lower().rstrip("s")]
            if len(s) < 40 or s.count(" | ") > 2:
                state["cells"].append(s)
                continue
            flush()
            if len(note) < NOTE_LIMIT:
                add(note, sec["item"], "revenue_note", s, state["scale"])
        flush()
    for prefix, group in (("B", business), ("R", note)):
        for i, p in enumerate(group, 1):
            p["passage_id"] = f"{prefix}{i:02d}"
    return {"business": business, "note": note, "revenue_note_found": found,
            "passages": business + note}


# --- the reported figures ------------------------------------------------------------------------------

def reported_figures(data: dict) -> dict:
    """The latest two years' revenue and receivables (as DSO uses them) and the tagged contract liabilities."""
    dso = days_sales_outstanding(data["facts"], data["labels"], data["sections"])
    cols = dso["columns"]
    cur = cols[0] if cols else {}
    values = plain_values(data["facts"])
    cl, cl_source = None, None
    if cur.get("as_at"):
        c, v, _ = pick(values, CL_CONCEPTS, cur["as_at"])
        if c:
            cl, cl_source = abs(v), c
        else:
            parts = [pick(values, group, cur["as_at"])[:2] for group in CL_PARTS]
            parts = [(c, v) for c, v in parts if c]
            if parts:
                cl, cl_source = sum(abs(v) for _, v in parts), " + ".join(c for c, _ in parts)
    return {"period": cur.get("period"), "period_end": (cur.get("period") or "--").split("--")[1] or None,
            "revenue": cur.get("revenue"), "receivables": cur.get("receivables"), "as_at": cur.get("as_at"),
            "prior_period": cols[1]["period"] if len(cols) > 1 else None,
            "prior_revenue": cols[1].get("revenue") if len(cols) > 1 else None,
            "contract_liabilities_tagged": cl, "contract_liabilities_concept": cl_source}


# --- the agent -----------------------------------------------------------------------------------------

def agent_inputs(pp: dict, meta: dict, reported: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "ticker": meta.get("ticker"),
                    "fiscal_year": meta.get("fiscal_year"), "period_end": reported["period_end"],
                    "reported_revenue": reported["revenue"], "receivables": reported["receivables"]},
        "revenue_note_found": pp["revenue_note_found"],
        "passages": [{"passage_id": p["passage_id"], "kind": p["kind"], "item": p["item"], "text": p["text"]}
                     for p in pp["passages"]],
    }


def _amount(e: dict, p: dict) -> tuple[float | None, str | None]:
    if not _norm(e.get("amount") or ""):
        return None, "no amount given"
    if not amount_in(e["amount"], e.get("quote") or ""):
        return None, f"the amount {e['amount']} is not in the quote"
    return parse_amount(e["amount"], p.get("scale"))


def read_model(stated: str | None, quotes: list[dict]) -> tuple[str | None, str, str | None]:
    """(model, basis, note): the agent's reading of the business model as its verified Item 1 quotes support it. A
    prepaid model needs a quote saying customers pay in advance; a mixed one needs that and a quote saying services
    are billed (government programs, insurers, other payers)."""
    prepaid = [q["passage_id"] for q in quotes if PREPAID_WORDS.search(q["quote"])]
    billed = [q["passage_id"] for q in quotes if BILLED_WORDS.search(q["quote"])]

    def where(ids):
        return ", ".join(dict.fromkeys(ids))

    if stated == "mixed" and prepaid and billed:
        return "mixed", f"Item 1: paid in advance ({where(prepaid)}) and billed ({where(billed)})", None
    if stated in ("prepaid", "mixed") and prepaid:
        note = (None if stated == "prepaid" else
                "The agent read the model as mixed, but no verified Item 1 quote says services are billed; read as "
                "prepaid.")
        return "prepaid", f"Item 1: paid in advance ({where(prepaid)})", note
    if stated in ("prepaid", "mixed"):
        return None, "not established", (f"The agent read the model as {stated}, but no verified Item 1 quote says "
                                         "customers pay in advance; not read as prepaid.")
    if stated == "billed":
        return "billed", f"the agent's reading of Item 1 ({where(q['passage_id'] for q in quotes) or 'no quote'})", None
    return None, "not stated", None


def validate_agent(parsed: dict, pp: dict, reported: dict) -> dict:
    """Keep the evidence that holds up, decide whether the check applies and re-derive the outcome."""
    by_id = {p["passage_id"]: p for p in pp["passages"]}
    held, notes = [], []

    def check(e, kind, label):
        p = by_id.get(e.get("passage_id"))
        if p is None:
            return None, "cites a passage that was not supplied"
        if p["kind"] != kind:
            return None, f"{label} must come from the {'Item 1 business description' if kind == 'business_model' else 'revenue note'}"
        if not _verbatim(e.get("quote") or "", p["text"]):
            return None, "quote is not verbatim in the cited passage"
        return p, None

    model_quotes = []
    for e in parsed.get("model_evidence") or []:
        p, why = check(e, "business_model", "business-model evidence")
        if why:
            held.append({"fact": "model", **e, "reason": why})
        else:
            model_quotes.append({"passage_id": p["passage_id"], "quote": e["quote"]})

    payers = []
    for e in parsed.get("payer_revenue") or []:
        p, why = check(e, "revenue_note", "revenue by payer")
        value = None
        if not why:
            value, why = _amount(e, p)
        if not why and not any(w.lower() in e["quote"].lower() for w in PAYER_WORDS.findall(e.get("payer") or "")):
            why = "the payer isn't named in the quote"
        if why:
            held.append({"fact": "payer_revenue", **e, "reason": why})
        else:
            payers.append({**{k: e[k] for k in ("payer", "basis", "year", "passage_id", "quote", "amount")},
                           "value": value})

    cl, cl_source = reported["contract_liabilities_tagged"], reported["contract_liabilities_concept"]
    agent_cl = parsed.get("contract_liabilities")
    cl_evidence = None
    if agent_cl:
        p, why = check(agent_cl, "revenue_note", "contract liabilities")
        value = None
        if not why:
            value, why = _amount(agent_cl, p)
        if not why and not CL_WORDS.search(agent_cl["quote"]) and not CL_WORDS.search(p["text"]):
            why = "quote doesn't name contract liabilities or deferred revenue"
        if why:
            held.append({"fact": "contract_liabilities", **agent_cl, "reason": why})
        else:
            cl_evidence = {**agent_cl, "value": value}
            if cl is None:
                cl, cl_source = value, f"revenue note ({agent_cl['passage_id']})"
    rec = reported["receivables"]
    cl_ge_rec = cl is not None and rec is not None and cl >= rec

    model, model_basis, note = read_model(parsed.get("model"), model_quotes)
    if note:
        notes.append(note)

    billed = {y: [x for x in payers if x["basis"] == "billed" and x["year"] == y] for y in ("current", "prior")}
    split = bool(billed["current"])
    billed_total = {y: sum(x["value"] for x in rows) for y, rows in billed.items() if rows}
    if split and reported["revenue"] and billed_total["current"] > reported["revenue"]:
        notes.append("The billed payer lines add up to more than the reported revenue: the payer split is not used.")
        split = False
    applies = model in ("prepaid", "mixed") or cl_ge_rec
    if not applies:
        status = "not_applicable"
    elif split:
        status = "billed_revenue"
    elif cl_ge_rec:
        status = "weight_zero"
    elif model == "mixed":
        status = "weight_half"
    else:
        status = "unresolved"
    return {"status": status, "factor": FACTORS.get(status), "applies": applies, "model": model,
            "model_basis": model_basis, "model_evidence": model_quotes,
            "payer_revenue": payers, "billed_revenue": billed_total if split else {},
            "payer_split_disclosed": split, "contract_liabilities": cl, "contract_liabilities_source": cl_source,
            "contract_liabilities_evidence": cl_evidence, "receivables": rec, "cl_ge_receivables": cl_ge_rec,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_PREPAID_PROMPT, template=FO_PREPAID_TEMPLATE, response_schema=FO_PREPAID_SCHEMA,
                      role="analyze", label="Financial Overview Pre-check Agent: prepaid model")
    return registry


# --- the pre-check -------------------------------------------------------------------------------------

def prepaid_precheck(data: dict, *, session=None, backend=None, mode: str = "live",
                     case_id: str | None = None) -> dict:
    """The prepaid-model pre-check result recorded in the run payload. The agent reads every filing's Item 1 and
    revenue note: whether the business model is prepaid is its reading (checked against its quotes), not a list."""
    pp = prepaid_passages(data)
    reported = reported_figures(data)
    result = {"check": "prepaid", "version": PP_VERSION, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "reported": reported, "passages": pp["passages"],
              "revenue_note_found": pp["revenue_note_found"]}
    if not pp["revenue_note_found"]:
        result["notes"].append("No revenue note heading was found; the payer split and contract liabilities rest on "
                               "the tagged facts only.")
    if not pp["passages"]:
        cl, rec = reported["contract_liabilities_tagged"], reported["receivables"]
        cl_ge_rec = cl is not None and rec is not None and cl >= rec
        status = "weight_zero" if cl_ge_rec else "not_applicable"
        checked = {"status": status, "factor": FACTORS.get(status), "applies": cl_ge_rec, "model": None,
                   "model_basis": "no Item 1 to read", "model_evidence": [], "payer_revenue": [],
                   "billed_revenue": {}, "payer_split_disclosed": False, "contract_liabilities": cl,
                   "contract_liabilities_source": reported["contract_liabilities_concept"],
                   "contract_liabilities_evidence": None, "receivables": rec, "cl_ge_receivables": cl_ge_rec}
        decided_by = "no_text"
        result["notes"].append("Neither Item 1 nor a revenue note was found: nothing for the agent to read, so it "
                               "was not called; only the tagged contract liabilities and receivables were compared.")
    else:
        require_agent(backend, "prepaid")
        checked, decided_by = _run_agent(result, pp, data, reported, session, backend, case_id)
    status = checked["status"]
    result.update({k: checked[k] for k in ("status", "factor", "applies", "model", "model_basis",
                                            "model_evidence", "payer_revenue", "billed_revenue",
                                            "payer_split_disclosed", "contract_liabilities",
                                            "contract_liabilities_source", "contract_liabilities_evidence",
                                            "receivables", "cl_ge_receivables")},
                  decided_by=decided_by, reason=OUTCOME_TEXT[status], effect=EFFECTS[status],
                  review_required=status == "unresolved")
    if status == "billed_revenue" and "prior" not in checked["billed_revenue"]:
        result["notes"].append("Billed revenue is disclosed for the fiscal year only: the prior year's DSO can't be "
                               "put on the same basis, so the DSO change isn't scored and its weight is spread over "
                               "the other measures.")
    return result


def _run_agent(result, pp, data, reported, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_PREPAID_PROMPT,
                            agent_inputs(pp, data["meta"], reported))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Pre-check Agent's prepaid-model output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_agent(run.parsed, pp, reported)
    agent.update(rationale=run.parsed.get("rationale"), stated_model=run.parsed.get("model"),
                 missing_information=run.parsed.get("missing_information") or [],
                 held_back=checked["held_back"], derived_status=checked["status"],
                 stated_status=checked["stated_status"], status_consistent=checked["status_consistent"])
    result["notes"] += checked["validation_notes"]
    if checked["held_back"]:
        result["notes"].append(f"{len(checked['held_back'])} agent item(s) held back by validation.")
    if not checked["status_consistent"]:
        result["notes"].append(f"The agent stated {str(checked['stated_status']).upper()} but its verified evidence "
                               f"supports {checked['status'].upper()}; the verified evidence decides.")
    return checked, "agent"


# --- the measure ---------------------------------------------------------------------------------------

def apply_prepaid_adjustments(fo: list[dict], pp: dict | None) -> list[dict]:
    """DSO with the prepaid-model outcome (a copy; the inputs stay untouched): billed revenue replaces revenue in
    both years when the payer split is disclosed; the weight factor (``prepaid_factor``) the composite applies; an
    unresolved outcome marks DSO for escalation."""
    if not pp or pp["status"] == "not_applicable":
        return fo
    out = []
    for m in fo:
        if m["key"] != "dso":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        m["prepaid_factor"] = pp["factor"]
        notes = [f"Prepaid model (pre-check): {pp['reason']}."]
        if pp["status"] == "billed_revenue":
            billed = pp["billed_revenue"]
            by_period = {pp["reported"]["period"]: billed.get("current"),
                         pp["reported"]["prior_period"]: billed.get("prior")}
            for col in m["columns"]:
                rev = by_period.get(col["period"])
                col["revenue_total"] = col["revenue"]
                if rev:
                    col["revenue"], col["revenue_basis"] = rev, "billed revenue (payer split)"
                    col["dso"] = col["receivables"] / (rev / 365) if col.get("receivables") is not None else None
                else:
                    col["dso"], col["revenue_basis"] = None, "no billed revenue disclosed for this year"
            for col, prior in zip(m["columns"], m["columns"][1:] + [None]):
                col["prior_dso"] = prior["dso"] if prior else None
                col["change"] = (col["dso"] - col["prior_dso"]
                                 if col["dso"] is not None and col["prior_dso"] is not None else None)
                if col["change"] is None:
                    col["reason"] = ("No prior-year billed revenue: the change in DSO on billed revenue can't be "
                                     "worked out" if col["dso"] is not None else
                                     "No billed revenue disclosed for this year")
                else:
                    col["reason"] = None
            if any(c.get("deal_adjustment") for c in m["columns"]):
                notes.append("The acquisitions-and-divestitures adjustment to DSO revenue is superseded by the billed "
                             "revenue from the payer split.")
            notes.append("DSO recomputed on billed revenue; normal weight.")
        elif pp["status"] == "unresolved":
            m["prepaid_unresolved"] = True
            notes.append(UNRESOLVED_NOTE)
        else:
            notes.append(f"DSO weight × {pp['factor']:g}; the other weights scaled up pro rata.")
        m["prepaid_note"] = " ".join(notes)
        out.append(m)
    return out
