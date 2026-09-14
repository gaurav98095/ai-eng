"""Typed configuration loaded from the environment."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
    aws_region: str = "ap-south-1"
    aws_endpoint_url: str | None = None
    s3_bucket: str = ""
    ingestion_queue_url: str = ""
    upload_url_ttl_seconds: int = Field(default=900, gt=0, le=604800)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_text_extract_bytes: int = Field(default=20 * 1024 * 1024, gt=0)


@lru_cache
def load_settings() -> Settings:
    """Build settings once per process.

    Parsing environment variables repeatedly is unnecessary, and caching makes
    each application start use the same immutable configuration snapshot.
    """
    return Settings()
