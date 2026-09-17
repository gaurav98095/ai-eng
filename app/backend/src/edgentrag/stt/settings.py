"""Settings for the hosted speech-to-text API."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from edgentrag.core.config import MappedYamlConfigSettingsSource


class STTSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGENTRAG_STT_", extra="ignore")

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
                    "stt_model_name": "model_name",
                    "stt_device": "device",
                    "stt_compute_type": "compute_type",
                    "stt_max_upload_bytes": "max_upload_bytes",
                },
            ),
            file_secret_settings,
        )

    model_name: str = "Systran/faster-whisper-small.en"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    api_token: SecretStr = SecretStr("")
    max_upload_bytes: int = Field(default=512 * 1024 * 1024, gt=0)


@lru_cache
def load_settings() -> STTSettings:
    return STTSettings()


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def resolve_compute_type(requested: str, device: str) -> str:
    if requested != "auto":
        return requested
    return "float16" if device == "cuda" else "int8"
