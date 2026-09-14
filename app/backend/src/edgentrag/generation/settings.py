"""Configuration for the model-serving process, independent of AWS settings."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class GenerationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGENTRAG_GENERATION_", extra="ignore"
    )

    api_token: SecretStr = SecretStr("")
    model_name: str = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    max_input_tokens: int = Field(default=1536, ge=1, le=32768)
    context_window: int = Field(default=2048, ge=2, le=32768)
