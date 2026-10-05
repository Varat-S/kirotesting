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
    return rows
