import pytest

from app.schemas.enums import FactStatus, ExtractionMethod
from app.services.extraction.base import ParserError
from app.services.extraction.sec.adapter import adapt_facts
from app.services.extraction.sec.inline_xbrl import InlineXbrlParser, parse_number
from app.services.extraction.sec.statements import parse_bundle
from app.services.extraction.sec.xml import safe_xml
from tests.sec_helpers import ACCESSION, bundle, inline_document


def adapt(data):
    parsed = InlineXbrlParser().parse(data)
    return parsed, adapt_facts(
        parsed,
        document_id="DOC",
        entity_id="POC",
        cik="1",
        accession=ACCESSION,
        presentation_roles={"us-gaap:Revenue": ["role:income"]},
    )


@pytest.mark.parametrize(
    "text,attrs,unit,reported,normalized,normalized_unit",
    [
        (
            "215,938",
            'scale="6" format="ixt:num-dot-decimal"',
            "USD",
            215938000000,
            215938,
            "USD_million",
        ),
        ("23", 'scale="3" sign="-"', "USD", -23000, -0.023, "USD_million"),
        ("25", 'scale="-2"', "pure", 0.25, 0.25, "ratio"),
        ("12", 'scale="3"', "shares", 12000, 12000, "shares"),
        ("2.5", "", "per_share", 2.5, 2.5, "USD/shares"),
        (
            "1.234,50",
            'format="ixt:num-comma-decimal"',
            "USD",
            1234.5,
            0.0012345,
            "USD_million",
        ),
    ],
)
def test_scaling_units_and_exact_provenance(
    text, attrs, unit, reported, normalized, normalized_unit
):
    data = inline_document(
        f'<ix:nonFraction id="amount" name="g:Revenue" contextRef="C" unitRef="{unit}" {attrs}>{text}</ix:nonFraction>'
    )
    parsed, facts = adapt(data)
    observation, fact = parsed["facts"][0], facts[0]
    assert observation["value"] == pytest.approx(reported)
    assert fact.normalized_value == pytest.approx(normalized)
    assert fact.normalized_unit == normalized_unit
    assert fact.raw_value == text
    assert fact.taxonomy_concept == "us-gaap:Revenue"  # alternate prefix g
    assert fact.xbrl_context_id == "C" and fact.inline_element_id == "amount"
    assert fact.sec_accession == ACCESSION and fact.entity_id == "POC"
    assert fact.status == FactStatus.UNVERIFIED
    assert fact.extraction_method == ExtractionMethod.INLINE_XBRL
    ref = fact.source_refs[0]
    assert ref.document_id == "DOC" and ref.xbrl_context_id == "C"
    assert ref.inline_element_id == "amount" and ref.presentation_role == "role:income"
    assert ref.sec_accession == ACCESSION


def test_nil_is_missing_and_not_zero():
    _, facts = adapt(
        inline_document(
            '<ix:nonFraction id="nil" name="g:Revenue" contextRef="C" unitRef="USD" xsi:nil="true"/>'
        )
    )
    assert facts[0].status == FactStatus.MISSING
    assert facts[0].normalized_value is None and facts[0].raw_value is None


def test_dimensions_and_typed_domain_preserved():
    parsed, facts = adapt(
        inline_document(
            '<ix:nonFraction id="typed" name="g:Revenue" contextRef="T" unitRef="USD">10</ix:nonFraction>'
        )
    )
    assert parsed["contexts"]["G"]["dimensions"] == {
        "poc:ProductAxis": "poc:GamingMember"
    }
    typed = facts[0].dimensions["poc:CustomAxis"]
    assert typed.startswith("typed:") and "Typed Customer" in typed
    assert "{https://example.com/poc}Domain" in typed and '"code","A"' in typed


