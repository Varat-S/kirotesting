"""Domain-aware coverage for the BO specialists (no borrower is hard-coded).

A sector profile adds two things to the generic plans:

* a CHECKLIST — questions a specialist must answer, each with a retrieval query
  that guarantees the relevant admitted passages reach it. A checklist question
  is a prompt to look, never a conclusion: the agent may answer that the
  evidence does not support a finding;
* SECTOR INDICATORS — reported concepts worth turning into deterministic
  indicators when the filing tags them (cost lines, accounting estimates).

Case-specific COVERAGE TESTS (for example "what does this filing say about
entity X?") are supplied per run from a file next to the case package. They use
the same mechanism and never enter generic code.

The sector is detected from the admitted evidence, or set explicitly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.hashing import content_hash


@dataclass(frozen=True)
class CoverageCheck:
    check_id: str
    agent_codes: tuple[str, ...]  # BO1..BO6 that must answer it
    question: str
    terms: tuple[str, ...]  # retrieval terms (case-insensitive regex)
    # A validated hypothesis "addresses" the check only if it matches these.
    finding_patterns: tuple[str, ...] = ()
    max_passages: int = 2
    origin: str = "sector"  # sector | case

    def as_prompt(self) -> dict[str, str]:
        return {"check_id": self.check_id, "question": self.question}


@dataclass(frozen=True)
class SectorIndicator:
    indicator_id: str
    concept_pattern: str  # matched against the reported concept name
    label: str
    group: str
    kind: str  # share_of_revenue | level
    caveat: str | None = None


@dataclass(frozen=True)
class SectorProfile:
    sector_id: str
    detection_pattern: str
    min_matching_passages: int
    checklist: tuple[CoverageCheck, ...]
    indicators: tuple[SectorIndicator, ...] = ()
    # Reported revenue breakdowns worth turning into shares, by concept.
    revenue_breakdown_concepts: tuple[tuple[str, str], ...] = ()  # (pattern, group)

    def identity(self) -> str:
        return content_hash([
            self.sector_id,
            [[c.check_id, list(c.agent_codes), list(c.terms)] for c in self.checklist],
            [[i.indicator_id, i.concept_pattern, i.kind] for i in self.indicators],
        ])


def _check(check_id, agents, question, *terms, finding=(), k=2):
    return CoverageCheck(check_id, tuple(agents), question, tuple(terms),
                         tuple(finding), k)


HEALTHCARE = SectorProfile(
    sector_id="healthcare_provider",
    # Language of an operator of care facilities, not of a supplier to them.
    detection_pattern=(r"\bour hospitals\b|equivalent admissions|inpatient admissions|"
                       r"payer mix|uncompensated care|patient days"),
    min_matching_passages=4,
    checklist=(
        _check("hc.payer_mix", ("BO2",),
               "What is the payer mix, by payer CATEGORY, and on what basis "
               "(revenues or admissions) and for which period is each share "
               "stated? A category share is not a share of one counterparty.",
               r"managed care and insurers", r"percentages of our (?:inpatient )?(?:revenues|admissions)",
               r"payer mix", finding=(r"Medicare|Medicaid|managed care|payer",)),
        _check("hc.contract_rates_vs_volumes", ("BO1", "BO2"),
               "Do payer agreements fix reimbursement RATES, and does anything in "
               "the evidence commit a payer to patient VOLUMES or revenue? State "
               "explicitly whether a volume or revenue commitment is evidenced.",
               r"managed care (?:plans|contracts|agreements|programs)",
               r"negotiat\w+", r"discounts from established (?:gross )?charges",
               r"contract (?:renegotiations|renewals|terms)",
               finding=(r"rates?|reimburs|discount|negotiat|contract|agreement",
                        r"volume|guarantee|commit")),
        _check("hc.unit_revenue_vs_pricing", ("BO3",),
               "What changed revenue per unit of volume (for example revenue per "
               "equivalent admission), and can price be separated from payer mix, "
               "case mix or service mix on the evidence?",
               r"revenues? per equivalent admission", r"equivalent admissions",
               r"same facility", finding=(r"revenue per equivalent admission|per unit",)),
        _check("hc.revenue_realisation", ("BO2",),
               "What does the evidence say about collectability: uninsured and "
               "self-pay exposure, and accounting estimates such as implicit price "
               "concessions? Say whether a figure is a period-end estimate or an "
               "annual amount, and whether comparative periods show a trend.",
               r"implicit price concessions", r"uncompensated care", r"uninsured",
               finding=(r"implicit price concession|uninsured|uncompensated|collect",)),
        _check("hc.operating_entities", ("BO4",),
               "Which named subsidiaries, affiliates, joint ventures or operating "
               "entities matter to how the business runs, and what is each one's role?",
               r"subsidiar\w+", r"joint ventures?", r"College", r"wholly[- ]owned",
               finding=(r"subsidiar|joint venture|college|affiliate|entity",)),
        _check("hc.workforce", ("BO4",),
               "What does the evidence show about labour cost and staffing, and is "
               "any mitigating initiative shown to WORK (a measured outcome), or "
               "only to exist?",
               r"salaries and benefits", r"contract labor", r"nurs\w+ (?:shortage|staffing)",
               r"labor costs?", finding=(r"labou?r|salar|nurs|staff|workforce",)),
        _check("hc.geography", ("BO5",),
               "Is revenue or volume concentrated geographically? Quote the share "
               "with its denominator and reporting period.",
               r"geographic concentration", r"revenues represented \d+\s?%",
               r"located in [A-Z][a-z]+ and [A-Z][a-z]+",
               finding=(r"geograph|concentrat|state",)),
        _check("hc.local_competition", ("BO3",),
               "How is competition described at the LOCAL market level, and is "
               "there comparative evidence of the company's position (share, "
               "rankings), as opposed to management's stated aims?",
               r"compet\w+ (?:with|among|from)", r"communities we serve",
               r"other hospitals", r"market share",
               finding=(r"compet|market",)),
    ),
    indicators=(
        SectorIndicator("labour_cost_to_revenue", r"LaborAndRelatedExpense$",
                        "Salaries and benefits / revenue", "workforce",
                        "share_of_revenue"),
        SectorIndicator("supplies_to_revenue", r"SuppliesExpense$",
                        "Supplies expense / revenue", "workforce", "share_of_revenue"),
        SectorIndicator(
            "implicit_price_concessions", r"EstimatedImplicitPriceConcessions$",
            "Estimated implicit price concessions (as reported)", "realisation", "level",
            caveat=("An accounting estimate that adjusts revenue and receivables to "
                    "the amount expected to be collected. It is not an incremental "
                    "cash loss, and a single amount shows no trend; compare the "
                    "prior-period value supplied alongside.")),
    ),
    revenue_breakdown_concepts=((r"RevenuesFromThirdPartyPayers$", "payer"),),
)

SECTOR_PROFILES = {HEALTHCARE.sector_id: HEALTHCARE}


def detect_sector(evidence) -> SectorProfile | None:
    """Pick a profile from the admitted narrative (deterministic), or none."""
    for profile in SECTOR_PROFILES.values():
        pattern = re.compile(profile.detection_pattern, re.I)
        hits = sum(1 for p in evidence.narrative_evidence
                   if pattern.search(p.get("text") or ""))
        if hits >= profile.min_matching_passages:
            return profile
    return None


def load_case_checks(path: str | Path) -> list[CoverageCheck]:
    """Case-specific coverage tests from a JSON file next to the package."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    checks = []
    for item in raw.get("coverage_checks", []):
        checks.append(CoverageCheck(
            check_id=item["check_id"], agent_codes=tuple(item["agent_codes"]),
            question=item["question"], terms=tuple(item["terms"]),
            finding_patterns=tuple(item.get("finding_patterns", [])),
            max_passages=int(item.get("max_passages", 3)), origin="case"))
    return checks


@dataclass
class CoverageOutcome:
    check: CoverageCheck
    agent_id: str
    passages_located: list[str] = field(default_factory=list)
    response_status: str = "no_response"
    hypothesis_ids: list[str] = field(default_factory=list)
    addressed_by: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def status(self) -> str:
        if self.addressed_by:
            return "addressed"
        if not self.passages_located:
            return "no_evidence_in_filing"
        if self.response_status == "not_supported_by_evidence":
            return "agent_reported_not_supported"
        return "unresolved"

    def as_payload(self) -> dict[str, Any]:
        return {
            "check_id": self.check.check_id, "origin": self.check.origin,
            "agent_id": self.agent_id, "question": self.check.question,
            "passages_located": self.passages_located,
            "agent_response": self.response_status,
            "hypothesis_ids_cited": self.hypothesis_ids,
            "addressed_by_validated_hypotheses": self.addressed_by,
            "status": self.status, "note": self.note,
        }
