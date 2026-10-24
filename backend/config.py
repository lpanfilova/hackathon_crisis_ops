"""Application configuration loaded from environment variables / .env file.

Values come from the process environment first, then a local `.env` file.
The AWS region and Bedrock model ID are REQUIRED and must be supplied via
`.env` (or the environment) — there are no hardcoded fallbacks for them, so a
missing value fails loudly at startup rather than silently using a default.
Generation knobs keep sensible defaults since they are tuning, not identity.
"""

from functools import lru_cache
from typing import Literal

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


class AllocatorSettings(BaseSettings):
    """Stock reallocation settings, sourced from `.env` / environment variables.

    Kept separate from ``Settings`` and fully defaulted, so the allocator runs
    without a `.env` file and without AWS configuration. Defaults match the
    Bay County demo scenario.
    """

    # Default planner when a request doesn't name one.
    allocator_method: Literal["optimal", "greedy", "compare"] = "optimal"

    # Planning window: trucks repeat round trips until this many hours are used.
    planning_horizon_hours: float = Field(6.0, gt=0)
    avg_speed_kmh: float = Field(35.0, gt=0)
    handling_hours_per_trip: float = Field(0.5, ge=0)

    # Solver budgets. The whole-trip step returns its best plan when the
    # rounding limit is hit; past the overall limit the planner falls back
    # to greedy.
    optimizer_time_limit_s: float = Field(10.0, gt=0)
    optimizer_rounding_time_s: float = Field(1.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_allocator_settings() -> AllocatorSettings:
    """Return a cached AllocatorSettings instance."""
    return AllocatorSettings()


class DatabaseSettings(BaseSettings):
    """Which database this process uses.

    ``sqlite`` is the default. ``dynamodb`` is all-or-nothing for the process:
    a missing credential or table is an error, and requests are not written to
    SQLite instead. Fully defaulted so tests and the local demo need no AWS
    configuration.
    """

    database_backend: Literal["sqlite", "dynamodb"] = "sqlite"
    dynamodb_table: str = "crisisops"
    dynamodb_scenario: str = "bay-county"
    # Empty means boto3's own region resolution (env, then AWS CLI config).
    aws_region: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return a cached DatabaseSettings instance."""
    return DatabaseSettings()
