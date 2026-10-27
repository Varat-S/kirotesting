"""Bounded extraction of an explicitly dated, line-delimited executive roster.

No biographies, appointments or missing officers are invented. Unsupported
formats remain coverage-unavailable; a roster is not a complete change log.
"""
import calendar
import re
from datetime import date


def person_key(name):
    name = re.sub(r"\bM\.?D\.?\b", "", name, flags=re.I)
    return " ".join(re.findall(r"[a-z]+", name.casefold()))


def disclosed_rosters(texts):
    found = {}
    pattern = (r"As of (?P<month>[A-Za-z]+) (?P<day>\d{1,2}),? (?P<year>\d{4}),? "
               r"our executive officers were as follows:")
    for eid, text in texts.items():
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        try:
            month = next(i for i, m in enumerate(calendar.month_name) if m.casefold() == match["month"].casefold())
            as_of = date(int(match["year"]), month, int(match["day"])).isoformat()
        except (StopIteration, ValueError):
            continue
        # Restrict to this table; biographies lack the name/age/position row form.
        tail = text[match.end():]
        rows = []
        for row in re.finditer(r"(?:^|\n)([^\n]+)\n(\d{1,3})\n([^\n]+)", tail):
            name, role = row[1].strip(), row[3].strip()
            if not re.search(r"\b(?:officer|president|director|executive)\b", role, re.I):
                continue
            rows.append({"name": name, "role": role, "quote": row[0].strip(), "evidence_id": eid})
        if rows:
            found[eid] = {"as_of_date": as_of, "as_of_quote": match[0], "entries": rows}
    return found


def roster_coverage(rosters, profiles):
    expected = {person_key(e["name"]): e for roster in rosters.values() for e in roster["entries"]}
    supplied = {person_key(p["name"]) for p in profiles}
    def role_signature(value):
        value = " ".join(re.findall(r"[a-z]+", value.casefold()))
        chief = re.search(r"chief .+?(?:officer|executive)\b", value)
        return chief[0] if chief else value
    verified = {person_key(p["name"]) for p in profiles if not p.get("validation_reasons")
                and (person_key(p["name"]) not in expected or
                     role_signature(p["current_role"]) == role_signature(expected[person_key(p["name"])]["role"]))}
    return {"status": "complete_for_disclosed_roster" if expected and set(expected) <= verified else
            "partial" if expected else "unavailable",
            "scope": "Disclosed dated roster only, not a complete turnover or historical-change review.",
            "expected_count": len(expected), "proposed_count": len(supplied & set(expected)),
            "verified_count": len(verified & set(expected)),
            "omitted_names": [expected[k]["name"] for k in sorted(set(expected) - supplied)],
            "unverified_names": [expected[k]["name"] for k in sorted((supplied & set(expected)) - verified)],
            "roster_dates": sorted({r["as_of_date"] for r in rosters.values()}),
            "source_evidence_ids": sorted(rosters)}
