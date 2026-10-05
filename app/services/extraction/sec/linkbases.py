"""Labels, role definitions and bounded presentation trees; no taxonomy fetches."""

import re
from collections import defaultdict
from .xml import NS, XLINK, safe_xml
from app.services.extraction.base import ParserError

LABEL_ROLE = "http://www.xbrl.org/2003/role/label"


def humanize(concept):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", concept.split(":")[-1])


def concept_from_href(href):
    prefix, _, name = href.rsplit("#", 1)[-1].partition("_")
    return f"{prefix}:{name}"


def load_labels(data):
    labels = defaultdict(dict)
    if not data:
        return {}
    root = safe_xml(data)
    for link in root.findall(".//link:labelLink", NS):
        locs = {
            n.get(XLINK + "label"): concept_from_href(n.get(XLINK + "href", ""))
            for n in link.findall("link:loc", NS)
        }
        resources = defaultdict(list)
        for label in link.findall("link:label", NS):
            resources[label.get(XLINK + "label")].append(label)
        for arc in link.findall("link:labelArc", NS):
            concept = locs.get(arc.get(XLINK + "from"))
            if concept:
                for label in resources[arc.get(XLINK + "to")]:
                    # English labels only; never silently overwrite with another language.
                    if label.get(
                        "{http://www.w3.org/XML/1998/namespace}lang", "en"
                    ).startswith("en"):
                        labels[concept][label.get(XLINK + "role", LABEL_ROLE)] = (
                            " ".join(label.itertext()).strip()
                        )
    return dict(labels)


def load_role_definitions(data):
    if not data:
        return {}
    return {
        n.get("roleURI"): n.findtext("link:definition", namespaces=NS)
        or n.get("roleURI")
        for n in safe_xml(data).findall(".//link:roleType", NS)
    }


def load_presentation(data):
    trees = {}
    if not data:
        return trees
    for link in safe_xml(data).findall(".//link:presentationLink", NS):
        locs = {
            n.get(XLINK + "label"): concept_from_href(n.get(XLINK + "href", ""))
            for n in link.findall("link:loc", NS)
        }
        children, parents = defaultdict(list), set()
        for arc in link.findall("link:presentationArc", NS):
            frm, to = arc.get(XLINK + "from"), arc.get(XLINK + "to")
            if frm in locs and to in locs and arc.get("use") != "prohibited":
                children[frm].append(
                    (float(arc.get("order", "0")), to, arc.get("preferredLabel"))
                )
                parents.add(to)
        budget = [0]

        def build(label, preferred=None, seen=()):
            budget[0] += 1
            if budget[0] > 20000 or len(seen) > 100:
                raise ParserError("Presentation hierarchy exceeds traversal limits.")
            node = {
                "concept": locs[label],
                "preferred_label": preferred,
                "children": [],
            }
            if label in seen:
                return node
            node["children"] = [
                build(to, pref, (*seen, label))
                for _, to, pref in sorted(
                    children.get(label, []), key=lambda a: (a[0], a[1])
                )
            ]
            return node

        trees[link.get(XLINK + "role")] = [
            build(label)
            for label in sorted(locs)
            if label not in parents and label in children
        ]
    return trees


def build_sections(trees, role_defs, facts, labels):
    """Reconstruct presentation rows as diagnostics, preserving every observation."""
    by_concept = defaultdict(list)
    for fact in facts:
        by_concept[fact["concept"]].append(fact)
    sections = []
    for role, roots in sorted(trees.items()):
        rows = []

        def walk(node, depth=0):
            concept = node["concept"]
            rows.append(
                {
                    "concept": concept,
                    "label": labels.get(concept, {}).get(node.get("preferred_label"))
                    or labels.get(concept, {}).get(LABEL_ROLE)
                    or humanize(concept),
                    "depth": depth,
                    "fact_ids": [f["observation_id"] for f in by_concept[concept]],
                }
            )
            for child in node["children"]:
                walk(child, depth + 1)

        for root in roots:
            walk(root)
        sections.append(
            {"role": role, "title": role_defs.get(role, role), "line_items": rows}
        )
    return sections
