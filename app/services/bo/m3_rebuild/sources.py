"""Selected-filing-only source discovery, with resolvable physical PDF pages.

A Form 10-K can also arrive as an SEC Inline XBRL (HTML) filing. Its admitted
passages have no pages or cover, so company and period come from the filing's
admitted metadata and each passage is one discovery unit (cited as page 1).

When a 10-K incorporates its executive officers by reference, the runner may
route the linked DEF 14A instead. That proxy is then the only source: its
"Executive Officers" table and ``Name—Title`` biographies are discovered here,
and a CEO who is also a director is reconciled to the director-nominee biography.
"""
from dataclasses import dataclass, asdict, field
from hashlib import sha256
from pathlib import Path
import re


def normalized(text):
    return " ".join(text.split())


@dataclass
class FilingSources:
    company: str
    filing_period: str
    source_path: str
    sha256: str
    pages: dict[str, str]
    section_ids: list[str]
    expected_roster: list[dict]
    coverage_verifiable: bool
    limitations: list[str]
    physical_pages: dict[str, int] = field(default_factory=dict)
    document_id: str | None = None
    evidence_packet_hash: str | None = None
    canonical_snapshot_version: int | None = None
    passage_offsets: dict[str, int] = field(default_factory=dict)
    document_kind: str = "10-K"

    def to_dict(self):
        return asdict(self)

    def check_quote(self, source_id, quote):
        if source_id not in self.pages or not quote.strip():
            return False
        return normalized(quote) in normalized(self.pages[source_id])


def locate_sources(path, company, filing_period):
    import pdfplumber
    path = Path(path).resolve()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", filing_period):
        raise ValueError("filing_period must be an ISO fiscal year-end date")
    with pdfplumber.open(path) as pdf:
        texts = [p.extract_text(x_tolerance=1) or "" for p in pdf.pages]
    sources = sources_from_pages(texts, company, filing_period, str(path), sha256(path.read_bytes()).hexdigest())
    sources.limitations.append("PDF extraction may split words; quotations preserve extracted text. Resolve physical page IDs against the PDF for typography.")
    return sources