def test_continuations_rich_text_exclusions_and_escaped_blocks():
    data = inline_document(
        """<ix:nonNumeric id="block" name="poc:PolicyTextBlock" contextRef="C" continuedAt="more"><p>First <ix:exclude>HIDDEN</ix:exclude> clause.</p></ix:nonNumeric>
    <ix:nonNumeric id="escaped" name="poc:OtherTextBlock" contextRef="C" escape="true">&lt;p&gt;Escaped words&lt;/p&gt;</ix:nonNumeric>
    <ix:nonNumeric id="name" name="poc:Name" contextRef="C" continuedAt="name-part">N</ix:nonNumeric>""",
        '<ix:continuation id="more"><p>Second clause.</p></ix:continuation><ix:continuation id="name-part">VIDIA</ix:continuation>',
    )
    parsed, facts = adapt(data)
    assert "First" in facts[0].raw_value and "Second clause." in facts[0].raw_value
    assert "HIDDEN" not in facts[0].raw_value
    assert facts[1].raw_value == "Escaped words"
    assert (
        facts[2].raw_value == "NVIDIA"
    )  # No synthetic whitespace in plain continuations.
    assert parsed["facts"][0]["type"] == "text_block"


@pytest.mark.parametrize(
    "extra",
    ["", '<ix:continuation id="missing" continuedAt="missing">cycle</ix:continuation>'],
)
def test_invalid_continuation_chain_rejected(extra):
    with pytest.raises(ParserError, match="continuation"):
        InlineXbrlParser().parse(
            inline_document(
                '<ix:nonNumeric name="poc:Text" contextRef="C" continuedAt="missing">start</ix:nonNumeric>',
                extra,
            )
        )


@pytest.mark.parametrize(
    "attrs", ['format="ixt:unsupported"', 'scale="999"', 'sign="+"']
)
def test_unsupported_transforms_preserve_raw_and_do_not_invent_amounts(attrs):
    parsed, facts = adapt(
        inline_document(
            f'<ix:nonFraction name="g:Revenue" contextRef="C" unitRef="USD" {attrs}>100</ix:nonFraction>'
        )
    )
    assert facts[0].raw_value == "100" and facts[0].normalized_value is None
    assert parsed["warnings"]


@pytest.mark.parametrize("text", ["USD 100", "about 100", "", "NaN"])
def test_numeric_parse_rejects_ambiguous_text(text):
    with pytest.raises(ValueError):
        parse_number(text)


def test_numeric_overflow_cannot_become_nonfinite_canonical_amount():
    parsed, facts = adapt(
        inline_document(
            '<ix:nonFraction name="g:Revenue" contextRef="C" unitRef="USD">1e999999</ix:nonFraction>'
        )
    )
    assert facts[0].normalized_value is None and parsed["warnings"]


def test_unknown_unit_namespace_cannot_impersonate_usd():
    data = inline_document(
        '<ix:nonFraction name="g:Revenue" contextRef="C" unitRef="USD">100</ix:nonFraction>'
    ).replace(b"<xbrli:measure>iso:USD", b"<xbrli:measure>poc:USD")
    _, facts = adapt(data)
    assert (
        facts[0].currency is None
        and facts[0].normalized_unit == "{https://example.com/poc}USD"
    )


def test_instant_period_is_preserved():
    data = inline_document(
        '<ix:nonFraction name="g:CashAndCashEquivalentsAtCarryingValue" contextRef="C" unitRef="USD">100</ix:nonFraction>'
    ).replace(
        b"<xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate>",
        b"<xbrli:instant>2024-12-31</xbrli:instant>",
    )
    _, facts = adapt(data)
    assert facts[0].period_type == "instant" and facts[0].period_start is None
    assert facts[0].period_end.isoformat() == "2024-12-31"


def test_entities_and_external_dtd_are_never_expanded():
    with pytest.raises(ParserError, match="entity"):
        safe_xml(
            b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///C:/secret.txt">]><x>&secret;</x>'
        )
    root = safe_xml(b'<!DOCTYPE x SYSTEM "https://example.com/malicious.dtd"><x/>')
    assert root.tag == "x"


def test_unadmitted_bundle_cannot_be_parsed():
    with pytest.raises(ParserError, match="ingestion"):
        parse_bundle(bundle(), entity_id="POC")
