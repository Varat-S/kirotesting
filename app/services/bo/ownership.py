"""Content-based ownership screening for BO roster v3 findings.

This versioned policy catches clear cross-remit claims before promotion.  It is
conservative: evidence may cross specialist boundaries, but the conclusion may
not.  Flagged findings stay in the audit trace with a suggested destination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


OWNERSHIP_POLICY_VERSION = "bo-semantic-ownership-2"


@dataclass(frozen=True)
class OwnershipDecision:
    in_scope: bool
    owner: str
    reason: str
    suggested_destination: str | None = None
    policy_version: str = OWNERSHIP_POLICY_VERSION


@dataclass(frozen=True)
class _Rule:
    source: str
    destination: str
    pattern: str
    reason: str
    unless: str | None = None


_RULES = (
    _Rule("M1S2", "M2", r"\b(?:hurricane|earthquake|wildfire|pandemic|climate|natural disaster)s?\b",
          "Industry-wide external-event exposure is owned by M2."),
    _Rule("M1S2", "M2", r"\bgeographic(?:al)? concentration\b.*\b(?:exposure|risk|vulnerab)",
          "Geography may be context for M1S2, but geographic exposure is owned by M2."),
    _Rule("M1S4", "M3", r"\b(?:board|governance|executive succession|management integrity|misconduct)\b",
          "Corporate governance and senior-management conclusions are owned by M3."),
    _Rule("M1S5", "M4", r"\b(?:capital allocation|acquisition strategy|share repurchase|dividend policy|capex strategy)\b",
          "Strategy and capital-allocation conclusions are owned by M4."),
    _Rule("M1S3", "M1S2", r"\b(?:payer|customer) mix\b",
          "Payer/customer mix alone is a volume-quality conclusion owned by M1S2.",
          unless=r"\b(?:retention|switching|loyalty|referral|steer|network access)\b"),
    _Rule("M1S1", "M1S2", r"\brevenue (?:grew|growth|declined|fell|increased)\b",
          "Revenue movement alone does not establish pricing discretion.",
          unless=r"\b(?:price|pricing|rate|reimbursement|yield|unit revenue)\b"),
)


def screen_ownership(owner: str, claim: str) -> OwnershipDecision:
    text = " ".join(claim.lower().split())
    for rule in _RULES:
        if rule.source != owner or not re.search(rule.pattern, text, re.I):
            continue
        if rule.unless and re.search(rule.unless, text, re.I):
            continue
        return OwnershipDecision(False, owner, rule.reason, rule.destination)
    return OwnershipDecision(True, owner, "Claim remains within the assigned remit.")
