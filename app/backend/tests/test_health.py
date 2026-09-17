"""Tests for the API liveness contract."""

from fastapi.testclient import TestClient

from edgentrag.api.app import create_app
from edgentrag.api.dependencies import get_events
from edgentrag.core.config import Settings


class FakeEvents:
    def __init__(self, available: bool) -> None:
        self.available = available

    def ping(self) -> bool:
        return self.available


def test_health_check_reports_a_healthy_api_process() -> None:
    """The route returns the public response contract, not framework internals."""
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_check_reports_loaded_configuration(tmp_path) -> None:
    """Readiness verifies a real test database, not the developer's database."""
    database_path = tmp_path / "edgentrag-test.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
    )

    app = create_app(settings=settings)
    app.dependency_overrides[get_events] = lambda: FakeEvents(True)
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "environment": "test",
        "checks": {"configuration": "ok", "database": "ok", "redis": "ok"},
    }


def test_readiness_check_returns_503_when_database_is_unavailable(tmp_path) -> None:
    """A live API can correctly report that it is not ready for traffic."""
    database_path = tmp_path / "missing-directory" / "database.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
    )

    app = create_app(settings=settings)
    app.dependency_overrides[get_events] = lambda: FakeEvents(True)
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "environment": "test",
        "checks": {"configuration": "ok", "database": "failed", "redis": "ok"},
    }
