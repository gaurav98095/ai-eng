"""Typed configuration loaded from the environment."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration shared by the API and future worker processes.

    Environment variables use the EDGENTRAG_ prefix. For example,
    EDGENTRAG_LOG_LEVEL=DEBUG overrides the default log level.
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        env_prefix="EDGENTRAG_",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str = "sqlite+aiosqlite:///./edgentrag.db"


@lru_cache
def load_settings() -> Settings:
    """Build settings once per process.

    Parsing environment variables repeatedly is unnecessary, and caching makes
    each application start use the same immutable configuration snapshot.
    """
    return Settings()
