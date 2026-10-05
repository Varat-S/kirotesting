# NVIDIA FY2026 offline SEC fixture

Files copied unchanged from the user's supplied `AI-Credit-Memo-main.zip`,
under `AI Credit Memo Generation/edgar_filings/NVDA 10-K FY2026/`.
`bundle.json` declares roles, SHA-256 hashes and availability for every file;
the parser does not depend on this particular folder structure.

Primary: NVIDIA Corporation, CIK 1045810, accession
`0001045810-26-000021`, 10-K filed 2026-02-25, fiscal report end 2026-01-25.
The [SEC filing index](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/0001045810-26-000021-index.htm)
supports the filing dates/accession. The supplied bytes are the fixture truth;
some supplied files differ in byte length from the live listing, so they are
not represented as a fresh byte-identical SEC download.

The proxy's supplied `filing.json` identifies DEF 14A accession
`0001045810-26-000036`, filed 2026-05-12, with report date 2026-06-24.
Its primary HTML is retained under `proxy/`. The generated bundle retains
the metadata explicitly rather than relying on the original folder/JSON layout.

Availability is **date granularity**, with no guessed acceptance timezone:
primary and companion files are available conservatively at
`2026-02-26T05:00:00Z`; proxy at `2026-05-13T04:00:00Z` (next US Eastern
midnight). Retrieval time describes local receipt, not publication. The
2026-03-01 cutoff admits 13 files and rejects the proxy.

Selected fixture assertions: 281 contexts; over 1,000 observations; explicit
dimensions; labels and presentation; text blocks; FY2026 dimensionless
`us-gaap:Revenues` of USD 215,938 million, versus FY2025 USD 130,497 million;
operating income USD 130,387 million. Revenue is repeated in several inline
elements, retained as separate source observations without auto-verification.
Exhibit 21 contains three subsidiaries after removing header rows.

Calculation and definition XML are preserved but their semantics are deferred.
Unknown issuer concepts require mapping review. The illustrative downstream
configuration includes airline metrics/peers; it is not NVIDIA credit policy.

See [SEC_INTEGRATION_REPORT.md](../../../SEC_INTEGRATION_REPORT.md) for commands,
validation, migration and limits. Git attributes disable line-ending conversion
for this directory so manifest hashes remain valid across operating systems.
