"""Typed configuration loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration shared by the API and future worker processes.

    Environment variables use the EDGENTRAG_ prefix. For example,
    EDGENTRAG_LOG_LEVEL=DEBUG overrides the default log level.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="EDGENTRAG_",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Build settings once per process.

    Parsing environment variables repeatedly is unnecessary, and caching makes
    every dependency receive the same immutable configuration snapshot.
    Tests can override this FastAPI dependency without mutating global state.
    """
    return Settings()
