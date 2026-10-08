"""Application configuration loader.

Settings and secrets are read from environment variables (optionally sourced
from a local ``.env`` file for development). Per Requirement 25.2, secrets are
NEVER hardcoded and NEVER logged: secret-bearing fields use ``SecretStr`` so
they are masked in any ``repr``/log output, and this module intentionally does
not emit secret values anywhere.

This is a scaffold. Later milestones extend it with the versioned/hashed
configuration registry (tolerances, policy, peers, metric defs, rules,
parser precedence, source profiles).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings.

    All fields are populated from environment variables (case-insensitive).
    A local ``.env`` file is read for convenience in development but is never
    committed; only ``.env.example`` with placeholder keys is tracked in git.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = Field(default="credit-memo-poc")
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    # --- Persistence ---
    # Default to a local SQLite database for the PoC (migratable to PostgreSQL).
    database_url: str = Field(default="sqlite:///./data/credit_memo.db")

    # --- File store layout (originals never mutated; snapshots frozen) ---
    data_dir: str = Field(default="data")
    output_dir: str = Field(default="output")

    # --- Secrets (read from env only; masked by SecretStr; never logged) ---
    llm_api_key: SecretStr | None = Field(default=None)
    llm_provider: str = Field(default="none")
    llm_model: str | None = Field(default=None)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = Field(default=120, ge=1, le=600)
    llm_max_output_tokens: int = Field(default=12000, ge=256, le=64000)
    llm_max_input_bytes: int = Field(default=1500000, ge=1000)
    sec_user_agent: str | None = Field(default=None)

    # --- Agentic analysis executor (Milestone 5) ---
    # Max concurrent provider calls on the DAG; default 6, configurable up to >=15.
    agent_max_concurrency: int = Field(default=6, ge=1, le=64)
    # Per-call provider timeout; a timeout is captured as a typed error, never a hang.
    agent_timeout_seconds: float = Field(default=120, ge=1, le=600)
    # Upper bound on automatic challenge-driven rerun rounds (Req 15.4).
    max_challenge_rerun_rounds: int = Field(default=1, ge=0, le=5)

    # --- Vertex AI provider (Milestone 18). Credentials via ADC/service account;
    # never committed. All model names are configuration, not hard-coded. ---
    google_cloud_project: str | None = Field(default=None)
    google_cloud_location: str | None = Field(default=None)
    vertex_model_narrow: str | None = Field(default=None)
    vertex_model_orchestrator: str | None = Field(default=None)
    vertex_model_challenge: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance built from the environment."""
    return Settings()
