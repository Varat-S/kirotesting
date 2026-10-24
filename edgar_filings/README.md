# edgar_filings

Input folder for the Financial Overview (FO) section (`/fo`, `python -m app.cli run-fo-case`).

Put one sub-folder per 10-K here, holding the filing's Inline XBRL files as EDGAR serves them:

```
edgar_filings/
  HCA 10-K FY2025/
    hca-20251231.htm        the Inline XBRL 10-K (required)
    hca-20251231.xsd        its schema (required; may embed the linkbases)
    hca-20251231_lab.xml    label linkbase       (optional if embedded in the .xsd)
    hca-20251231_pre.xml    presentation linkbase (balance-sheet lines)
    hca-20251231_cal.xml    calculation linkbase  (working-capital lines for FFO)
    company.json            optional: {"ticker", "name", "sic", ...}
    *.htm                   exhibits are ignored by the FO engine
```

This is the same layout as the "10-K with FO" project's `edgar_filings/`, so its folders can be
copied here unchanged. Folder names may contain spaces. Files over 32 MiB are refused.

The filings are not committed (they are large); see `.gitignore`.
