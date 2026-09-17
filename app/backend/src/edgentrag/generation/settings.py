"""Configuration for the model-serving process, independent of AWS settings."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from edgentrag.core.config import MappedYamlConfigSettingsSource


class GenerationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGENTRAG_GENERATION_", extra="ignore"
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
                    "generation_model_name": "model_name",
                    "generation_device": "device",
                    "generation_max_input_tokens": "max_input_tokens",
                    "generation_context_window": "context_window",
                },
            ),
            file_secret_settings,
        )

    api_token: SecretStr = SecretStr("")
    model_name: str = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    max_input_tokens: int = Field(default=1536, ge=1, le=32768)
    context_window: int = Field(default=2048, ge=2, le=32768)
