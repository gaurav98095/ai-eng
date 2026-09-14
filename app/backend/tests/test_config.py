"""Tests for typed environment configuration."""

import os

import pytest
from pydantic import ValidationError

from edgentrag.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    """Configuration tests must not depend on the developer's real settings."""
    for name in tuple(os.environ):
        if name.startswith("EDGENTRAG_"):
            monkeypatch.delenv(name)


def test_settings_have_safe_local_defaults() -> None:
    """A new developer can run the API without an environment file."""
    settings = Settings(_env_file=None)

    assert settings.environment == "local"
    assert settings.log_level == "INFO"
    assert settings.use_colab_for_embedding is True
    assert settings.use_colab_for_llm is True


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
