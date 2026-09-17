"""Tests for typed environment configuration."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from edgentrag.core.config import Settings
from edgentrag.embedding.settings import EmbeddingSettings
from edgentrag.generation.settings import GenerationSettings
from edgentrag.stt.settings import STTSettings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    """Configuration tests must not depend on the developer's real settings."""
    for name in tuple(os.environ):
        if name.startswith("EDGENTRAG_"):
            monkeypatch.delenv(name)


def test_settings_have_safe_local_defaults() -> None:
    """A new developer can run the API with committed YAML defaults."""
    settings = Settings(_env_file=None)

    assert settings.environment == "local"
    assert settings.log_level == "INFO"
    assert settings.use_colab_for_embedding is True
    assert settings.use_colab_for_llm is True


def test_yaml_configuration_is_loaded_and_environment_takes_precedence(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text("history_turns: 12\nlog_level: WARNING\n")
    monkeypatch.setenv("EDGENTRAG_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("EDGENTRAG_LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.history_turns == 12
    assert settings.log_level == "DEBUG"


def test_dotenv_configuration_takes_precedence_over_yaml(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text("log_level: WARNING\n")
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("EDGENTRAG_LOG_LEVEL=ERROR\n")
    monkeypatch.setenv("EDGENTRAG_CONFIG_FILE", str(config_file))

    settings = Settings(_env_file=dotenv_file)

    assert settings.log_level == "ERROR"


def test_production_yaml_profile_has_safe_defaults(monkeypatch) -> None:
    config_file = Path(__file__).resolve().parents[1] / "config.production.yml"
    monkeypatch.setenv("EDGENTRAG_CONFIG_FILE", str(config_file))

    settings = Settings(
        _env_file=None,
        ingestion_queue_url="https://sqs.example.test/ingestion",
        chat_queue_url="https://sqs.example.test/chat",
    )

    assert settings.environment == "production"
    assert settings.redis_tls is True
    assert settings.aws_endpoint_url is None
    assert settings.db_pool_size == 20


def test_yaml_configuration_rejects_unknown_keys(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text("not_a_setting: value\n")
    monkeypatch.setenv("EDGENTRAG_CONFIG_FILE", str(config_file))

    with pytest.raises(ValueError, match="unknown configuration keys"):
        Settings(_env_file=None)


def test_yaml_model_selection_is_shared_with_hosted_services(
    monkeypatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "\n".join(
            [
                "embedding_model_name: sentence-transformers/all-mpnet-base-v2",
                "generation_model_name: Qwen/Qwen2.5-7B-Instruct",
                "generation_context_window: 8192",
                "stt_model_name: Systran/faster-whisper-large-v3",
            ]
        )
    )
    monkeypatch.setenv("EDGENTRAG_CONFIG_FILE", str(config_file))

    assert EmbeddingSettings(_env_file=None).model_name.endswith("mpnet-base-v2")
    assert GenerationSettings().model_name == "Qwen/Qwen2.5-7B-Instruct"
    assert GenerationSettings().context_window == 8192
    assert STTSettings().model_name.endswith("large-v3")


@pytest.mark.parametrize("embedding_colab", [True, False])
@pytest.mark.parametrize("llm_colab", [True, False])
def test_services_select_hosts_independently(embedding_colab, llm_colab):
    settings = Settings(
        _env_file=None,
        use_colab_for_embedding=embedding_colab,
        use_colab_for_llm=llm_colab,
        embedding_api_token="test-token",
        embedding_service_url="https://old.example.test",
        colab_embedding_service_url="https://colab.example.test",
        lightning_embedding_service_url="https://lightning.example.test",
        generation_api_token="generation-test-token",
        colab_generation_service_url="https://colab-generation.example.test",
        lightning_generation_service_url="https://lightning-generation.example.test",
    )
    host = "colab" if embedding_colab else "lightning"
    assert str(settings.embedding_service_url) == f"https://{host}.example.test/"
    host = "colab" if llm_colab else "lightning"
    assert str(settings.generation_service_url) == (
        f"https://{host}-generation.example.test/"
    )


def test_lightning_cannot_fall_back_to_direct_colab_url():
    with pytest.raises(
        ValidationError, match="lightning_embedding_service_url is required"
    ):
        Settings(
            _env_file=None,
            use_colab_for_embedding=False,
            embedding_service_url="https://old.example.test",
            embedding_api_token="test-token",
        )


@pytest.mark.parametrize("field", ["use_colab_for_embedding", "use_colab_for_llm"])
def test_switches_reject_invalid_booleans(field):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: "typo"})


def test_selected_embedding_host_requires_token():
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            _env_file=None,
            use_colab_for_embedding=False,
            lightning_embedding_service_url="https://lightning.example.test",
        )


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_switches_can_be_selected_from_environment(monkeypatch, value, expected):
    monkeypatch.setenv("EDGENTRAG_USE_COLAB_FOR_EMBEDDING", value)
    monkeypatch.setenv("EDGENTRAG_USE_COLAB_FOR_LLM", value)
    monkeypatch.setenv(
        "EDGENTRAG_LIGHTNING_EMBEDDING_SERVICE_URL", "https://lightning.example.test"
    )
    monkeypatch.setenv(
        "EDGENTRAG_COLAB_EMBEDDING_SERVICE_URL", "https://colab.example.test"
    )
    monkeypatch.setenv("EDGENTRAG_EMBEDDING_API_TOKEN", "test-token")
    settings = Settings(_env_file=None)
    assert settings.use_colab_for_embedding is expected
    assert settings.use_colab_for_llm is expected
    host = "colab" if expected else "lightning"
    assert str(settings.embedding_service_url) == f"https://{host}.example.test/"
    assert settings.generation_service_url is None


def test_embedding_service_url_and_token_must_be_configured_together() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, embedding_service_url="https://embedding.example.test")

    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, embedding_api_token="test-token")


def test_selected_generation_host_requires_url_and_token_together() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            _env_file=None,
            colab_generation_service_url="https://generation.example.test",
        )

    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, generation_api_token="test-token")
