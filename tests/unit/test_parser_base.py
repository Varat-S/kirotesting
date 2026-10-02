"""Unit tests for the shared parser interface and precedence config (M3).

Confirms parser name/version metadata plumbing and that the default parser
precedence can be registered as a versioned configuration artifact (the
``parser_precedence`` ConfigRegistry kind) -- without any reconciliation logic,
which is Milestone 4.
"""

from __future__ import annotations

from app.core.config_registry import ConfigRegistry
from app.schemas.enums import ExtractionMethod
from app.services.extraction.base import (
    DEFAULT_PARSER_PRECEDENCE,
    ParserMetadata,
)
from app.services.extraction.xbrl import XbrlParser


def test_parser_metadata_created_by_and_dict() -> None:
    meta = ParserMetadata(
        name="demo", version="2.1.0", extraction_method=ExtractionMethod.XBRL
    )
    assert meta.created_by == "demo@2.1.0"
    assert meta.as_dict() == {
        "parser_name": "demo",
        "parser_version": "2.1.0",
        "extraction_method": "xbrl",
    }


def test_parser_exposes_metadata() -> None:
    parser = XbrlParser()
    assert parser.metadata.name == "xbrl_parser"
    assert parser.metadata.extraction_method is ExtractionMethod.XBRL


def test_default_precedence_is_registerable_config(db_session) -> None:
    registry = ConfigRegistry(db_session)
    registered = registry.register("parser_precedence", DEFAULT_PARSER_PRECEDENCE)
    assert registered.artifact_kind == "parser_precedence"
    assert registered.version == 1
    # Idempotent on identical content.
    again = registry.register("parser_precedence", DEFAULT_PARSER_PRECEDENCE)
    assert again.version == 1
    # Order lists structured sources before lossy ones.
    order = DEFAULT_PARSER_PRECEDENCE["order"]
    assert order.index("xbrl") < order.index("pdf_table") < order.index("ocr")
