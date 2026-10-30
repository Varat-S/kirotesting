"""A number is bound when it is a supplied value, rounded or as a percentage."""

from app.services.bo.hypotheses import unbound_numbers


def test_supplied_value_quoted_at_full_precision_is_bound():
    # Live HCA runs: the orchestrator copied raw six-decimal ratios.
    text = "Managed care was 0.488995 of revenue; payer mix moved -0.012736."
    assert unbound_numbers(text, [0.488995, -0.012736], []) == []


def test_rounded_and_percentage_forms_stay_bound():
    assert unbound_numbers("Managed care was 48.9% (0.489).", [0.488995], []) == []
    # capex_to_revenue 0.0653968... rounded to six decimals.
    assert unbound_numbers("Capex was 0.065397 of revenue.", [0.0653968253968254], []) == []


def test_unsupplied_number_is_still_unbound():
    assert unbound_numbers("Margin was 0.488996.", [0.488995], []) == ["0.488996"]


def test_thousands_separator_is_read_as_one_number():
    # Live HCA run: "$7,692.0 million" was read as "692.0" and rejected.
    assert unbound_numbers("Cash flow was $7,692.0 million.", [7692.0], []) == []
    assert unbound_numbers("Cash flow was $7,692.0 million.", [692.0], []) == ["7,692.0"]
    assert unbound_numbers("Occupancy took 1,250 days.", [1250.0], []) == []
