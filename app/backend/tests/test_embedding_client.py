"""Tests for the worker's HTTP boundary to the Colab embedding service."""

import asyncio

import httpx
import pytest

from edgentrag.embedding.client import (
    EmbeddingServiceUnavailable,
    HttpEmbeddingClient,
)


def test_embedding_client_validates_and_returns_vectors() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embed"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.read() == b'{"texts":["one chunk"]}'
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "dimensions": 2,
                "embeddings": [[0.6, 0.8]],
            },
        )

    client = HttpEmbeddingClient(
        base_url="https://embedding.example.test",
        api_token="test-token",
        transport=httpx.MockTransport(respond),
    )
    try:
        result = asyncio.run(client.embed(["one chunk"]))
        assert result.model == "test-model"
        assert result.dimensions == 2
        assert result.embeddings == [[0.6, 0.8]]
    finally:
        asyncio.run(client.aclose())


def test_embedding_client_turns_service_errors_into_retryable_failures() -> None:
    client = HttpEmbeddingClient(
        base_url="https://embedding.example.test",
        api_token="test-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, json={"detail": "unavailable"})
        ),
    )
    try:
        with pytest.raises(EmbeddingServiceUnavailable, match="request failed"):
            asyncio.run(client.embed(["one chunk"]))
    finally:
        asyncio.run(client.aclose())


def test_embedding_client_rejects_wrong_vector_dimensions() -> None:
    client = HttpEmbeddingClient(
        base_url="https://embedding.example.test",
        api_token="test-token",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "model": "test-model",
                    "dimensions": 3,
                    "embeddings": [[0.1, 0.2]],
                },
            )
        ),
    )
    try:
        with pytest.raises(EmbeddingServiceUnavailable, match="dimensions"):
            asyncio.run(client.embed(["one chunk"]))
    finally:
        asyncio.run(client.aclose())
