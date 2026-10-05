"""Context, dimension and unit parsing adapted from the groupmate parser."""

import json
from datetime import date
from app.services.extraction.base import ParserError
from .xml import NS, qname


def typed_value(element):
    # Expanded element names and attributes retain the typed domain, not just text.
    return [
        element.tag,
        sorted(element.attrib.items()),
        element.text or "",
        [
            [typed_value(child), child.tail or ""]
            for child in element
            if isinstance(child.tag, str)
        ],
    ]


def parse_contexts(root):
    contexts = {}
    for node in root.iter(f"{{{NS['xbrli']}}}context"):
        identifier = node.find(".//xbrli:identifier", NS)
        period = node.find("xbrli:period", NS)
        if identifier is None or period is None or not node.get("id"):
            raise ParserError("Context lacks entity, period or ID.")
        if not (identifier.text or "").strip():
            raise ParserError("Context entity identifier is empty.")
        instant = period.findtext("xbrli:instant", namespaces=NS)
        start = period.findtext("xbrli:startDate", namespaces=NS)
        end = period.findtext("xbrli:endDate", namespaces=NS)
        try:
            date.fromisoformat(instant or end)
            if not instant:
                if date.fromisoformat(start) > date.fromisoformat(end):
                    raise ValueError("Reversed context duration")
        except (ValueError, TypeError) as exc:
            raise ParserError("Invalid context period.") from exc
        dimensions = {}
        for member in node.iter(
            f"{{{NS['xbrldi']}}}explicitMember", f"{{{NS['xbrldi']}}}typedMember"
        ):
            axis = qname(member.get("dimension"), member)
            if not axis or axis in dimensions:
                raise ParserError("Missing/duplicate dimension axis.")
            if member.tag.endswith("explicitMember"):
                value = qname((member.text or "").strip(), member)
                if not value:
                    raise ParserError("Explicit dimension member is empty.")
            else:
                value = "typed:" + json.dumps(
                    [typed_value(c) for c in member],
                    sort_keys=True,
                    separators=(",", ":"),
                )
            dimensions[axis] = value
        context = {
            "entity": (identifier.text or "").strip(),
            "entity_scheme": identifier.get("scheme"),
            "period": {"type": "instant", "date": instant}
            if instant
            else {"type": "duration", "start": start, "end": end},
            "dimensions": dimensions,
        }
        if node.get("id") in contexts and contexts[node.get("id")] != context:
            raise ParserError("Conflicting duplicate context ID.")
        contexts[node.get("id")] = context
    return contexts


def parse_units(root):
    units = {}
    for node in root.iter(f"{{{NS['xbrli']}}}unit"):

        def measures(path):
            values = []
            for measure in node.findall(path, NS):
                text = (measure.text or "").strip()
                prefix, _, name = text.partition(":")
                uri = measure.nsmap.get(prefix)
                if not name or not uri:
                    raise ParserError(
                        "Unit measure requires a namespace-qualified name."
                    )
                if uri == "http://www.xbrl.org/2003/iso4217" or (
                    uri == NS["xbrli"] and name in {"pure", "shares"}
                ):
                    values.append(name)
                else:
                    values.append(f"{{{uri}}}{name}")
            return "*".join(sorted(values))

        num = measures("xbrli:divide/xbrli:unitNumerator/xbrli:measure")
        den = measures("xbrli:divide/xbrli:unitDenominator/xbrli:measure")
        value = f"{num}/{den}" if num and den else measures("xbrli:measure")
        if not node.get("id") or not value:
            raise ParserError("Unit lacks ID or measure.")
        if node.get("id") in units and units[node.get("id")] != value:
            raise ParserError("Conflicting duplicate unit ID.")
        units[node.get("id")] = value
    return units
