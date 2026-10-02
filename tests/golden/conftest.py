"""Shared helpers for Milestone 3 parser golden tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the directory holding synthetic golden fixtures."""
    return FIXTURES_DIR


def read_fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()
