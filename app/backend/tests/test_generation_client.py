"""Tests for the backend's authenticated generation-service client."""

import asyncio
from contextlib import contextmanager

import httpx
import pytest

from edgentrag.llm.client import HttpLLMClient, LLMInputTooLarge, LLMServiceUnavailable
from edgentrag.llm.contracts import LLMRequest


def _telemetry_recorder(monkeypatch, *, include_content: bool):
    captured: dict[str, object] = {}

    @contextmanager
    def record_span(name, **attributes):
        captured["name"] = name
        captured.update(attributes)
        yield object()

    def record_attributes(_, **attributes):
        captured.update(attributes)

    monkeypatch.setattr("edgentrag.llm.client.capture_content", lambda: include_content)
    monkeypatch.setattr("edgentrag.llm.client.span", record_span)
    monkeypatch.setattr("edgentrag.llm.client.set_span_attributes", record_attributes)
    return captured


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

    client = HttpLLMClient(
        base_url="https://model.example.test/",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        response = asyncio.run(
            client.generate(
                LLMRequest(prompt="Question and sources", max_new_tokens=32)
            )
        )
        assert response.model == "small-model"
        assert response.content == "Grounded response."
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("include_content", [False, True])
def test_generation_client_emits_redacted_openinference_attributes(
    monkeypatch, include_content
):
    captured = _telemetry_recorder(monkeypatch, include_content=include_content)
    client = HttpLLMClient(
        base_url="https://model.example.test",
        api_token="secret",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "model": "small-model",
                    "content": "private answer",
                    "input_tokens": 12,
                    "output_tokens": 4,
                },
            )
        ),
    )
    request = LLMRequest(
        instructions="private instructions",
        prompt="private prompt",
        max_new_tokens=32,
    )
    try:
        asyncio.run(client.generate(request))
    finally:
        asyncio.run(client.aclose())

    assert captured["name"] == "llm.generate"
    assert captured["openinference.span.kind"] == "LLM"
    assert captured["llm.system"] == "transformers"
    assert captured["llm.token_count.prompt"] == 12
    assert captured["llm.token_count.completion"] == 4
    serialized = repr(captured)
    if include_content:
        assert captured["llm.input_messages.1.message.content"] == "private prompt"
        assert captured["llm.output_messages.0.message.content"] == "private answer"
        assert captured["input.mime_type"] == "application/json"
        assert captured["output.mime_type"] == "application/json"
    else:
        assert "private instructions" not in serialized
        assert "private prompt" not in serialized
        assert "private answer" not in serialized


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
    client = HttpLLMClient(
        base_url="https://model.example.test",
        api_token="secret",
        transport=httpx.MockTransport(lambda _: response),
    )
    try:
        expected_error = (
            LLMInputTooLarge if response.status_code == 413 else LLMServiceUnavailable
        )
        with pytest.raises(expected_error) as error:
            asyncio.run(client.generate(LLMRequest(prompt="question")))
        assert "private tunnel" not in str(error.value)
    finally:
        asyncio.run(client.aclose())
