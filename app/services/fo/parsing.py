"""Inline-XBRL filing folder -> the facts, sections and text the FO engine reads.

Ported from the "10-K with FO" project (``credit_memo_app.py``: ``locate_files``,
``load_filing`` and their helpers) so the Financial Overview computes exactly the
same figures. The kirotesting SEC parser (``app.services.extraction.sec``) is
stricter and keeps a different fact shape, so it is not reused here; nothing in
it is changed. XML is parsed without entity resolution or network access.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from lxml import etree

NS = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
XLINK = "{%s}" % NS["xlink"]
IX = "{%s}" % NS["ix"]
LINK = "{%s}" % NS["link"]

LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
TERSE_ROLE = "http://www.xbrl.org/2003/role/terseLabel"

BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
CELL_TAGS = {"td", "th"}
DIMENSION_SUFFIXES = ("Axis", "Member", "Domain", "Table", "LineItems")

WORD_NUMBERS = {
    "no": 0, "none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}

OPERATING_CASH_FLOW_CONCEPTS = ["us-gaap:NetCashProvidedByUsedInOperatingActivities",
                                "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                                "ifrs-full:CashFlowsFromUsedInOperatingActivities"]
COMPANY_FILE = "company.json"


def _parser():
    # recover/huge_tree as in the FO project; never resolve entities or fetch DTDs.
    return etree.XMLParser(huge_tree=True, recover=True, resolve_entities=False,
                           no_network=True, load_dtd=False)


def _parse(path):
    return etree.parse(str(path), _parser()).getroot()


# =============================================================== text helpers

def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def raw_text(el):
    """Concatenated text of an element, skipping ix:exclude content."""
    parts = []

    def walk(node):
        if node.tag == IX + "exclude":
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return "".join(parts)


def rich_text(el):
    """Readable plain text from (X)HTML content: block breaks, ' | ' between table cells."""
    parts = []

    def walk(node):
        # ix:header holds the hidden contexts/units, which would otherwise leak ID strings into the text.
        if not isinstance(node.tag, str) or node.tag in (IX + "exclude", IX + "header"):
            return
        name = local(node.tag).lower()
        if name in BLOCK_TAGS:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if name in CELL_TAGS:
            parts.append(" | ")
        elif name in BLOCK_TAGS:
            parts.append("\n")

    walk(el)
    text = "".join(parts).replace("\xa0", " ")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line)
        line = re.sub(r"(\s*\|\s*)+", " | ", line).strip(" |")
        line = line.replace("$ | ", "$").replace(" | %", "%").replace(" | )", ")")
        if line:
            lines.append(line)
    return "\n".join(lines)


def humanize(concept):
    """Fallback label for concepts the filer's label linkbase doesn't cover."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", concept.split(":")[-1])


# =============================================================== value transforms

def _num(t):
    m = re.search(r"-?\d+(?:\.\d+)?", t.replace(",", ""))
    return float(m.group()) if m else None


def _word_or_num(t):
    for word in re.findall(r"[a-z]+", t.lower()):
        if word in WORD_NUMBERS:
            return WORD_NUMBERS[word]
    return _num(t)


def parse_number(text, fmt):
    fmt = (fmt or "").split(":")[-1]
    t = text.strip()
    if fmt == "fixed-zero" or t in {"-", "—", "–"}:
        return 0.0
    if fmt == "numwordsen":
        n = _word_or_num(t)
        return float(n) if n is not None else None
    if fmt == "num-comma-decimal":
        t = t.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace(" ", "")
    return _num(t)


