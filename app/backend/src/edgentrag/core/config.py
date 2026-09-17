"""Typed configuration loaded from YAML, then environment overrides."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_CONFIG_FILE = Path(__file__).resolve().parents[3] / "config.yml"


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Load committed, non-sensitive defaults from a YAML mapping."""

    def __init__(self, settings_cls: type[BaseSettings], config_file: Path) -> None:
        super().__init__(settings_cls)
        self.config_file = config_file

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[None, str, bool]:
        """YAML is read as one mapping in ``__call__``."""
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if not self.config_file.is_file():
            return {}
        with self.config_file.open(encoding="utf-8") as config_file:
            values = yaml.safe_load(config_file) or {}
        if not isinstance(values, dict):
            raise ValueError(
                f"configuration file must be a mapping: {self.config_file}"
            )
        unknown = set(values) - set(self.settings_cls.model_fields)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(
                f"unknown configuration keys in {self.config_file}: {names}"
            )
        return values


class Settings(BaseSettings):
    """Configuration shared by the API and future worker processes.

    ``config.yml`` provides committed, non-sensitive defaults. Environment
    variables and the local ``.env`` file use the EDGENTRAG_ prefix and take
    precedence, so they are reserved for secrets and deployment overrides.
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        env_prefix="EDGENTRAG_",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use explicit values and environment settings ahead of YAML defaults."""
        config_file = Path(
            env_settings.env_vars.get("edgentrag_config_file", DEFAULT_CONFIG_FILE)
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls, config_file),
            file_secret_settings,
        )

    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Local Compose and production both use PostgreSQL; the database boundary
    # normalizes driver details while tests may override this with SQLite.
    database_url: str = "postgresql+asyncpg://edgentrag:edgentrag@localhost:5432/edgentrag"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    redis_url: str = "redis://localhost:6379/0"
    redis_tls: bool = False
    history_turns: int = Field(default=8, ge=1, le=50)
    aws_region: str = "ap-south-1"
    aws_endpoint_url: str | None = None
    s3_bucket: str = ""
    ingestion_queue_url: str = ""
    chat_queue_url: str = ""
    stt_queue_url: str = ""
    embedding_queue_url: str = ""
    queue_wait_seconds: int = Field(default=20, ge=0, le=20)
    queue_visibility_timeout_seconds: int = Field(default=900, gt=0, le=43200)
    queue_batch_size: int = Field(default=10, ge=1, le=10)
    queue_max_receives: int = Field(default=5, ge=1, le=100)
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    sse_ticket_seconds: int = Field(default=60, gt=0, le=600)
    upload_url_ttl_seconds: int = Field(default=900, gt=0, le=604800)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_text_extract_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    use_colab_for_embedding: bool = True
    use_colab_for_llm: bool = True
    colab_embedding_service_url: AnyHttpUrl | None = None
    colab_generation_service_url: AnyHttpUrl | None = None
    lightning_embedding_service_url: AnyHttpUrl | None = None
    lightning_generation_service_url: AnyHttpUrl | None = None
    generation_service_url: AnyHttpUrl | None = None
    generation_api_token: SecretStr = SecretStr("")
    generation_request_timeout_seconds: float = Field(default=300, gt=0, le=600)
    embedding_service_url: AnyHttpUrl | None = None
    embedding_api_token: SecretStr = SecretStr("")
    embedding_batch_size: int = Field(default=32, gt=0, le=64)
    embedding_request_timeout_seconds: float = Field(default=180, gt=0, le=600)
    search_max_chunks: int = Field(default=5000, ge=1, le=50000)

    @model_validator(mode="after")
    def validate_model_configuration(self) -> "Settings":
        """Resolve selected hosts and require their URL/token pairs."""
        if self.environment == "production" and self.database_url.startswith("sqlite"):
            raise ValueError(
                "production requires a PostgreSQL database_url"
            )
        if self.environment == "production" and self.aws_endpoint_url:
            raise ValueError(
                "aws_endpoint_url is only valid for local Floci development"
            )
        if self.environment == "production":
            required_queues = {
                "ingestion_queue_url": self.ingestion_queue_url,
                "chat_queue_url": self.chat_queue_url,
            }
            missing_queues = [
                name for name, value in required_queues.items() if not value
            ]
            if missing_queues:
                raise ValueError(
                    "production requires queue URLs: " + ", ".join(missing_queues)
                )
        if self.environment == "local" and not self.aws_endpoint_url:
            # Compose overrides the endpoint to the internal `floci` service;
            # host-run commands may continue using localhost:4566.
            self.aws_endpoint_url = "http://localhost:4566"
        if self.use_colab_for_embedding:
            # Preserve pre-profile Colab configuration for existing installations.
            self.embedding_service_url = (
                self.colab_embedding_service_url or self.embedding_service_url
            )
        else:
            if self.lightning_embedding_service_url is None:
                raise ValueError(
                    "lightning_embedding_service_url is required when "
                    "use_colab_for_embedding is false"
                )
            self.embedding_service_url = self.lightning_embedding_service_url
        self.generation_service_url = (
            (self.colab_generation_service_url or self.generation_service_url)
            if self.use_colab_for_llm
            else self.lightning_generation_service_url
        )
        has_url = self.embedding_service_url is not None
        has_token = bool(self.embedding_api_token.get_secret_value())
        if has_url != has_token:
            raise ValueError(
                "embedding_service_url and embedding_api_token "
                "must be configured together"
            )
        if (
            self.environment == "production"
            and has_url
            and not self.embedding_queue_url
        ):
            raise ValueError(
                "production requires embedding_queue_url when embedding is configured"
            )
        has_generation_url = self.generation_service_url is not None
        has_generation_token = bool(self.generation_api_token.get_secret_value())
        if has_generation_url != has_generation_token:
            raise ValueError(
                "generation_service_url and generation_api_token "
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
