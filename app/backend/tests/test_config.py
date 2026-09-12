"""Tests for typed environment configuration."""

from edgentrag.core.config import Settings


def test_settings_have_safe_local_defaults() -> None:
    """A new developer can run the API without an environment file."""
    settings = Settings()

    assert settings.environment == "local"
    assert settings.log_level == "INFO"
