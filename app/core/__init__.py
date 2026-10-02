"""Core layer: configuration loading and versioning, hashing, time/cutoff
enforcement, IDs, logging, and security helpers.

Secrets are read exclusively from environment variables and are never
hardcoded or logged (see Requirement 25.2).
"""

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
