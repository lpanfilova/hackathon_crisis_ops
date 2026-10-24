"""Application configuration loaded from environment variables / .env file.

Values come from the process environment first, then a local `.env` file.
The AWS region and Bedrock model ID are REQUIRED and must be supplied via
`.env` (or the environment) — there are no hardcoded fallbacks for them, so a
missing value fails loudly at startup rather than silently using a default.
Generation knobs keep sensible defaults since they are tuning, not identity.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration sourced from `.env` / environment variables."""

    # Required — must be provided via .env (or environment). No fallback.
    aws_region: str = Field(..., min_length=1)
    bedrock_model_id: str = Field(..., min_length=1)

    # Optional tuning knobs with sensible defaults.
    bedrock_max_tokens: int = 512
    bedrock_temperature: float = 0.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Raises:
        pydantic.ValidationError: if required values (aws_region,
            bedrock_model_id) are absent from both the environment and `.env`.
    """
    return Settings()
