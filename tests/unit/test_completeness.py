"""Source-package completeness tests (Requirement 22).

Covers task 2.5: a missing critical source triggers a mandatory escalation /
inability-to-conclude; a missing optional source records reduced coverage
without escalating; the determination is deterministic and driven by a versioned
``source_profiles`` config artifact (not LLM confidence).
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.services.ingestion import (
    CompletenessEvaluator,
    SourceProfileError,
)

# Illustrative airline profile (example only; the profile is configuration).
AIRLINE_PROFILE = {
    "label": "ILLUSTRATIVE — NOT BANK POLICY",
    "sources": {
        "annual_filing": "critical",
        "quarterly_filing": "critical",
        "debt_maturity_disclosure": "important",
        "operating_statistics": "important",
        "external_industry_dataset": "important",
        "macro_fuel_dataset": "optional",
    },
}


def _register_profile(session: Session) -> ConfigRegistry:
    reg = ConfigRegistry(session)
    reg.register("source_profiles", AIRLINE_PROFILE, label="airline-illustrative")
    return reg


def test_missing_critical_source_mandatory_escalation(db_session: Session) -> None:
    """Req 22.2: missing critical source -> mandatory escalation / inability."""
    reg = _register_profile(db_session)
    evaluator = CompletenessEvaluator(reg)

    assessment = evaluator.assess(
        present_source_types=[
            "quarterly_filing",
            "debt_maturity_disclosure",
            "operating_statistics",
            "external_industry_dataset",
            "macro_fuel_dataset",
        ]
    )
    assert assessment.mandatory_escalation is True
    assert assessment.inability_to_conclude is True
    assert assessment.missing_critical == ["annual_filing"]
    assert assessment.complete is False
    assert assessment.profile_version == 1


def test_missing_optional_source_reduced_coverage_no_escalation(
    db_session: Session,
) -> None:
    """Req 22.3: missing optional source -> reduced coverage, no escalation."""
    reg = _register_profile(db_session)
    evaluator = CompletenessEvaluator(reg)

    assessment = evaluator.assess(
        present_source_types=[
            "annual_filing",
            "quarterly_filing",
            "debt_maturity_disclosure",
            "operating_statistics",
            "external_industry_dataset",
        ]
    )
    assert assessment.mandatory_escalation is False
    assert assessment.reduced_coverage is True
    assert assessment.missing_optional == ["macro_fuel_dataset"]
    assert assessment.missing_critical == []


def test_complete_package(db_session: Session) -> None:
    reg = _register_profile(db_session)
    evaluator = CompletenessEvaluator(reg)
    assessment = evaluator.assess(present_source_types=AIRLINE_PROFILE["sources"].keys())
    assert assessment.complete is True
    assert assessment.mandatory_escalation is False
    assert assessment.reduced_coverage is False


def test_assessment_is_deterministic(db_session: Session) -> None:
    """Req 22.4: the determination is deterministic (same inputs -> same result)."""
    reg = _register_profile(db_session)
    evaluator = CompletenessEvaluator(reg)
    present = ["quarterly_filing"]
    a = evaluator.assess(present_source_types=present)
    b = evaluator.assess(present_source_types=present)
    assert a == b


def test_no_profile_configured_raises(db_session: Session) -> None:
    """Req 21.1: no silent default profile; absence is surfaced."""
    reg = ConfigRegistry(db_session)
    evaluator = CompletenessEvaluator(reg)
    with pytest.raises(SourceProfileError):
        evaluator.assess(present_source_types=["annual_filing"])


def test_pinned_profile_version_reproducible(db_session: Session) -> None:
    """A historical run can pin an explicit profile version (Req 19.6)."""
    reg = ConfigRegistry(db_session)
    reg.register("source_profiles", AIRLINE_PROFILE, label="v1")
    # Change the profile -> new version.
    changed = dict(AIRLINE_PROFILE)
    changed["sources"] = dict(AIRLINE_PROFILE["sources"])
    changed["sources"]["macro_fuel_dataset"] = "critical"
    reg.register("source_profiles", changed, label="v2")

    evaluator = CompletenessEvaluator(reg)
    present = ["annual_filing", "quarterly_filing", "debt_maturity_disclosure",
               "operating_statistics", "external_industry_dataset"]

    v1 = evaluator.assess(present_source_types=present, profile_version=1)
    v2 = evaluator.assess(present_source_types=present, profile_version=2)
    # Under v1 the macro/fuel dataset is optional (no escalation);
    # under v2 it became critical (escalation).
    assert v1.mandatory_escalation is False
    assert v2.mandatory_escalation is True
