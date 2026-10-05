"""Small offline Inline-XBRL fixtures; no network or dashboard imports."""

from datetime import date, datetime, timezone
from app.services.extraction.sec.bundle import SecFilingBundle, SecFilingFile

ACCESSION = "0000000001-25-000001"


def inline_document(facts=None, extra=""):
    facts = (
        facts
        or """
    <ix:nonFraction id="r" name="g:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="C" unitRef="USD" scale="6">100</ix:nonFraction>
    <ix:nonFraction id="r2" name="g:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="C" unitRef="USD" scale="6">100</ix:nonFraction>
    <ix:nonFraction id="gaming" name="g:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="G" unitRef="USD" scale="6">30</ix:nonFraction>
    <ix:nonFraction id="dc" name="g:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="D" unitRef="USD" scale="6">70</ix:nonFraction>
    <ix:nonFraction id="o" name="g:OperatingIncomeLoss" contextRef="C" unitRef="USD" scale="6">20</ix:nonFraction>"""
    )

    def context(cid, dims=""):
        return f'''<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>{dims}</xbrli:entity><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>'''

    contexts = (
        context("C")
        + context(
            "G",
            '<xbrli:segment><xbrldi:explicitMember dimension="poc:ProductAxis">poc:GamingMember</xbrldi:explicitMember></xbrli:segment>',
        )
        + context(
            "D",
            '<xbrli:segment><xbrldi:explicitMember dimension="poc:ProductAxis">poc:DataCenterMember</xbrldi:explicitMember></xbrli:segment>',
        )
        + context(
            "T",
            '<xbrli:segment><xbrldi:typedMember dimension="poc:CustomAxis"><poc:Domain code="A">Typed Customer</poc:Domain></xbrldi:typedMember></xbrli:segment>',
        )
    )
    return f"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:g="http://fasb.org/us-gaap/2024" xmlns:poc="https://example.com/poc" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:iso="http://www.xbrl.org/2003/iso4217" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2022-02-16"><body><ix:header><ix:resources>{contexts}
    <xbrli:unit id="USD"><xbrli:measure>iso:USD</xbrli:measure></xbrli:unit>
    <xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>
    <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
    <xbrli:unit id="per_share"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
    </ix:resources></ix:header><h2>Item 1. Business</h2><p>We depend on a limited number of third-party manufacturers and customers.</p>{facts}{extra}</body></html>""".encode()


def filing_file(
    filename="primary.htm",
    role="primary_inline_xbrl",
    content=None,
    filing_date=date(2025, 1, 10),
    accession=ACCESSION,
    form="10-K",
    accepted_at=None,
):
    from app.services.acquisition.models import SecFiling

    metadata = SecFiling(
        "1", form, accession, filing_date, date(2024, 12, 31), filename, accepted_at
    )
    return SecFilingFile(
        filename=filename,
        role=role,
        content=inline_document() if content is None else content,
        accession=accession,
        form=form,
        filing_date=filing_date,
        report_date=date(2024, 12, 31),
        accepted_at=accepted_at,
        available_at=metadata.available_at,
        availability_granularity=metadata.availability_granularity,
        retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )


def bundle(files=None):
    return SecFilingBundle(
        cik="1",
        ticker="POC",
        accession=ACCESSION,
        form="10-K",
        report_date=date(2024, 12, 31),
        filing_date=date(2025, 1, 10),
        primary_document="primary.htm",
        files=files or [filing_file()],
    )
