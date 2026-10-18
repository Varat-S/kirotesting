"""Exhibit 21 table extraction; names are evidence, never automatic entity roles."""

import re
from .xml import local, rich_text, safe_xml


def find_exhibit21(file):
    if file.role == "exhibit_21":
        return True
    if file.role != "other_exhibit":
        return False
    heading = rich_text(safe_xml(file.content, html=True))[:1200]
    return bool(
        re.search(r"exhibit\s+21(?:\D|$)", heading, re.I)
        and re.search(r"subsidiar", heading, re.I)
    )


def parse_exhibit21(file):
    root, rows = safe_xml(file.content, html=True), []
    headers = re.compile(
        r"subsidiar(?:y|ies) of (?:the )?registrant|jurisdiction of|state or other|name of (?:the )?subsidiar|^jurisdiction$",
        re.I,
    )
    for index, row in enumerate(root.iter("tr")):
        cells = [
            " ".join(rich_text(c).split())
            for c in row
            if local(c.tag).lower() in {"td", "th"}
        ]
        cells = [c for c in cells if c and not re.fullmatch(r"[\s_-]+", c)]
        if (
            len(cells) < 2
            or any(headers.search(c) for c in cells)
            or all(local(c.tag).lower() == "th" for c in row)
        ):
            continue
        rows.append(
            {
                "name": cells[0],
                "jurisdiction": cells[1],
                "other": cells[2:],
                "document_id": file.document_id,
                "sec_accession": file.accession,
                "location": index,
                "status": "unverified",
            }
        )
    if rows:
        return rows

    # Some issuers publish Exhibit 21 as paragraphs: an uppercase jurisdiction
    # heading followed by one legal name per paragraph, with no HTML table.
    # Preserve the disclosed names as unverified evidence; do not infer roles,
    # ownership percentages, guarantor status, or materiality from the list.
    jurisdiction = None
    legal_suffix = re.compile(
        r"(?:\bInc\.?|\bLLC|\bL\.L\.C\.|\bL\.P\.|\bLtd\.?|\bLimited|"
        r"\bCorp\.?|\bCorporation|\bCompany|\bP\.C\.|\bPLLC)\s*$", re.I)
    ignored = re.compile(r"^Exhibit\s+21(?:\.\d+)?$|^Subsidiaries", re.I)
    for index, node in enumerate(root.iter()):
        if local(node.tag).lower() != "p":
            continue
        value = " ".join(rich_text(node).split())
        if not value or ignored.search(value):
            continue
        if value == value.upper() and len(value) <= 80 and not legal_suffix.search(value):
            jurisdiction = value
            continue
        if legal_suffix.search(value):
            rows.append({
                "name": value,
                "jurisdiction": jurisdiction,
                "other": [],
                "document_id": file.document_id,
                "sec_accession": file.accession,
                "location": index,
                "status": "unverified",
            })
    return rows
