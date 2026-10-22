# Financial Overview reference data

ILLUSTRATIVE - NOT BANK POLICY.

Copied verbatim from the "10-K with FO" project's `Mappings/` folder and read by
`app/services/fo/industry.py`:

* `Healthcare_Industry_Groups.json` - `{TICKER: {company_name, industry_group}}`. Every `*.json`
  file in this folder is read as a mapping file (files that are not in this shape are skipped).
* `Benchmarks.json` - `{"benchmarks": [{"sector": <industry group>, <measure>: value, "p"?: score}]}`
  for `debt_to_ebitdar`, `net_debt_to_ebitdar`, `rcf_to_debt`, `ebitdar_margin`,
  `days_sales_outstanding` (the change in days). Only "Hospitals/Healthcare Facilities" has values;
  other industry groups get "no industry benchmark" for the benchmark-scored measures.

This folder sits outside `config/poc`, so the versioned PoC configuration bootstrap never reads it.
Each FO run records a content hash of these files (`reference_hash`).
