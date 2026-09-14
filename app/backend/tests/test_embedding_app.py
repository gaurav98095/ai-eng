"""Tests for the authenticated Colab embedding-service HTTP contract."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from edgentrag.embedding.app import create_app
from edgentrag.embedding.settings import EmbeddingSettings


class FakeEmbedder:
    """Stable vectors for API tests without loading model weights."""

    is_loaded = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.is_loaded = True
        return [[1.0, 0.0] if "cat" in text else [0.0, 1.0] for text in texts]


def create_test_client(
    *,
    token: str = "test-token",
    max_batch_size: int = 4,
    max_text_characters: int = 20,
) -> tuple[TestClient, FakeEmbedder]:
    embedder = FakeEmbedder()
    app = create_app(
        settings=EmbeddingSettings(
            api_token=SecretStr(token),
            max_batch_size=max_batch_size,
            max_text_characters=max_text_characters,
        ),
        embedder=embedder,
    )
    return TestClient(app), embedder


def test_health_does_not_load_model_and_embedding_returns_normalized_vectors() -> None:
    client, embedder = create_test_client()

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["loaded"] is False

    response = client.post(
        "/embed",
        headers={"Authorization": "Bearer test-token"},
        json={"texts": ["cat article", "dog article"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "dimensions": 2,
        "embeddings": [[1.0, 0.0], [0.0, 1.0]],
    }
    assert embedder.is_loaded is True


def test_embedding_requires_the_shared_bearer_token() -> None:
    client, embedder = create_test_client()

    response = client.post("/embed", json={"texts": ["a short text"]})

    assert response.status_code == 401
    assert embedder.is_loaded is False


def test_embedding_rejects_batches_and_texts_over_configured_limits() -> None:
    client, _ = create_test_client(max_batch_size=1, max_text_characters=8)
    headers = {"Authorization": "Bearer test-token"}

    too_many = client.post(
        "/embed",
        headers=headers,
        json={"texts": ["first", "second"]},
    )
    too_long = client.post(
        "/embed",
        headers=headers,
        json={"texts": ["this text is too long"]},
    )
    blank = client.post(
        "/embed",
        headers=headers,
        json={"texts": ["   "]},
    )

    assert too_many.status_code == 422
    assert too_long.status_code == 422
    assert blank.status_code == 422


def test_embedding_endpoint_is_disabled_until_token_is_configured() -> None:
    client, embedder = create_test_client(token="")

    response = client.post(
        "/embed",
        headers={"Authorization": "Bearer anything"},
        json={"texts": ["a short text"]},
    )

    assert response.status_code == 503
    assert embedder.is_loaded is False
