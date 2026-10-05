from app.services.extraction.sec.linkbases import (
    build_sections,
    load_labels,
    load_presentation,
    load_role_definitions,
)
from app.services.extraction.sec.narrative import narrative_evidence, split_items
from app.services.extraction.sec.subsidiaries import find_exhibit21, parse_exhibit21
from tests.sec_helpers import ACCESSION, filing_file


def test_labels_presentation_order_and_roles_are_metadata_only():
    base = '<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">'
    labels = load_labels(
        (
            base
            + """<link:labelLink><link:loc xlink:label="loc" xlink:href="taxonomy.xsd#us-gaap_Revenues"/>
    <link:label xlink:label="name" xml:lang="en-US">Total revenue</link:label><link:label xlink:label="foreign" xml:lang="fr">Revenus</link:label>
    <link:labelArc xlink:from="loc" xlink:to="name"/><link:labelArc xlink:from="loc" xlink:to="foreign"/></link:labelLink></link:linkbase>"""
        ).encode()
    )
    assert list(labels["us-gaap:Revenues"].values()) == ["Total revenue"]
    trees = load_presentation(
        (
            base
            + """<link:presentationLink xlink:role="role:income"><link:loc xlink:label="root" xlink:href="t.xsd#poc_IncomeStatement"/><link:loc xlink:label="revenue" xlink:href="t.xsd#us-gaap_Revenues"/><link:loc xlink:label="operating" xlink:href="t.xsd#us-gaap_OperatingIncomeLoss"/>
    <link:presentationArc xlink:from="root" xlink:to="operating" order="2"/><link:presentationArc xlink:from="root" xlink:to="revenue" order="1"/></link:presentationLink></link:linkbase>"""
        ).encode()
    )
    roles = load_role_definitions(
        b'<schema xmlns="http://www.w3.org/2001/XMLSchema" xmlns:link="http://www.xbrl.org/2003/linkbase"><annotation><appinfo><link:roleType roleURI="role:income"><link:definition>Income statement</link:definition></link:roleType></appinfo></annotation></schema>'
    )
    observations = [
        {"concept": "us-gaap:Revenues", "observation_id": "1"},
        {"concept": "us-gaap:Revenues", "observation_id": "2"},
    ]
    sections = build_sections(trees, roles, observations, labels)
    assert sections[0]["title"] == "Income statement"
    assert sections[0]["line_items"][1]["concept"] == "us-gaap:Revenues"
    assert sections[0]["line_items"][1]["fact_ids"] == ["1", "2"]
    assert sections[0]["line_items"][1]["label"] == "Total revenue"


def test_item_table_of_contents_does_not_replace_real_body():
    text = (
        "Item 1. Business\nPage 1\nItem 1A. Risk Factors\nPage 2\nItem 1. Business\nWe depend on third-party suppliers. "
        + "long body " * 30
        + "\nItem 1A. Risk Factors\nExport regulations and customer concentration."
    )
    assert "third-party suppliers" in next(
        p["text"] for p in split_items(text) if p["section"] == "Item 1"
    )
    evidence = narrative_evidence(text, document_id="PRIMARY", accession=ACCESSION)
    assert any("supplier_concentration" in p["candidate_topics"] for p in evidence)
    assert all(
        p["status"] == "unverified"
        and p["document_id"] == "PRIMARY"
        and p["section"].startswith("Item")
        for p in evidence
    )


def test_exhibit_heading_fallback_and_header_rows_filtered():
    file = filing_file(
        "unusual.htm",
        "other_exhibit",
        b"<html><h1>Exhibit 21.1 Subsidiaries</h1><table><tr><td>Subsidiaries of Registrant (All 100% owned)</td><td>State or Other Jurisdiction of Incorporation</td></tr><tr><td>Example International Ltd</td><td>Singapore</td></tr></table></html>",
    )
    assert find_exhibit21(file)
    rows = parse_exhibit21(
        file.model_copy(update={"admitted": True, "document_id": "EX21"})
    )
    assert len(rows) == 1 and rows[0]["name"] == "Example International Ltd"
    assert rows[0]["jurisdiction"] == "Singapore" and rows[0]["document_id"] == "EX21"
    assert rows[0]["status"] == "unverified"
