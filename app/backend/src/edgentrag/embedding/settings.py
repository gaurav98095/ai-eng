"""Environment settings for the separately deployed Colab embedding API."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from edgentrag.core.config import MappedYamlConfigSettingsSource


class EmbeddingSettings(BaseSettings):
    """Configuration for model choice, accelerator, and service authentication."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        env_prefix="EDGENTRAG_EMBEDDING_",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            MappedYamlConfigSettingsSource(
                settings_cls,
                {
                    "embedding_model_name": "model_name",
                    "embedding_device": "device",
                },
            ),
            file_secret_settings,
        )

    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    api_token: SecretStr = SecretStr("")
    max_batch_size: int = Field(default=64, gt=0, le=64)
    max_text_characters: int = Field(default=10000, gt=0)
