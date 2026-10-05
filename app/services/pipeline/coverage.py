"""Measure capture and compare explicit reference values without auto-verifying."""

import re
from collections import Counter


def reference_checks(preview, reference):
    evidence = preview["evidence"]
    documents = {d["document_id"]: d["filename"] for d in evidence["documents"]}
    checks = []
    for expected in reference["facts"]:
        matches = [
            f
            for f in evidence["facts"]
            if f["name"] == expected["name"]
            and f["fiscal_year"] == expected["year"]
            and f["period_type"] == expected["period_type"]
            and f["normalized_unit"] == expected["unit"]
            and any(
                documents.get(r["document_id"]) == expected["file"]
                and r["page"] == expected["page"]
                for r in f["source_refs"]
            )
        ]
        checks.append(
            {
                "expected": expected,
                "passed": bool(matches)
                and all(f["normalized_value"] == expected["value"] for f in matches),
                "fact_ids": [f["fact_id"] for f in matches],
            }
        )
    for name, value in reference["metrics"].items():
        actual = preview["metrics"].get(name, {}).get("result")
        checks.append(
            {
                "metric": name,
                "expected": value,
                "actual": actual,
                "passed": actual is not None and abs(actual - value) < 1e-8,
            }
        )
    return {
        "checks": checks,
        "passed": sum(c["passed"] for c in checks),
        "total": len(checks),
    }


def ocr_reference_checks(capture, reference):
    checks = []
    for page in capture["pages"]:
        expected = Counter(
            float(v) for v in reference["pages"].get(str(page["page"]), [])
        )
        text = re.sub(r"(?<=\d)\s+(?=[,.]\d)|(?<=[,.])\s+(?=\d)", "", page["text"])
        text = re.sub(r"(\d+(?:\.\d+)?)\s*0/0", r"\1%", text)
        observed = Counter()
        for raw in re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", text):
            negative = raw.startswith("(") or raw.startswith("-")
            value = float(raw.strip("()-").replace(",", ""))
            observed[-value if negative else value] += 1
        found = sum((expected & observed).values())
        missing = list((expected - observed).elements())
        total = sum(expected.values())
        checks.append(
            {
                "page": page["page"],
                "recognized_reference_cells": found,
                "reference_cells": total,
                "fraction": found / total if total else None,
                "unrecognized_values": missing,
            }
        )
    recognized = sum(c["recognized_reference_cells"] for c in checks)
    total = sum(c["reference_cells"] for c in checks)
    return {
        "method": reference["method"],
        "pages": checks,
        "recognized_reference_cells": recognized,
        "reference_cells": total,
        "fraction": recognized / total if total else None,
        "limitation": "Page-level numeric occurrence matching is a recognition check, not a check of labels, periods, table layout or every text character.",
    }
