"""Async HTTP client for the separately hosted speech-to-text service."""

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class Transcript:
    """A durable transcription result returned by a model provider."""

    model: str
    text: str


class STTProvider(Protocol):
    """The queue worker depends on this narrow inference boundary."""

    async def transcribe(
        self,
        *,
        session_id: str,
        file_id: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> Transcript: ...

    async def aclose(self) -> None: ...


class STTServiceUnavailable(Exception):
    """Raised when the remote transcription service cannot serve a request."""


class STTTranscriptionRejected(Exception):
    """Raised when the provider permanently rejects the submitted media."""


class HttpSTTClient:
    """Call the bearer-authenticated ``/transcribe`` service over HTTP(S)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def transcribe(
        self,
        *,
        session_id: str,
        file_id: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> Transcript:
        """Submit one bounded audio object and return its joined transcript."""
        try:
            response = await self._client.post(
                "/transcribe",
                data={"session_id": session_id, "file_id": file_id},
                files={"file": (filename, content, content_type)},
            )
            if response.status_code in {400, 413, 415, 422}:
                raise STTTranscriptionRejected("speech-to-text rejected the audio")
            response.raise_for_status()
            payload = response.json()
            rows = payload["segments"]
            text = "\n".join(str(row["text"]).strip() for row in rows).strip()
            model = str(payload["model"])
            if not text or not model:
                raise ValueError("speech-to-text response was incomplete")
            return Transcript(model=model, text=text)
        except STTTranscriptionRejected:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise STTServiceUnavailable(
                "speech-to-text service request failed"
            ) from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
