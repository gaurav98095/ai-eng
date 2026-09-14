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


def test_embedding_service_url_and_token_must_be_configured_together() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, embedding_service_url="https://embedding.example.test")

    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(_env_file=None, embedding_api_token="test-token")
