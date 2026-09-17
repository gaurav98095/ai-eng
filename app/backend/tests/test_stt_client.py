"""Tests for the authenticated speech-to-text HTTP boundary."""

import asyncio

import httpx
import pytest

from edgentrag.stt.client import (
    HttpSTTClient,
    STTServiceUnavailable,
    STTTranscriptionRejected,
)


def test_stt_client_posts_audio_and_joins_segments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/transcribe"
        assert request.headers["authorization"] == "Bearer secret"
        assert b'name="session_id"' in request.content
        assert b'name="file"; filename="meeting.mp3"' in request.content
        return httpx.Response(
            200,
            json={
                "model": "whisper-test",
                "segments": [{"text": "First sentence."}, {"text": "Second."}],
            },
        )

    client = HttpSTTClient(
        base_url="https://stt.example.test",
        api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        transcript = asyncio.run(
            client.transcribe(
                session_id="session-1",
                file_id="file-1",
                filename="meeting.mp3",
                content_type="audio/mpeg",
                content=b"audio",
            )
        )
        assert transcript.model == "whisper-test"
        assert transcript.text == "First sentence.\nSecond."
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("status", "error"),
    [(422, STTTranscriptionRejected), (503, STTServiceUnavailable)],
)
def test_stt_client_sanitizes_remote_errors(status, error) -> None:
    client = HttpSTTClient(
        base_url="https://stt.example.test",
        api_token="secret",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="private details")
        ),
    )
    try:
        with pytest.raises(error) as raised:
            asyncio.run(
                client.transcribe(
                    session_id="session-1",
                    file_id="file-1",
                    filename="meeting.mp3",
                    content_type="audio/mpeg",
                    content=b"audio",
                )
            )
        assert "private details" not in str(raised.value)
    finally:
        asyncio.run(client.aclose())