def sources_from_evidence_packet(packet):
    """Discover the executive section only inside a routed canonical packet."""
    data = packet.model_dump(mode="json") if hasattr(packet, "model_dump") else dict(packet)
    context = data.get("context") or {}
    company = context.get("company")
    filing_period = context.get("filing_period")
    document_id = context.get("management_document_id")
    document_sha256 = context.get("management_document_sha256")
    if not company or not filing_period or not document_id or not document_sha256:
        raise ValueError("M3 packet lacks admitted management document context")
    proxy = context.get("management_document_format") == "sec_proxy_html"
    sec_html = proxy or context.get("management_document_format") == "sec_inline_xbrl"
    if sec_html:
        _check_sec_identity(context, filing_period)
    permitted = set(data.get("evidence_ids") or [])
    passages = [
        p for p in data.get("narrative_evidence") or []
        if p.get("document_id") == document_id and p.get("evidence_id") in permitted
    ]
    passages.sort(key=lambda p: (p.get("page") or 0, p.get("location") or 0,
                                p["evidence_id"]))
    if not passages:
        return FilingSources(
            company=company, filing_period=filing_period,
            source_path=f"canonical-document:{document_id}", sha256=document_sha256,
            pages={}, section_ids=[], expected_roster=[], coverage_verifiable=False,
            limitations=[
                "The admitted management document produced no routed readable passages."
            ], document_id=document_id,
            evidence_packet_hash=data.get("packet_hash"),
            canonical_snapshot_version=data.get("snapshot_version"),
        )
    # Discover on complete physical pages so a heading/date/biography split at
    # the 2,000-character capture boundary is not treated as a new PDF page.
    # An HTML filing has no pages: each passage is its own unit.
    groups = {}
    for index, passage in enumerate(passages, 1):
        groups.setdefault(index if sec_html else passage.get("page") or 1, []).append(passage)
    unit_of = {p["evidence_id"]: unit for unit, members in groups.items() for p in members}
    physical = sorted(groups)
    texts = ["".join(p.get("text") or "" for p in groups[page]) for page in physical]
    if proxy:
        discovered = sources_from_proxy(
            texts, [int(groups[u][0].get("location") or 0) for u in physical],
            company, filing_period, f"sec-proxy:{document_id}", document_sha256,
        )
        discovered.limitations.append(
            f"Executive officers taken from the DEF 14A proxy statement (accession "
            f"{context.get('management_document_accession')}, filed "
            f"{context.get('management_document_filing_date')}) that the selected 10-K "
            "incorporates by reference. Fiscal period confirmed from the 10-K's SEC metadata. "
            "HTML passages have no pages; citations give page 1 and the passage offset."
        )
    elif sec_html:
        discovered = sources_from_pages(
            [fold_html_table(t) for t in texts], company, filing_period,
            f"sec-inline-xbrl:{document_id}", document_sha256,
            cover_verified=True, max_units=30,
        )
        discovered.limitations.append(
            "Company and fiscal period were confirmed from the admitted SEC filing metadata "
            f"(form {context.get('management_document_form')}, CIK "
            f"{context.get('management_document_cik')}), not from cover-page text. "
            "HTML passages have no pages; citations give page 1 and the passage offset."
        )
    else:
        discovered = sources_from_pages(
            texts, company, filing_period, f"canonical-document:{document_id}",
            document_sha256,
        )
    old_pages = dict(discovered.pages)
    old_sections = list(discovered.section_ids)
    selected_pages = {physical[int(s.split('p')[-1]) - 1] for s in old_pages}
    section_pages = {physical[int(s.split('p')[-1]) - 1] for s in old_sections}
    # Supply every chunk on each discovered section page, preserving exact IDs
    # and text; adjacent chunks are ordered and never merged into fake evidence.
    discovered.pages = {p["evidence_id"]: p.get("text") or "" for p in passages
                        if unit_of[p["evidence_id"]] in selected_pages}
    discovered.section_ids = [p["evidence_id"] for p in passages
                              if unit_of[p["evidence_id"]] in section_pages]
    for row in discovered.expected_roster:
        page = physical[int(row["source_id"].split('p')[-1]) - 1]
        row["source_id"] = next(
            (p["evidence_id"] for p in groups[page] if row["name"] in p["text"]),
            groups[page][0]["evidence_id"],
        )
    discovered.physical_pages = {
        passage["evidence_id"]: 1 if sec_html else int(passage.get("page") or 1)
        for passage in passages
    }
    discovered.document_id = document_id
    discovered.passage_offsets = {p['evidence_id']:int(p.get('location') or 0) for p in passages}
    discovered.evidence_packet_hash = data.get("packet_hash")
    discovered.canonical_snapshot_version = data.get("snapshot_version")
    return discovered


def sources_from_pages(texts, company, filing_period, source_path="synthetic", digest="synthetic",
                       *, cover_verified=False, max_units=12):
    """Pure discovery function; synthetic inputs are explicitly labelled in tests.

    ``cover_verified`` means the caller already confirmed company and period from
    admitted filing metadata (an SEC HTML 10-K has no cover passage).
    """
    pages = {}
    if not cover_verified:
        from datetime import date
        period = date.fromisoformat(filing_period)
        period_pattern = rf"fiscal\s+year\s+ended\s+{period.strftime('%B')}\s+{period.day},?\s+{period.year}"
        covers = [i for i, t in enumerate(texts) if re.search(period_pattern, t, re.I)
                  and re.search(r"Form\s+10-K", t, re.I)]
        if len(covers) != 1:
            raise ValueError("Selected filing period cannot be uniquely established from Form 10-K cover")
        cover = texts[covers[0]]
        company_words = re.findall(r"\w+", company.casefold())
        if not company_words or not all(w in cover.casefold() for w in company_words):
            raise ValueError("Selected filing cover does not support requested company")
        pages = {f"pdf:p{covers[0]+1}": cover}
    heading = re.compile(r"^(?:Information\s+(?:about|on)\s+(?:our\s+)?)?(?:Our\s+)?(?:Directors\s+and\s+)?Executive\s+Officers\s*$", re.I | re.M)
    candidates = []
    for i, text in enumerate(texts):
        match = heading.search(text)
        if match:
            fragment = text[match.start():] + "\n" + "\n".join(texts[i+1:i+3])
            fragment = re.split(r"^Item\s+\d+[A-Z]?\.", fragment, maxsplit=1, flags=re.M | re.I)[0]
            # Prefer actual dated roster/biographies over TOC and back-cover lists.
            quality = int(bool(re.search(r"As\s+of\s+\w+\s+\d{1,2},?\s+\d{4}", fragment, re.I))) * 2
            quality += int(bool(re.search(r"(?:appointed|has served|previously served)", fragment, re.I)))
            candidates.append((quality, i, match.start()))
    section_ids, limitations, roster, roster_issues = [], [], [], []
    if candidates:
        quality, start, offset = max(candidates)
        if quality == 3:
            for i in range(start, len(texts)):
                text = texts[i][offset:] if i == start else texts[i]
                boundary = re.search(r"^Item\s+\d+[A-Z]?\.", text, re.M | re.I)
                if boundary:
                    text = text[:boundary.start()]
                if text.strip():
                    sid = f"pdf:p{i+1}"
                    pages[sid] = text
                    section_ids.append(sid)
                if boundary:
                    break
                if i - start >= max_units:
                    raise ValueError("Executive section exceeds discovery limit; select/verify source section explicitly")
            roster, roster_issues = parse_roster([(sid, pages[sid]) for sid in section_ids])
            limitations.extend(roster_issues)
    if not section_ids:
        limitations.append("No usable executive biographies located; information may be incorporated by reference. No outside sources were used.")
    if not roster and section_ids:
        limitations.append("Roster table format is not deterministically countable; completeness requires review and cannot be marked complete.")
    return FilingSources(company, filing_period, source_path, digest, pages, section_ids, roster,
                         bool(roster) and not roster_issues, limitations)


