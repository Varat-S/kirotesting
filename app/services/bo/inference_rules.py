"""Deterministic guards against known unsupported inferential steps (Layer C).

Each rule names one transition a Business Overview claim must not make on the
evidence a filing normally provides, for example "a reimbursement agreement
guarantees volume" or "operating pressure means the borrower cannot service its
debt".

WHAT THESE RULES ARE: lexical pattern checks over the claim text (and, for the
BO/FO boundary, the stated mechanism). They catch the listed over-readings when
they are worded in recognisable ways, and they are negation-aware so a claim
that DENIES the over-reading is not flagged.

WHAT THEY ARE NOT: proof that a claim is logically sound. A claim that passes
has not been shown to be correct, and an unusual wording can evade a pattern.
Every hit is recorded as a ``review_flag`` marked ``heuristic``, with the rule
id and the effect applied, so a reviewer can see exactly why a finding was held
back.

Effects:

* ``audit_only`` — the hypothesis cannot be used as a finding (recorded as
  unverified). Used where the claim asserts the forbidden conclusion outright.
* ``qualify``    — the hypothesis is kept as a qualified finding and must carry
  its qualification. Used where the claim may be fair but is under-supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_FLAGS = re.I | re.S


@dataclass(frozen=True)
class InferenceRule:
    rule_id: str
    description: str
    effect: str  # audit_only | qualify
    triggers: tuple[str, ...]  # ALL must match the claim
    exempt: tuple[str, ...] = ()  # ANY match means the claim denies/qualifies it
    # Apply only when the hypothesis' evidence basis is one of these (empty = any).
    only_basis: tuple[str, ...] = ()
    # Do not apply when the claim is presented as a management claim.
    skip_management_claims: bool = False
    creates_fo_handoff: str | None = None
    correction: str | None = None

    def hit(self, text: str) -> bool:
        return (all(re.search(t, text, _FLAGS) for t in self.triggers)
                and not any(re.search(e, text, _FLAGS) for e in self.exempt))


_NEG = r"\b(?:not|no|never|neither|without|cannot|can't|n't|rather than)\b"


class _ContractCommitmentRule(InferenceRule):
    """Scope negation to each commitment assertion, not the whole hypothesis.

    A noun such as "commitments" can follow "lack", and an unrelated denial
    elsewhere must not exempt an affirmative guarantee. This is still a
    lexical guard; evidence sufficiency remains the hypothesis validator's job.
    """

    def hit(self, text: str) -> bool:
        subject = r"\b(?:contracts?|agreements?|arrangements?|contractually)\b"
        action = r"\b(?:guarantee\w*|secure\w*|assure\w*|ensure\w*|commit\w*|lock(?:s|ed|ing)?[- ]in)\b"
        target = r"\b(?:volumes?|revenues?|patients?|admissions?|demand|minimum purchases?)\b"
        denial = (r"\b(?:not(?!\s+only\b)|no(?!\s+(?:fewer|less)\s+than\b)|never|"
                  r"without|cannot|lack(?:s|ed|ing)?|absence\s+of|rather\s+than)\b|n't\b")
        uncertainty = (r"\b(?:unclear|unknown|uncertain|not\s+(?:established|evidenced|verified|disclosed))"
                       r"[^.;]{0,80}\b(?:whether|that|if)\b|\b(?:may|might|could)\s*$")
        for clause in re.split(r"[.!?;]|\b(?:but|however|yet|whereas|although)\b", text, flags=re.I):
            for verb in re.finditer(action, clause, _FLAGS):
                before, after = clause[:verb.start()], clause[verb.end():]
                if not re.search(subject + r"[^.;]{0,120}$", before, _FLAGS):
                    continue
                value = re.search(target, after[:60], _FLAGS)
                passive = re.search(r"\b(?:revenues?|volumes?)\s+(?:is|are)\s+contractually\s*$", before, _FLAGS)
                if value is None and passive is None:
                    continue
                # A new coordinated assertion gets its own negation scope.
                prefix = re.split(r",|\b(?:and|or)\b", before, flags=re.I)[-1]
                if re.search(denial, prefix, _FLAGS) or re.search(uncertainty, prefix, _FLAGS):
                    continue
                if value is not None and re.search(denial, after[:value.start()], _FLAGS):
                    continue
                if re.search(r"\b(?:is|are|remains?)\s+(?:not\s+(?:established|evidenced|verified|disclosed)|unverified|unclear|unknown)\b", after, _FLAGS):
                    continue
                return True
        return False

RULES: tuple[InferenceRule, ...] = (
    _ContractCommitmentRule(
        "rate_agreement_to_volume_guarantee",
        "A reimbursement or price agreement does not guarantee volumes or revenue.",
        "audit_only",
        (r"(?:contract|agreement|arrangement)\w*[^.]{0,120}\b(?:guarantee|secure|lock[- ]in|"
         r"assure|ensure|commit)\w*[^.]{0,60}\b(?:volume|revenue|patient|admission|demand)"
         r"|revenues? (?:is|are) contractually (?:secured|guaranteed|committed)"
         r"|contractually (?:secured|guaranteed) (?:revenue|volume)",),
        exempt=(_NEG + r"[^.]{0,60}\b(?:guarantee|secure|assure|ensure|commit|lock)",
                r"rates?[^.]{0,40}\b(?:not|rather than)\b[^.]{0,20}volumes?"),
        correction="State that the agreement sets rates and that no volume or "
                   "revenue commitment is evidenced.",
    ),
    InferenceRule(
        "unit_revenue_growth_to_pricing_power",
        "Higher revenue per unit can reflect payer, case or service mix; it does "
        "not by itself show pricing power.",
        "audit_only",
        (r"revenues? per (?:equivalent )?(?:admission|unit|patient|case)",
         r"pricing power|price increases?|ability to (?:raise|increase) prices|"
         r"stronger pricing|pricing strength|rate increases? (?:demonstrate|show)"),
        exempt=(r"\bmix\b|acuity|complexity|intensity",
                r"(?:does|do|did) not (?:by itself |alone |necessarily )?"
                r"(?:establish|prove|show|demonstrate|isolate|indicate)",
                r"cannot be (?:isolated|separated|attributed|established)",
                r"not (?:isolated|separable|established)"),
        correction="Say whether price can be separated from mix on the evidence.",
    ),
    InferenceRule(
        "narrow_networks_to_switching_costs",
        "Narrow networks do not by themselves establish the level of switching costs.",
        "audit_only",
        (r"narrow(?:er)?[- ]networks?|tiered networks?",
         r"(?:low|high|minimal|limited|significant) switching costs?|"
         r"switching costs? (?:are|is|remain|appear) (?:low|high|minimal|limited)"),
        exempt=(r"depend\w* on|cannot be (?:established|determined)|not established|"
                r"unclear|ambiguous|either direction|do(?:es)? not (?:by itself |alone )?"
                r"(?:establish|mean|imply)",),
        correction="Classify switching costs as not established unless plan design "
                   "and patient options are evidenced.",
    ),
    InferenceRule(
        "stated_strategy_to_proven_discipline",
        "An acquisition intention is not evidence of disciplined or successful "
        "capital allocation.",
        "qualify",
        (r"acquisition|acquir\w+|M&A",
         r"\b(?:disciplined|successful(?:ly)?|proven|effective|value[- ]creating|"
         r"track record)\b"),
        exempt=(_NEG + r"[^.]{0,50}\b(?:disciplined|successful|proven|effective|evidence)",
                r"\b(?:stated|intends?|intention|aims?|seeks?|plans?) \b[^.]{0,60}\bnot\b",
                r"outcomes? (?:are|is) not (?:measured|evidenced|disclosed)"),
        only_basis=("management_statement", "generic_risk_disclosure", "inference", "none"),
        skip_management_claims=True,
        correction="Report it as a stated strategy unless realised outcomes are evidenced.",
    ),
    InferenceRule(
        "generic_departure_risk_to_key_person_dependency",
        "A generic warning about losing managers does not establish high "
        "key-person dependency.",
        "qualify",
        (r"key[- ](?:person|management|personnel|employee)|management (?:dependen|departure)|"
         r"loss of (?:key|senior)|senior management",
         r"\b(?:high|material|significant|critical|heavy|substantial)\b[^.]{0,30}"
         r"\b(?:dependen\w+|risk|reliance)|\b(?:dependen\w+|reliance)\b[^.]{0,30}"
         r"\b(?:high|material|significant|critical)\b"),
        exempt=(r"generic|not established|does not establish|cannot be established|"
                r"no company[- ]specific",),
        only_basis=("management_statement", "generic_risk_disclosure", "inference", "none"),
        correction="Classify key-person dependency as not established without "
                   "company-specific evidence.",
    ),
    InferenceRule(
        "payer_category_share_to_counterparty_concentration",
        "A payer-category share does not show dependence on a concentrated group "
        "of individual counterparties.",
        "qualify",
        (r"concentrated (?:group|number|set|base) of|small (?:number|group) of|"
         r"limited number of|few (?:large )?(?:payers|insurers|customers)",
         r"payers?|insurers?|customers?|counterpart",
         # ...and the support offered is a payer CATEGORY.
         r"Medicare|Medicaid|government|managed care|commercial insur|programs?"),
        exempt=(r"categor\w+|not (?:individual|a single)|no single|"
                r"individual (?:payer|insurer|counterpart)\w* (?:is|are) not",),
        correction="Describe the dependence as a payer-category share unless "
                   "individual counterparties are evidenced.",
    ),
    InferenceRule(
        "accounting_estimate_to_cash_loss",
        "An accounting estimate such as implicit price concessions is not an "
        "incremental cash loss, and one amount shows no trend.",
        "qualify",
        (r"implicit price concessions?|contractual (?:allowances?|adjustments?)",
         r"\bloss(?:es)?\b|deteriorat\w+|worsen\w+|bad debts? (?:increas|ris)|"
         r"cash (?:leakage|loss|shortfall)|increasing(?:ly)? uncollect"),
        exempt=(r"accounting (?:estimate|adjustment)|period[- ]end|"
                r"not (?:an? )?(?:incremental|cash)|prior[- ](?:year|period)|"
                r"compar\w+|decreas\w+|declin\w+|no (?:adverse )?trend|"
                r"trend (?:is|cannot be) (?:not )?established",),
        correction="State whether the figure is a period-end estimate and compare "
                   "periods before describing a trend.",
    ),
    InferenceRule(
        "essential_service_to_recession_resistance",
        "Essential-service status does not establish recession resistance.",
        "audit_only",
        (r"recession[- ](?:proof|resistant|resilient|immune)|immune to (?:economic|the economic|downturn)|"
         r"(?:unaffected|not affected) by (?:economic|the economy|downturn|recession)|"
         r"insulated from (?:economic|downturn|recession)",),
        exempt=(_NEG + r"[^.]{0,40}(?:recession|immune|insulated|unaffected)",
                r"(?:does|do) not (?:establish|prove|show)",
                r"cannot be (?:concluded|established)"),
        correction="Qualify demand resilience unless evidence across a downturn is cited.",
    ),
    InferenceRule(
        "operating_pressure_to_debt_capacity",
        "Debt service, liquidity, covenant and rating conclusions belong to the "
        "Financial Overview.",
        "audit_only",
        (r"(?:servic\w+|repay\w*|meet\w*|pay\w*) (?:its |the |their |on )?(?:\w+ )?"
         r"(?:debt|indebtedness|interest|obligations)\b|debt[- ]service|"
         r"liquidity (?:crisis|shortfall|crunch|problem|pressure|stress)|"
         r"covenant (?:breach|violation|default|compliance)|\bdefault(?:s|ing)? on\b|"
         r"refinanc\w+ risk|insolven\w+|credit rating|borrowing capacity|leverage (?:ratio|capacity)",),
        exempt=(r"Financial Overview|\bFO\b|outside (?:the )?(?:scope|Business Overview|BO)|"
                r"not (?:assessed|concluded|evaluated) here|is not a (?:BO|business)|"
                r"for financial analysis|requires? financial (?:analysis|quantification)",),
        creates_fo_handoff="debt_service_capacity",
        correction="State the operating effect only and hand the financial "
                   "question to the Financial Overview.",
    ),
)

RULES_BY_ID = {rule.rule_id: rule for rule in RULES}


def evaluate(*, claim: str, mechanism: str | None, claim_type: str,
             evidence_basis: str) -> list[dict[str, Any]]:
    """Return the review flags raised for one live hypothesis."""
    text = " ".join(part for part in (claim, mechanism) if part)
    flags = []
    for rule in RULES:
        if rule.skip_management_claims and claim_type == "management_claim":
            continue
        if rule.only_basis and evidence_basis not in rule.only_basis:
            continue
        if rule.hit(text):
            flags.append({
                "rule_id": rule.rule_id, "layer": "C_inferential_validity",
                "effect": rule.effect, "description": rule.description,
                "correction": rule.correction, "heuristic": True,
                "fo_handoff_variable": rule.creates_fo_handoff,
            })
    return flags
