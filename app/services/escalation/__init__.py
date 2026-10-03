"""Escalation service: event-based routing to humans. Every escalation has a
reason and rule_id and cannot vanish unresolved.

Public API (Milestone 5):

* :mod:`rules` -- versioned policy/rule layer keeping the THREE concepts
  (policy threshold / peer benchmark / historical deterioration) separate
  (task 5.5).
* :mod:`engine` -- event-based escalation engine with additive, non-destructive
  resolution and case-status reflection (task 5.6).
"""

from app.services.escalation.engine import (
    CASE_STATUS_ESCALATED,
    CASE_STATUS_OPEN,
    EscalationEngine,
    EscalationView,
)
from app.services.escalation.rules import (
    DEFAULT_POLICY,
    ILLUSTRATIVE_LABEL,
    EscalationCategory,
    PolicyRuleEvaluator,
    RuleConcept,
    RuleOutcome,
    RuleRegistry,
    Severity,
)

__all__ = [
    "CASE_STATUS_ESCALATED",
    "CASE_STATUS_OPEN",
    "DEFAULT_POLICY",
    "ILLUSTRATIVE_LABEL",
    "EscalationCategory",
    "EscalationEngine",
    "EscalationView",
    "PolicyRuleEvaluator",
    "RuleConcept",
    "RuleOutcome",
    "RuleRegistry",
    "Severity",
]