def transform_text(text, fmt):
    """Normalise ixt/ixt-sec transformed non-numeric values; return raw text otherwise."""
    from datetime import datetime

    f = (fmt or "").split(":")[-1]
    t = " ".join(text.split())
    if f == "fixed-true":
        return True
    if f == "fixed-false":
        return False
    if f == "date-monthname-day-year-en":
        for pattern in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(t, pattern).date().isoformat()
            except ValueError:
                pass
    if f == "date-monthname-day-en":
        for pattern in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(t + " 2000", pattern).strftime("--%m-%d")
            except ValueError:
                pass
    if f in {"duryear", "durday", "durwordsen", "numwordsen"}:
        n = _word_or_num(t)
        if n is not None:
            n = int(n) if float(n).is_integer() else n
            return {"duryear": f"P{n}Y", "durday": f"P{n}D"}.get(f, n)
    return t


def clean_number(v):
    return int(v) if v is not None and float(v).is_integer() and abs(v) < 1e18 else v


# =============================================================== file discovery

def locate_files(folder):
    """Find the inline XBRL document, its schema and the label/presentation/calculation linkbases."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Not a folder: {folder}")

    # The instance is whichever .htm declares the inline XBRL namespace (exhibits don't). Proxy
    # statements carry inline XBRL too (pay-versus-performance), so they rank last.
    candidates = []
    for p in list(folder.glob("*.htm")) + list(folder.glob("*.html")) + list(folder.glob("*.xhtml")):
        data = p.read_bytes()
        if NS["ix"].encode() in data:
            doc_type = re.search(rb'name="dei:DocumentType"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)', data)
            is_proxy = bool(doc_type and b"14A" in doc_type.group(1))
            candidates.append((not is_proxy, len(data), p, data))
    if not candidates or not any(c[0] for c in candidates):
        raise FileNotFoundError(
            "No Inline XBRL 10-K/10-Q document found in this folder. (Older filings that use a "
            "standalone .xml instance are not supported.)")
    _, _, instance, data = max(candidates, key=lambda c: c[:2])

    # Prefer the schema the instance itself references.
    xsd = None
    m = re.search(rb'schemaRef[^>]*?href="([^"]+)"', data)
    if m:
        ref = folder / Path(m.group(1).decode()).name
        if ref.exists():
            xsd = ref
    if xsd is None:
        xsds = sorted(folder.glob("*.xsd"))
        if not xsds:
            raise FileNotFoundError("No .xsd schema found in this folder.")
        xsd = xsds[0]

    # Linkbases are declared in the schema; fall back to filename conventions.
    linkbases = {}
    schema = _parse(xsd)
    for ref in schema.iter(LINK + "linkbaseRef"):
        href = Path(ref.get(XLINK + "href", "")).name
        role = ref.get(XLINK + "role", "")
        path = folder / href
        if not path.exists():
            continue
        if "label" in role or href.endswith("_lab.xml"):
            linkbases["label"] = path
        elif "presentation" in role or href.endswith("_pre.xml"):
            linkbases["presentation"] = path
        elif "calculation" in role or href.endswith("_cal.xml"):
            linkbases["calculation"] = path
    for kind, suffix in (("label", "_lab.xml"), ("presentation", "_pre.xml"), ("calculation", "_cal.xml")):
        if kind not in linkbases:
            found = sorted(folder.glob(f"*{suffix}"))
            if found:
                linkbases[kind] = found[0]
            elif next(schema.iter(LINK + kind + "Link"), None) is not None:  # embedded in the schema itself
                linkbases[kind] = xsd

    return {"instance": instance, "schema": xsd, **linkbases}


# =============================================================== linkbases

def concept_from_href(href):
    frag = href.split("#", 1)[-1]
    prefix, _, name = frag.partition("_")
    return f"{prefix}:{name}"


def load_labels(path):
    labels = defaultdict(dict)  # concept -> {role: text}
    if not path or not Path(path).exists():
        return labels
    root = _parse(path)
    for link in root.iter(LINK + "labelLink"):
        locs = {l.get(XLINK + "label"): concept_from_href(l.get(XLINK + "href"))
                for l in link.iter(LINK + "loc")}
        resources = defaultdict(list)
        for lab in link.iter(LINK + "label"):
            resources[lab.get(XLINK + "label")].append(lab)
        for arc in link.iter(LINK + "labelArc"):
            concept = locs.get(arc.get(XLINK + "from"))
            for lab in resources.get(arc.get(XLINK + "to"), []):
                labels[concept][lab.get(XLINK + "role", LABEL_ROLE)] = " ".join((lab.text or "").split())
    return labels


def load_role_definitions(xsd_path):
    roles = {}
    root = _parse(xsd_path)
    for rt in root.iter(LINK + "roleType"):
        d = rt.find("link:definition", NS)
        roles[rt.get("roleURI")] = (d.text or "").strip() if d is not None else rt.get("roleURI")
    return roles


def load_presentation(path):
    """role -> list of root nodes; node = {concept, preferred_label, children}."""
    trees = {}
    if not path or not Path(path).exists():
        return trees
    root = _parse(path)
    for link in root.iter(LINK + "presentationLink"):
        role = link.get(XLINK + "role")
        locs = {l.get(XLINK + "label"): concept_from_href(l.get(XLINK + "href"))
                for l in link.iter(LINK + "loc")}
        children = defaultdict(list)
        has_parent = set()
        for arc in link.iter(LINK + "presentationArc"):
            frm, to = arc.get(XLINK + "from"), arc.get(XLINK + "to")
            children[frm].append((float(arc.get("order", 0)), to, arc.get("preferredLabel")))
            has_parent.add(to)

        def build(loc_label, preferred=None, seen=()):
            node = {"concept": locs[loc_label], "preferred_label": preferred, "children": []}
            if loc_label in seen:
                return node
            for _, to, pref in sorted(children.get(loc_label, []), key=lambda a: (a[0], a[1], a[2] or "")):
                node["children"].append(build(to, pref, seen + (loc_label,)))
            return node

        roots = [l for l in locs if l not in has_parent and l in children]
        trees[role] = [build(r) for r in roots]
    return trees


def load_calculations(path):
    """role -> {parent concept: [(child concept, weight)]} from the calculation linkbase."""
    calcs = {}
    if not path or not Path(path).exists():
        return calcs
    root = _parse(path)
    for link in root.iter(LINK + "calculationLink"):
        locs = {l.get(XLINK + "label"): concept_from_href(l.get(XLINK + "href"))
                for l in link.iter(LINK + "loc")}
        children = calcs.setdefault(link.get(XLINK + "role"), defaultdict(list))
        for arc in link.iter(LINK + "calculationArc"):
            frm, to = locs.get(arc.get(XLINK + "from")), locs.get(arc.get(XLINK + "to"))
            if frm and to:
                children[frm].append((to, float(arc.get("weight", 1))))
    return calcs


def working_capital_lines(calcs, cfo_concepts):
    """{concept: weight} for the working-capital changes in operating cash flow: the IncreaseDecreaseIn... lines the
    calculation linkbase adds up into operating cash flow, with the weight (+1 / -1) each carries into it. A
    working-capital subtotal counts once, without its parts. The calculation network with the most such lines wins."""
    best = {}
    for children in calcs.values():
        found = {}

        def walk(concept, weight, seen):
            for child, w in children.get(concept, []):
                if child in seen:
                    continue
                if child.split(":")[-1].startswith("IncreaseDecreaseIn"):
                    found.setdefault(child, weight * w)
                else:
                    walk(child, weight * w, seen | {child})

        for cfo in cfo_concepts:
            if cfo in children:
                walk(cfo, 1.0, {cfo})
        if len(found) > len(best):
            best = found
    return best


# =============================================================== instance

def parse_contexts(doc):
    contexts = {}
    for ctx in doc.iter("{%s}context" % NS["xbrli"]):
        period = ctx.find("xbrli:period", NS)
        instant = period.findtext("xbrli:instant", namespaces=NS)
        if instant:
            p = {"type": "instant", "date": instant}
        else:
            p = {"type": "duration",
                 "start": period.findtext("xbrli:startDate", namespaces=NS),
                 "end": period.findtext("xbrli:endDate", namespaces=NS)}
        dims = {}
        for m in ctx.iterfind(".//xbrldi:explicitMember", NS):
            dims[m.get("dimension")] = m.text.strip()
        for m in ctx.iterfind(".//xbrldi:typedMember", NS):
            dims[m.get("dimension")] = raw_text(m).strip()
        contexts[ctx.get("id")] = {
            "entity": ctx.findtext(".//xbrli:identifier", namespaces=NS),
            "period": p,
            "dimensions": dims,
        }
    return contexts


def parse_units(doc):
    units = {}
    for u in doc.iter("{%s}unit" % NS["xbrli"]):
        num = [m.text.split(":")[-1] for m in u.iterfind("xbrli:divide/xbrli:unitNumerator/xbrli:measure", NS)]
        den = [m.text.split(":")[-1] for m in u.iterfind("xbrli:divide/xbrli:unitDenominator/xbrli:measure", NS)]
        if num:
            units[u.get("id")] = "/".join(["*".join(num), "*".join(den)])
        else:
            units[u.get("id")] = "*".join(m.text.split(":")[-1] for m in u.iterfind("xbrli:measure", NS))
    return units


def period_key(p):
    return p["date"] if p["type"] == "instant" else f"{p['start']}--{p['end']}"


def concept_label(labels, concept):
    ls = labels.get(concept, {})
    return ls.get(LABEL_ROLE) or ls.get(TERSE_ROLE) or humanize(concept)


def ix_amount(el):
    """An ix:nonFraction element's value, scaled and signed as tagged; None when it isn't a number."""
    value = parse_number(raw_text(el), el.get("format"))
    if value is None:
        return None
    value = float(Decimal(repr(value)).scaleb(int(el.get("scale", "0"))))
    return -value if el.get("sign") == "-" else value


def parse_facts(doc, contexts, units, labels):
    continuations = {c.get("id"): c for c in doc.iter(IX + "continuation")}
    facts = []
    seen = set()

    for el in doc.iter(IX + "nonFraction", IX + "nonNumeric"):
        concept = el.get("name")
        ctx = contexts.get(el.get("contextRef"))
        if ctx is None:
            continue
        fmt = el.get("format")
        nil = el.get("{%s}nil" % NS["xsi"]) == "true"
        fact = {
            "id": el.get("id"),
            "concept": concept,
            "label": concept_label(labels, concept),
            "period": ctx["period"],
            "period_key": period_key(ctx["period"]),
            "dimensions": ctx["dimensions"],
        }
        if el.tag == IX + "nonFraction":
            value = None if nil else ix_amount(el)
            fact.update({
                "type": "numeric",
                "value": clean_number(value),
                "unit": units.get(el.get("unitRef")),
                "decimals": el.get("decimals"),
            })
        else:
            # Follow the continuation chain for text split across the document (bounded, cycle-safe).
            pieces, chain = [el], set()
            nxt = el.get("continuedAt")
            while nxt and nxt in continuations and nxt not in chain:
                chain.add(nxt)
                pieces.append(continuations[nxt])
                nxt = continuations[nxt].get("continuedAt")
            if concept.endswith("TextBlock") or el.get("escape") == "true":
                value = "\n".join(rich_text(p) for p in pieces)
            else:
                value = transform_text(" ".join(raw_text(p) for p in pieces), fmt)
            fact.update({"type": "text_block" if concept.endswith("TextBlock") else "text",
                         "value": value})

        dedupe_key = (concept, el.get("contextRef"), el.get("unitRef"), json.dumps(fact["value"]))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        facts.append(fact)
    return facts


# =============================================================== sections

def classify_statement(category, title):
    if category.lower() != "statement":
        return None
    t = title.lower()
    if "cash flow" in t:
        kind = "cash_flow"
    elif re.search(r"balance sheet|financial position|financial condition", t):
        kind = "balance_sheet"
    elif re.search(r"equity|stockholders|shareholders|partners'? capital|members'? capital", t):
        kind = "equity"
    elif "comprehensive" in t and not re.search(r"operations|earnings|statements? of income|income and comprehensive", t):
        kind = "comprehensive_income"
    elif re.search(r"operations|income|earnings|loss", t):
        kind = "income_statement"
    else:
        kind = "other_statement"
    return kind + ("_parenthetical" if "parenthetical" in t else "")


def build_sections(trees, role_defs, facts, labels):
    """Statement and note sections as presented, with the concepts each one shows. The FO engine needs only each
    section's statement type and line concepts (balance-sheet lines), so values are not attached."""
    sections = []
    for role, roots in trees.items():
        definition = role_defs.get(role, role)
        m = re.match(r"\s*(\d+)\s*-\s*(\w+)\s*-\s*(.*)", definition)
        order, category, title = (m.groups() if m else ("", "Other", definition))
        title = title.strip()
        line_items = []

        def walk(node, depth):
            concept = node["concept"]
            ls = labels.get(concept, {})
            label = (ls.get(node["preferred_label"]) if node["preferred_label"] else None) \
                or ls.get(LABEL_ROLE) or humanize(concept)
            name = concept.split(":")[-1]
            kind = "dimension" if name.endswith(DIMENSION_SUFFIXES) else "heading" if name.endswith("Abstract") else "line"
            line_items.append({"concept": concept, "label": label, "depth": depth, "kind": kind})
            for c in node["children"]:
                walk(c, depth + 1)

        for r in roots:
            walk(r, 0)
        sections.append({
            "order": order,
            "category": category,
            "title": title,
            "statement_type": classify_statement(category, title),
            "role": role,
            "line_items": line_items,
        })
    sections.sort(key=lambda s: s["order"])
    return sections