def _check_sec_identity(context, filing_period):
    """An SEC 10-K's admitted metadata stands in for the cover page."""
    form = str(context.get("management_document_form") or "")
    if context.get("management_document_format") == "sec_proxy_html":
        # A proxy has no fiscal period; the selected 10-K it is linked to does.
        if form != "DEF 14A":
            raise ValueError(f"Selected proxy source is a {form or 'document of unknown form'}, not a DEF 14A")
        if str(context.get("management_primary_report_date") or "") != str(filing_period):
            raise ValueError("Proxy statement is not linked to a Form 10-K for the selected filing period")
        return
    if not form.startswith("10-K"):
        raise ValueError(f"Selected SEC filing is a {form or 'document of unknown form'}, not a Form 10-K")
    if str(context.get("management_document_report_date") or "") != str(filing_period):
        raise ValueError("Selected filing period cannot be uniquely established from Form 10-K metadata")


def fold_html_table(text):
    """Rejoin an HTML table's one-cell-per-line rows (name / age / position) into one
    line each, for roster discovery only; quotations always use the original text."""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        cells = [line.strip() for line in lines[i:i + 3]]
        if len(cells) == 3 and all(cells):
            header = (re.fullmatch(r"Name", cells[0], re.I) and re.fullmatch(r"Age", cells[1], re.I)
                      and re.match(r"Position", cells[2], re.I))
            row = (re.fullmatch(r"\d{2,3}", cells[1]) and not cells[0][0].isdigit()
                   and re.search(r"[A-Za-z]", cells[2]))
            if header or row:
                out.append(" ".join(cells))
                i += 3
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def parse_roster(pages):
    """Conservative bounded table parser; non-dotted age rows are recognized too.

    After the roster introduction, consume rows/continuations until a biography.
    Unfamiliar content, duplicate rows or names without matching biographies make
    coverage uncertain. A nonempty subset never proves completeness.
    """
    roster, issues = [], []
    active = False
    bio_lines = []
    all_text = "\n".join(text for _, text in pages)
    for sid, text in pages:
        for raw in text.splitlines():
            line = raw.strip()
            if re.search(r"as of.*(?:executive|officer)", line, re.I):
                active = True
                continue
            if not active or not line or re.fullmatch(r"\d+", line):
                continue
            if re.match(r"(?:Name\s+Age|Position\(s\)|Name$)", line, re.I):
                continue
            if re.search(r"\b(?:was appointed|has served|became|has been)\b", line):
                active = False
                bio_lines.append(line)
                continue
            row = re.match(r"^(.+?)(?:\.{2,}\s*|\s+)(\d{2,3})\s+(.+)$", line)
            if row:
                name = row[1].strip().rstrip('.')
                if not re.fullmatch(r"[A-Za-zÀ-ž .,'’()-]+", name) or len(name.split()) < 2:
                    issues.append("Unresolved roster row: " + line)
                    continue
                roster.append({"name": name, "position": row[3].strip(), "source_id": sid})
            elif roster and re.match(r"^(?:and |Chief |Officer|Vice |President|Administrative |Resources |Executive |Legal |Operating |Financial )", line, re.I):
                roster[-1]["position"] += " " + line
            else:
                issues.append("Potentially unparsed roster content: " + line)
    names = [normalized(x["name"]).casefold().rstrip('.') for x in roster]
    if len(names) != len(set(names)):
        issues.append("Duplicate names in the disclosed roster; coverage requires review.")
    for row in roster:
        pattern = re.escape(row["name"].rstrip('.')) + r"\.?\s+(?:was appointed|has served|became|has been)"
        if not re.search(pattern, normalized(all_text), re.I):
            issues.append("No reconciled biography for roster member: " + row["name"])
    # A biography not represented by the parsed rows signals a missing executive.
    for match in re.finditer(r"(?:^|\n)([A-Z][A-Za-z .,'’()-]+?)\s+(?:was appointed|has served|became|has been)\b", all_text):
        bio_name = match[1].strip().rstrip('.')
        if not any(bio_name.casefold() == n for n in names):
            issues.append("Biography not reconciled to roster: " + bio_name)
    if active:
        issues.append("Executive table boundary was not established by a biography.")
    if not roster:
        issues.append("No confidently parsed executive roster rows.")
    return roster, list(dict.fromkeys(issues))


