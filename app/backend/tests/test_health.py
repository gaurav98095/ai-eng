"""Tests for the API liveness contract."""

from fastapi.testclient import TestClient

from edgentrag.api.app import create_app
from edgentrag.core.config import Settings, get_settings


def test_health_check_reports_a_healthy_api_process() -> None:
    """The route returns the public response contract, not framework internals."""
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_check_reports_loaded_configuration() -> None:
    """Readiness uses injected settings instead of the developer's environment."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(environment="test")
    client = TestClient(app)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "environment": "test",
        "checks": {"configuration": "ok"},
    }
