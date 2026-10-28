"""Module 4 schema contract tests.

The four deliverables must expose the EXACT column structures the specialist is
required to produce, and the prompt catalogue must register the Module 4 agent.
"""

from __future__ import annotations

from app.prompts.templates import PROMPT_CATALOGUE
from app.schemas.module4 import (
    Deliverable1,
    Module4Output,
    NonNoteDebtObligation,
    SeniorNoteTranche,
)
from app.services.modules.module4.prompt import MODULE4_AGENT_NAME
from tests.fixtures.hca_module4 import hca_module4_output


def test_fixture_validates_into_module4_output() -> None:
    out = Module4Output.model_validate(hca_module4_output())
    assert out.deliverable_1 and out.deliverable_2
    assert out.deliverable_3 and out.deliverable_4


def test_senior_note_tranche_has_required_columns() -> None:
    fields = set(SeniorNoteTranche.model_fields)
    assert fields == {
        "senior_note_series",
        "legal_borrower",
        "principal_amount_m",
        "coupon_interest_rate",
        "maturity_year",
        "collateral_security_status",
        "guarantors_credit_support",
        "priority_ranking_tier",
    }


def test_non_note_obligation_has_required_columns() -> None:
    fields = set(NonNoteDebtObligation.model_fields)
    assert fields == {
        "obligation_facility",
        "legal_borrower",
        "principal_drawn_m",
        "available_facility_limit_m",
        "interest_rate_terms",
        "maturity",
        "collateral_security_status",
        "guarantors_credit_support",
        "priority_ranking_tier",
    }


def test_deliverable1_has_four_sections() -> None:
    fields = set(Deliverable1.model_fields)
    assert fields == {
        "extraction_source",
        "senior_notes_schedule",
        "non_note_obligations_schedule",
        "creditor_priority_framework",
        "reconciling_items",  # lines between the instruments and the total (Validator 8, check 6)
    }


def test_module4_prompt_registered_in_catalogue() -> None:
    names = {entry["name"] for entry in PROMPT_CATALOGUE}
    assert MODULE4_AGENT_NAME in names