# =============================================================== 10-K narrative text, split by Item

ITEM_TITLES = {
    "1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments", "1C": "Cybersecurity",
    "2": "Properties", "3": "Legal Proceedings", "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity", "6": "[Reserved]",
    "7": "Management's Discussion and Analysis", "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data", "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures", "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance", "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management",
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services", "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}
ITEM_LINE = re.compile(r"^\s*item\s*(\d{1,2}[a-c]?)\s*[.:\-–—]?\s*(.*)$", re.I)
AUDIT_REPORT_LINE = re.compile(r"^\s*report of independent registered public accounting firm", re.I)


def document_text(doc):
    body = doc.find(".//{http://www.w3.org/1999/xhtml}body")
    return rich_text(body if body is not None else doc)


def split_items(text):
    """Split the 10-K text into Items. Each Item heading appears twice (table of contents and
    body), so for each Item the occurrence with the longest following text is kept."""
    lines = text.split("\n")
    heads = []
    for i, line in enumerate(lines):
        m = ITEM_LINE.match(line.replace("|", " ")) if len(line) < 200 else None
        if m and m.group(1).upper() in ITEM_TITLES:
            heads.append((i, m.group(1).upper(), m.group(2).strip(" |.")))
    if not heads:
        return [{"item": "", "title": "Full document", "text": text}]

    def body_of(k):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        return "\n".join(lines[heads[k][0] + 1:end]).strip()

    # The real Item 1 is the occurrence with the most text; headings before it are the table of contents.
    ones = [k for k, h in enumerate(heads) if h[1] == "1"]
    start = max(ones, key=lambda k: len(body_of(k))) if ones else 0

    best = {}
    for k in range(start, len(heads)):
        i, num, title = heads[k]
        body = body_of(k)
        if num not in best or len(body) >= len(best[num]["text"]):  # ties: the later (body) heading wins
            title = re.sub(r"\s*\|?\s*\d+$", "", title).strip(" |")  # drop table-of-contents page numbers
            best[num] = {"item": num, "title": title or ITEM_TITLES[num], "text": body, "_line": i}
    items = sorted(best.values(), key=lambda s: s["_line"])
    front = "\n".join(lines[:heads[start][0]]).strip()
    if front:
        items.insert(0, {"item": "", "title": "Cover page, contents and forward-looking statements",
                         "text": front, "_line": -1})

    # Audited statements placed as F-pages after Item 15/16 (rather than in Item 8): split them off.
    for host in [s for s in items if s["item"] in ("15", "16")]:
        body_lines = host["text"].split("\n")
        j = next((j for j, line in enumerate(body_lines) if AUDIT_REPORT_LINE.match(line)), None)
        if j is not None:
            host["text"] = "\n".join(body_lines[:j]).strip()
            items.append({"item": "F", "title": "Financial statements and notes (F-pages)",
                          "text": "\n".join(body_lines[j:]).strip(), "_line": host["_line"] + j})
            break
    items.sort(key=lambda s: s["_line"])
    for s in items:
        s.pop("_line", None)
    return items


