"""Tests for typed environment configuration."""

import pytest
from pydantic import ValidationError

from edgentrag.core.config import Settings


def test_settings_have_safe_local_defaults() -> None:
    """A new developer can run the API without an environment file."""
    settings = Settings()

    assert settings.environment == "local"
    assert settings.log_level == "INFO"


def test_embedding_service_url_and_token_must_be_configured_together() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(embedding_service_url="https://embedding.example.test")

    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(embedding_api_token="test-token")
