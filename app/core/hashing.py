"""Deterministic content hashing for versioned configuration artifacts.

Used by the configuration registry (Requirement 19.6) to compute a stable
SHA-256 over an artifact's content. Hashing is canonical: JSON is serialized
with sorted keys and no insignificant whitespace, so two logically-equal
artifacts hash identically regardless of key order, and any content change
yields a different hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(content: Any) -> str:
    """Serialize ``content`` to a canonical JSON string (sorted keys)."""
    return json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def content_hash(content: Any) -> str:
    """Return the SHA-256 hex digest of ``content`` in canonical form."""
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()
