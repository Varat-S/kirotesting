"""Proxy passages retain the proxy's own document identity and cutoff admission."""

from .narrative import narrative_evidence
from .xml import rich_text, safe_xml


def parse_proxy(file):
    return narrative_evidence(
        rich_text(safe_xml(file.content, html=True)),
        document_id=file.document_id,
        accession=file.accession,
        kind="DEF 14A",
    )
