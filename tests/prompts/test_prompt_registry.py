"""Prompt registry tests (task 6.2; Req 19.1, 19.5)."""

from __future__ import annotations

from app.prompts.registry import PromptRegistry, format_prompt_id
from app.prompts.templates import PROMPT_CATALOGUE


def test_prompt_id_format():
    assert format_prompt_id("business_analysis", 1) == "business_analysis_v1.0"


def test_register_catalogue_assigns_versioned_ids(db_session):
    registry = PromptRegistry(db_session)
    registered = registry.register_catalogue()

    names = {p.name for p in registered}
    assert names == {e["name"] for e in PROMPT_CATALOGUE}

    analysis = registry.latest("business_analysis")
    assert analysis is not None
    assert analysis.prompt_id == "business_analysis_v1.0"
    assert analysis.version == 1
    assert analysis.content_hash  # a stable content hash exists


def test_register_is_idempotent_for_identical_content(db_session):
    registry = PromptRegistry(db_session)
    first = registry.register(
        "p", template="hello", response_schema={"type": "object"}
    )
    again = registry.register(
        "p", template="hello", response_schema={"type": "object"}
    )
    assert first.version == again.version == 1
    assert first.content_hash == again.content_hash


def test_prompt_update_creates_new_version_and_flags_regression(db_session):
    """A prompt update allocates a new version + a REAL needs-regression signal."""
    registry = PromptRegistry(db_session)
    v1 = registry.register("p", template="v1", response_schema={"type": "object"})
    v2 = registry.register("p", template="v2", response_schema={"type": "object"})

    assert v2.version == 2
    assert v1.content_hash != v2.content_hash

    # Both outstanding versions signal that regression tests must run (Req 19.5).
    needing = {p.prompt_id for p in registry.prompts_needing_regression()}
    assert "p_v2.0" in needing

    cleared = registry.clear_regression("p", 2)
    assert cleared.needs_regression is False
    assert "p_v2.0" not in {
        p.prompt_id for p in registry.prompts_needing_regression()
    }


def test_prior_versions_are_never_rewritten(db_session):
    registry = PromptRegistry(db_session)
    registry.register("p", template="v1", response_schema={"type": "object"})
    registry.register("p", template="v2", response_schema={"type": "object"})

    v1 = registry.get("p", 1)
    assert v1 is not None
    assert v1.template == "v1"  # historical version preserved intact
