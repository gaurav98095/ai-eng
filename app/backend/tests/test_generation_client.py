"""Tests for the backend's authenticated generation-service client."""

import asyncio

import httpx
import pytest

from edgentrag.generation.client import (
    GenerationInputTooLarge,
    GenerationServiceUnavailable,
    HttpGenerationClient,
)
from edgentrag.generation.schemas import GenerateRequest


def test_generation_client_posts_contract_and_validates_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/generate"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "model": "small-model",
                "content": "Grounded response.",
                "input_tokens": 12,
                "output_tokens": 4,
            },
        )

    client = HttpGenerationClient(
        base_url="https://model.example.test/",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        response = asyncio.run(
            client.generate(
                GenerateRequest(prompt="Question and sources", max_new_tokens=32)
            )
        )
        assert response.model == "small-model"
        assert response.content == "Grounded response."
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="private tunnel routing details"),
        httpx.Response(413, text="model tokenizer private details"),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, text="not json"),
    ],
)
def test_generation_client_sanitizes_http_and_payload_failures(response):
    client = HttpGenerationClient(
        base_url="https://model.example.test",
        api_token="secret",
        transport=httpx.MockTransport(lambda _: response),
    )
    try:
        expected_error = (
            GenerationInputTooLarge
            if response.status_code == 413
            else GenerationServiceUnavailable
        )
        with pytest.raises(expected_error) as error:
            asyncio.run(client.generate(GenerateRequest(prompt="question")))
        assert "private tunnel" not in str(error.value)
    finally:
        asyncio.run(client.aclose())