HONORIFIC = r"(?:Mr|Ms|Mrs|Dr)\."
PROXY_SECTION = re.compile(r"^Executive\s+Officers\s*$", re.I | re.M)
# An all-capitals line ("GENERAL INFORMATION") opens the next proxy section.
PROXY_BOUNDARY = re.compile(r"^[A-Z][A-Z0-9 &,'’/-]{3,}$", re.M)
_BIO_HEADING = re.compile(
    r"^([A-Z][A-Za-z.'’ -]{1,60}(?:,\s*[A-Z][A-Za-z.]{1,10})?)(?:\s*[—–][^\n]*)?\n"
    + HONORIFIC + r"\s+([A-Z][\w'’-]+)", re.M)


def name_key(value):
    return " ".join(re.findall(r"[a-z0-9]+", normalized(value).casefold()))


def surname(name):
    """Last name of a roster entry, without a trailing credential (", D.O.", ", Jr.")."""
    words = name.split(",")[0].split()
    return words[-1].rstrip(".") if words else ""


def proxy_bio_starts(text):
    """(offset, name) of each ``Name[—Title]`` heading followed by ``Mr./Ms./Dr. Surname``.

    Requiring the honorific line to repeat the heading's surname keeps table rows,
    committee lists and prose from being read as biography headings.
    """
    return [(m.start(), m[1].strip()) for m in _BIO_HEADING.finditer(text)
            if surname(m[1]).casefold() == m[2].casefold()]


def proxy_boundaries(text):
    """Offsets where a proxy biography ends: any person's heading or a section heading."""
    return sorted({p for p, _ in proxy_bio_starts(text)}
                  | {m.start() for m in PROXY_BOUNDARY.finditer(text)})


