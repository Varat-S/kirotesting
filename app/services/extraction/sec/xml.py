"""Bounded XML/HTML parsing without entity expansion or network resolution."""

import re
from lxml import etree
from app.services.extraction.base import ParserError

MAX_FILE_BYTES = 32 * 1024 * 1024
NS = {
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
IX_NAMESPACES = {
    "http://www.xbrl.org/2013/inlineXBRL",
    "http://www.xbrl.org/2008/inlineXBRL",
}
XLINK = "{" + NS["xlink"] + "}"


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def is_inline(node, name):
    return isinstance(node.tag, str) and node.tag in {
        f"{{{ns}}}{name}" for ns in IX_NAMESPACES
    }


def safe_xml(data: bytes, *, html=False):
    if len(data) > MAX_FILE_BYTES:
        raise ParserError("SEC file exceeds the 32 MiB limit.")
    if re.search(rb"<!ENTITY\s", data, re.I):
        raise ParserError("XML entity declarations are not permitted.")
    parser = (
        etree.HTMLParser(no_network=True)
        if html
        else etree.XMLParser(
            resolve_entities=False,
            load_dtd=False,
            no_network=True,
            huge_tree=False,
            recover=False,
        )
    )
    try:
        root = etree.fromstring(data, parser)
        if root is None:
            raise ParserError("Empty SEC document.")
        return root
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ParserError(f"Invalid SEC XML/HTML: {exc}") from exc


def qname(value, node):
    """Canonicalize known taxonomy prefixes; preserve unknown namespace identity."""
    if not value or ":" not in value:
        return value
    prefix, name = value.split(":", 1)
    uri = node.nsmap.get(prefix)
    if not uri:
        raise ParserError(f"Undefined QName prefix {prefix!r}.")
    for canonical, marker in [
        ("us-gaap", "fasb.org/us-gaap/"),
        ("dei", "/dei/"),
        ("srt", "/srt/"),
    ]:
        if marker in uri:
            return f"{canonical}:{name}"
    return f"{prefix}:{name}" if prefix in {"nvda", "poc"} else f"{{{uri}}}{name}"


def raw_text(element):
    parts = []

    def walk(node):
        if is_inline(node, "exclude") or not isinstance(node.tag, str):
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return "".join(parts)


def rich_text(element):
    blocks = {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "li", "br", "section"}
    parts = []

    def walk(node):
        if (
            not isinstance(node.tag, str)
            or is_inline(node, "exclude")
            or is_inline(node, "header")
            or local(node.tag).lower() in {"head", "script", "style"}
        ):
            return
        tag = local(node.tag).lower()
        if tag in blocks:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        parts.append(" | " if tag in {"td", "th"} else "\n" if tag in blocks else "")

    walk(element)
    return "\n".join(
        line
        for raw in "".join(parts).replace("\xa0", " ").splitlines()
        if (line := re.sub(r"[ \t]+", " ", raw).strip(" |"))
    )