# =============================================================== periods

def is_annual(key):
    if "--" not in key:
        return False
    start, end = key.split("--")
    try:
        return 350 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 380
    except ValueError:
        return False


def period_sort_key(key):
    start, _, end = key.partition("--")
    return (end or start, start)


def period_label(key):
    if "--" not in key:
        return f"At {key}"
    start, end = key.split("--")
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        months = round(((d1 - d0).days + 1) / 30.44)
        if months >= 1:
            return f"{months}M to {end}"
        return f"{start} to {end}"
    except ValueError:
        return f"{start} to {end}"


def item_ref(item):
    return {"": "Front matter", "F": "F-pages", "Proxy": "Proxy statement"}.get(item, f"Item {item}")


# =============================================================== filing

def read_company_info(folder):
    """{'sic', 'sic_description', 'ticker', 'name', 'cik'} from company.json in the filing folder, else None."""
    try:
        info = json.loads((Path(folder) / COMPANY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and info.get("sic") else None


def load_filing(folder):
    """Everything the FO engine reads from one filing folder."""
    files = locate_files(folder)
    doc = _parse(files["instance"])
    labels = load_labels(files.get("label"))
    role_defs = load_role_definitions(files["schema"])
    trees = load_presentation(files.get("presentation"))

    contexts = parse_contexts(doc)
    units = parse_units(doc)
    facts = parse_facts(doc, contexts, units, labels)
    sections = build_sections(trees, role_defs, facts, labels)

    dei = {f["concept"].split(":")[1]: f["value"] for f in facts
           if f["concept"].startswith("dei:") and not f["dimensions"]}
    # On the cover page the symbol is usually tagged per class of stock (a dimension), so the undimensioned
    # fact is absent; take every TradingSymbol fact, in document order.
    symbols = list(dict.fromkeys(str(f["value"]).strip().upper() for f in facts
                                 if f["concept"] == "dei:TradingSymbol" and str(f["value"]).strip()))
    meta = {
        "folder": Path(folder).name,
        "source": files["instance"].name,
        "entity_name": dei.get("EntityRegistrantName"),
        "cik": dei.get("EntityCentralIndexKey"),
        "ticker": dei.get("TradingSymbol") or (symbols[0] if symbols else None),
        "tickers": symbols,
        "document_type": dei.get("DocumentType"),
        "period_end": dei.get("DocumentPeriodEndDate"),
        "fiscal_year": dei.get("DocumentFiscalYearFocus"),
        "fiscal_period": dei.get("DocumentFiscalPeriodFocus"),
        "files": {k: Path(v).name for k, v in files.items()},
    }
    return {
        "meta": meta,
        "facts": facts,
        "sections": sections,
        "document": split_items(document_text(doc)),
        "labels": {k: dict(v) for k, v in labels.items()},
        "context_count": len(contexts),
        "working_capital": working_capital_lines(load_calculations(files.get("calculation")),
                                                 OPERATING_CASH_FLOW_CONCEPTS),
        "company": read_company_info(folder),
    }