def sources_from_proxy(texts, locations, company, filing_period, source_path="synthetic",
                       digest="synthetic", *, max_units=30):
    """Discover a DEF 14A's executive officers; ``texts`` are its routed passages in order.

    The section runs from an "Executive Officers" heading over contiguous passages to
    the next all-capitals heading. A rostered officer whose biography is not in that
    section (typically a CEO who is also a director) is reconciled to a unique
    ``Name`` / ``Mr. Surname`` biography elsewhere in the proxy, which is added as its
    own passages. Nothing is joined across non-contiguous passages.
    """
    def contiguous(i):
        return i + 1 < len(texts) and locations[i] + len(texts[i]) == locations[i + 1]

    candidates = []
    for i, text in enumerate(texts):
        for match in PROXY_SECTION.finditer(text):
            units, section, j = [], "", i
            while True:
                units.append((j, len(section)))
                section += texts[j][match.start():] if j == i else texts[j]
                boundary = PROXY_BOUNDARY.search(section, section.find("\n") + 1)
                if boundary or not contiguous(j):
                    break
                if len(units) > max_units:
                    raise ValueError("Executive section exceeds discovery limit; select/verify source section explicitly")
                j += 1
            if boundary:
                section = section[:boundary.start()]
                units = [(u, o) for u, o in units if o < len(section)]
            quality = 2 * bool(re.search(r"^Name\b[^\n]*\bAge\b", section, re.M | re.I))
            quality += bool(proxy_bio_starts(section))
            candidates.append((quality, i, units, section))
    pages, section_ids, roster, limitations = {}, [], [], []
    best = max(candidates, key=lambda c: (c[0], -c[1]), default=None)
    if best and best[0] == 3:
        _, _, units, section = best
        issues = []
        for row in parse_proxy_roster(section, issues):
            offset = row.pop("offset")
            unit = max(u for u, o in units if o <= offset)
            roster.append({**row, "source_id": f"pdf:p{unit + 1}"})
        selected = {u for u, _ in units}
        in_section = {name_key(n) for _, n in proxy_bio_starts(section)}
        roster_keys = {name_key(r["name"]) for r in roster}
        for name in sorted(in_section - roster_keys):
            issues.append("Biography not reconciled to roster: " + name)
        for row in roster:
            if name_key(row["name"]) in in_section:
                continue
            found = set()
            for u in range(len(texts)):
                if u in selected:
                    continue
                text = texts[u] + (texts[u + 1] if contiguous(u) else "")
                found |= {u for p, n in proxy_bio_starts(text)
                          if p < len(texts[u]) and name_key(n) == name_key(row["name"])}
            if len(found) != 1:
                issues.append("No reconciled biography for roster member: " + row["name"])
                continue
            u = found.pop()
            start = next(p for p, n in proxy_bio_starts(texts[u] + (texts[u + 1] if contiguous(u) else ""))
                         if name_key(n) == name_key(row["name"]))
            run, text = [u], texts[u]
            while not [b for b in proxy_boundaries(text) if b > start] and contiguous(run[-1]) and len(run) < 4:
                run.append(run[-1] + 1)
                text += texts[run[-1]]
            selected |= set(run)
        section_ids = [f"pdf:p{u + 1}" for u in sorted(selected)]
        pages = {sid: texts[int(sid[5:]) - 1] for sid in section_ids}
        names = [name_key(r["name"]) for r in roster]
        if len(names) != len(set(names)):
            issues.append("Duplicate names in the disclosed roster; coverage requires review.")
        if not roster:
            issues.append("No confidently parsed executive roster rows.")
        limitations.extend(dict.fromkeys(issues))
    if not section_ids:
        limitations.append("No executive officers section with a roster and biographies located in the proxy statement.")
    return FilingSources(company, filing_period, source_path, digest, pages, section_ids, roster,
                         bool(roster) and not limitations, limitations, document_kind="DEF 14A")


def parse_proxy_roster(section, issues):
    """Rows of a proxy's ``Name | Age | Position | Since`` table, with the name's offset.

    Cells are split on table pipes and line breaks, so a row wrapped over lines still
    parses; any cell not consumed as name, age, position or "since" date is reported.
    """
    header = re.search(r"^Name\b[^\n]*\bAge\b[^\n]*$", section, re.M | re.I)
    if not header:
        return []
    body = section[header.end():]
    stops = [m.start() for m in re.finditer(r"^[^|\n]{100,}$", body, re.M)]
    stops += [p for p, _ in proxy_bio_starts(body)]
    body = body[:min(stops, default=len(body))]
    cells = [(header.end() + m.start() + len(m[0]) - len(m[0].lstrip()), m[0].strip())
             for m in re.finditer(r"[^|\n]+", body) if m[0].strip()]
    rows, used = [], set()
    for k, (_, cell) in enumerate(cells):
        if not (re.fullmatch(r"\d{2,3}", cell) and 0 < k < len(cells) - 1):
            continue
        (offset, name), (_, position) = cells[k - 1], cells[k + 1]
        used |= {k - 1, k, k + 1}
        if k + 2 < len(cells) and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", cells[k + 2][1]):
            used.add(k + 2)
        if not re.fullmatch(r"[A-Za-zÀ-ž .,'’()-]+", name) or len(name.split()) < 2:
            issues.append("Unresolved roster row: " + name)
            continue
        rows.append({"name": name.rstrip("."), "position": position, "offset": offset})
    issues.extend("Potentially unparsed roster content: " + cell
                  for k, (_, cell) in enumerate(cells) if k not in used)
    return rows
