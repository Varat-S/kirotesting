"""Immutable, content-addressed raw file store (Requirement 1.4, 1.7, 25.3).

Originals are written under a configurable storage root (default
``data/raw``) at a **content-addressed** path derived from the SHA-256 of the
bytes. This gives three properties for free:

* **Identical bytes -> identical path and hash** (Req 1.7): re-ingesting the
  same bytes resolves to the same object; we never create a duplicate.
* **Never overwrite** (Req 1.4): if the content-addressed object already exists
  we leave it untouched; a differing payload hashes to a different path, so an
  existing object is never mutated in place.
* **Filename sanitization / path-traversal prevention** (Req 25.3): the stored
  path is derived from the hash, not from the (untrusted) upload filename. The
  original filename is sanitized and kept only as metadata.

The store is deliberately filesystem-based and simple; it is injected into the
ingestion service so tests can point it at a ``tmp_path`` instead of ``data/``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# Allowed evidence file types (Req 1.3 / 25.3 restrict file types).
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".xlsx",
        ".csv",
        ".json",
        ".html",
        ".htm",
        ".xhtml",
        ".xbrl",
        ".xml",
        ".xsd",
    }
)


class UnsupportedFileTypeError(ValueError):
    """Raised when an upload's extension is not an allowed evidence type."""


class UnsafeFilenameError(ValueError):
    """Raised when an upload filename is unsafe (path traversal, absolute, empty)."""


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of raw bytes (Req 1.5)."""
    return hashlib.sha256(data).hexdigest()


def sanitize_filename(filename: str) -> str:
    """Return a safe basename for ``filename`` or raise ``UnsafeFilenameError``.

    Rejects empty names, absolute paths, and any path-traversal attempt
    (``..`` segments or embedded separators). Only the final path component is
    kept and it must still be a plain name (Req 25.3). The returned value is
    used only as *metadata*; the on-disk path is content-addressed and never
    derived from this value.
    """
    if not filename or not filename.strip():
        raise UnsafeFilenameError("Empty filename is not allowed.")

    # Reject absolute paths and drive-letter style paths outright.
    if filename.startswith(("/", "\\")) or (len(filename) > 1 and filename[1] == ":"):
        raise UnsafeFilenameError(f"Absolute paths are not allowed: {filename!r}")

    # Any path separator (forward or back) means the upload is trying to carry a
    # directory component -- reject it rather than silently flatten, so a
    # traversal attempt can never resolve outside the store (Req 25.3).
    normalized = filename.replace("\\", "/")
    if "/" in normalized:
        raise UnsafeFilenameError(
            f"Filenames must not contain path separators: {filename!r}"
        )

    # Reject traversal / dot tokens as the whole name.
    if normalized in (".", ".."):
        raise UnsafeFilenameError(f"Unsafe filename: {filename!r}")

    base = os.path.basename(normalized)
    if base in ("", ".", ".."):
        raise UnsafeFilenameError(f"Unsafe filename: {filename!r}")
    return base


def validate_extension(safe_name: str) -> str:
    """Return the lower-cased extension of ``safe_name`` or raise.

    Enforces the allowed-type restriction (Req 1.3 / 25.3).
    """
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file type {ext!r}. Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )
    return ext


@dataclass(frozen=True)
class StoredObject:
    """Result of storing raw bytes: its hash, on-disk path, and newness."""

    sha256: str
    storage_path: str
    newly_written: bool


class RawFileStore:
    """Content-addressed, write-once store for immutable originals."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, digest: str, ext: str) -> Path:
        # Shard by the first two hex chars to avoid huge flat directories.
        return self._root / digest[:2] / f"{digest}{ext}"

    def store(self, data: bytes, ext: str) -> StoredObject:
        """Write ``data`` immutably and return where it landed.

        Identical bytes always resolve to the same path (Req 1.7). An existing
        object is never overwritten (Req 1.4): if the target exists we return it
        as-is with ``newly_written=False``.
        """
        digest = sha256_bytes(data)
        target = self._path_for(digest, ext)

        if target.exists():
            # Already stored; never mutate an existing object.
            return StoredObject(digest, str(target), newly_written=False)

        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create so a concurrent writer can never clobber bytes.
        with open(target, "xb") as fh:
            fh.write(data)
        return StoredObject(digest, str(target), newly_written=True)
