# Real-data readiness

The deterministic runner is tested with labelled synthetic source files and
the supplied real NVIDIA FY2026 Inline-XBRL bundle. NVIDIA testing covers
ingestion, dimensions, provenance, mapping, selected metrics and draft JSON/HTML.
This does not establish accuracy on a real Delta filing or validate credit policy.

The parsers remain PoC implementations. Before using a real case, check:

- SEC Inline XBRL versus plain instance XML and taxonomy extensions;
- duplicate concepts across consolidated/segment contexts;
- quarterly, YTD and annual reporting periods;
- spreadsheet merged headers, units, formula caches and sheet selection;
- GAAP versus adjusted measures and restricted versus unrestricted cash;
- complex PDF tables, footnotes, parentheses and image-only pages;
- airline operating measures and non-GAAP EBITDA definitions.

The SEC adapter recognizes annual durations of 330–400 days, including
52/53-week calendars, and uses DEI fiscal-year focus when present. Annual metrics
use dimensionless consolidated FY and matching instant facts; interim and
dimensional observations remain in the evidence snapshot. The standalone
XBRL path retains its earlier calendar-year handling. New fiscal calendars,
interim metric definitions and issuer extensions still need source-specific
validation. See [SEC_INTEGRATION_REPORT.md](SEC_INTEGRATION_REPORT.md).

XBRL concept mapping and table-label mapping are explicit configuration. Unknown
and ambiguous labels require review. The input package declares entity, scope,
accounting basis and extraction options; those declarations must be checked
against the source documents during real-case preparation. Generic cash is not
automatically treated as unrestricted cash. A long-term debt component is not
automatically treated as total debt.

Original bytes are content addressed and write once. Audit and finalized snapshot
immutability are application/ORM controls in this PoC, rather than hardware/WORM
storage or protection against direct SQL access. CLI reviewer names are local
identities; production authentication/authorization is a separate deployment
concern.

OCR requires Tesseract and the `ocr` extra. Binary PDF output requires a working
native WeasyPrint installation and the `pdf` extra. JSON/HTML generation and the
deterministic test suite require neither optional backend.

SEC discovery/download and local filing-bundle processing are now available.
Each proxy/amendment/companion file independently passes the evidence cutoff;
date-only availability conservatively uses next midnight US Eastern. Calculation
and definition linkbase semantics remain deferred.

Next, select one real Delta filing and related source files, create an explicit
package and contemporaneous ground-truth manifest, and identify demonstrated
parser gaps. A real LLM backend can be added through the existing `LLMBackend`
interface after that preparation; schema validation, model-run logging,
grounding, and the human approval gate remain mandatory.
