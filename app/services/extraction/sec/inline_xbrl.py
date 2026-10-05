"""Inline-XBRL observations: transform/scale/sign once, retaining exact lineage."""

import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from app.core.hashing import content_hash
from app.services.extraction.base import ParserError
from .contexts import parse_contexts, parse_units
from .linkbases import LABEL_ROLE, humanize
from .xml import NS, is_inline, qname, raw_text, rich_text, safe_xml


def parse_number(text, fmt=None):
    fmt = (fmt or "").split(":")[-1]
    text = text.replace("\xa0", " ").strip()
    if fmt in {"fixed-zero", "zerodash", "numdash"}:
        return Decimal(0)
    if fmt in {"numwordsen", "num-words-en"}:
        words = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "twelve": 12,
        }
        if text.lower() in words:
            return Decimal(words[text.lower()])
    elif fmt in {"num-comma-decimal", "numdotcomma", "numcomma"}:
        text = text.replace(".", "").replace(" ", "").replace(",", ".")
    elif fmt in {"", "num-dot-decimal", "numcommadot", "numdotdecimal"}:
        text = text.replace(",", "").replace(" ", "")
    else:
        raise ValueError(f"Unsupported numeric transformation {fmt!r}.")
    if not re.fullmatch(r"[+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
        raise ValueError(f"Unrecognized numeric text {text[:80]!r}.")
    return Decimal(text)


def transform_text(text, fmt=None):
    fmt = (fmt or "").split(":")[-1]
    text = " ".join(text.split())
    if fmt == "fixed-true":
        return "true"
    if fmt == "fixed-false":
        return "false"
    if fmt in {"date-monthname-day-year-en", "date-monthname-day-en"}:
        for pattern in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
            try:
                value = datetime.strptime(
                    text if "year" in fmt else text + " 2000", pattern
                )
                return (
                    value.date().isoformat()
                    if "year" in fmt
                    else value.strftime("--%m-%d")
                )
            except ValueError:
                pass
    return text


def parse_facts(root, contexts, units, labels):
    continuations = {}
    for node in root.iter():
        if is_inline(node, "continuation"):
            if node.get("id") in continuations:
                raise ParserError("Duplicate continuation ID.")
            continuations[node.get("id")] = node
    facts, warnings = [], []
    ids = set()
    for index, node in enumerate(root.iter()):
        numeric = is_inline(node, "nonFraction")
        if not numeric and not is_inline(node, "nonNumeric"):
            continue
        if len(facts) >= 100000:
            raise ParserError("SEC document exceeds fact limit.")
        context_id = node.get("contextRef")
        if context_id not in contexts:
            raise ParserError(f"Unknown context {context_id!r}.")
        element_id = node.get("id")
        if element_id and element_id in ids:
            raise ParserError(f"Duplicate inline fact ID {element_id!r}.")
        ids.add(element_id) if element_id else None
        concept = qname(node.get("name"), node)
        if not concept:
            raise ParserError("Inline fact lacks concept.")
        context = contexts[context_id]
        nil = node.get(f"{{{NS['xsi']}}}nil") in {"true", "1"}
        text, value, error = raw_text(node), None, None
        if numeric:
            unit = units.get(node.get("unitRef"))
            if unit is None:
                raise ParserError("Numeric fact references an unknown unit.")
            if not nil:
                try:
                    scale = int(node.get("scale", "0"))
                    if abs(scale) > 18 or node.get("sign") not in {None, "-"}:
                        raise ValueError("Invalid scale/sign.")
                    value = float(parse_number(text, node.get("format")).scaleb(scale))
                    if node.get("sign") == "-":
                        value = -value
                    if not math.isfinite(value):
                        raise ValueError("Non-finite numeric value.")
                except (ValueError, InvalidOperation, OverflowError) as exc:
                    value = None
                    error = str(exc)
                    warnings.append(f"{element_id or index}: {error}")
        else:
            unit = None
            pieces, seen, nxt = [node], set(), node.get("continuedAt")
            while nxt:
                if nxt in seen or len(seen) >= 100 or nxt not in continuations:
                    raise ParserError(
                        "Missing, cyclic or excessive continuation chain."
                    )
                seen.add(nxt)
                next_node = continuations[nxt]
                pieces.append(next_node)
                nxt = next_node.get("continuedAt")
            rich = concept.endswith("TextBlock") or node.get("escape") in {"true", "1"}
            text = ("\n" if rich else "").join(
                (rich_text if rich else raw_text)(part) for part in pieces
            )
            if len(text) > 2_000_000:
                raise ParserError("Text block exceeds size limit.")
            # Escaped markup is converted to readable evidence, never executed.
            if rich and "<" in text:
                text = rich_text(
                    safe_xml(("<div>" + text + "</div>").encode(), html=True)
                )
            value = None if nil else transform_text(text, node.get("format"))
        period = context["period"]
        observation = {
            "concept": concept,
            "label": labels.get(concept, {}).get(LABEL_ROLE) or humanize(concept),
            "context_id": context_id,
            "entity": context["entity"],
            "entity_scheme": context["entity_scheme"],
            "dimensions": context["dimensions"],
            "period": period,
            "period_key": period.get("date") or f"{period['start']}--{period['end']}",
            "inline_element_id": element_id,
            "location": index,
            "raw_text": text,
            "value": value,
            "unit": unit,
            "nil": nil,
            "error": error,
            "format": node.get("format"),
            "scale": node.get("scale", "0"),
            "sign": node.get("sign"),
            "decimals": node.get("decimals"),
            "type": "numeric"
            if numeric
            else "text_block"
            if concept.endswith("TextBlock")
            else "text",
        }
        observation["observation_id"] = content_hash(observation)
        facts.append(observation)
    if not facts:
        raise ParserError("No Inline-XBRL facts found.")
    return facts, warnings


class InlineXbrlParser:
    version = "sec-inline-xbrl-1.0.0"

    def parse(self, data, *, labels=None):
        root = safe_xml(data)
        contexts, units = parse_contexts(root), parse_units(root)
        facts, warnings = parse_facts(root, contexts, units, labels or {})
        return {
            "contexts": contexts,
            "units": units,
            "facts": facts,
            "warnings": warnings,
            "document_text": rich_text(root),
            "parser_version": self.version,
        }
