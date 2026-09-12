"""Tests for the API liveness contract."""

from fastapi.testclient import TestClient

from edgentrag.api.app import create_app


def test_health_check_reports_a_healthy_api_process() -> None:
    """The route returns the public response contract, not framework internals."""
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
