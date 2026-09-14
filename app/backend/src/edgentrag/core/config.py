"""Typed configuration loaded from the environment."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
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
    embedding_service_url: AnyHttpUrl | None = None
    embedding_api_token: SecretStr = SecretStr("")
    embedding_batch_size: int = Field(default=32, gt=0, le=64)
    embedding_request_timeout_seconds: float = Field(default=180, gt=0, le=600)
    search_max_chunks: int = Field(default=5000, ge=1, le=50000)

    @model_validator(mode="after")
    def validate_embedding_configuration(self) -> "Settings":
        """Require a URL and bearer token together when embeddings are enabled."""
        has_url = self.embedding_service_url is not None
        has_token = bool(self.embedding_api_token.get_secret_value())
        if has_url != has_token:
            raise ValueError(
                "embedding_service_url and embedding_api_token "
                "must be configured together"
            )
        return self


@lru_cache
def load_settings() -> Settings:
    """Build settings once per process.

    Parsing environment variables repeatedly is unnecessary, and caching makes
    each application start use the same immutable configuration snapshot.
    """
    return Settings()
