"""Async HTTP client used by the ingestion worker to call Colab."""

from typing import Protocol

import httpx

from edgentrag.embedding.schemas import EmbeddingBatch as EmbeddingBatch
from edgentrag.embedding.schemas import validate_embedding_batch


class EmbeddingProvider(Protocol):
    """Async interface used by the worker and fake test providers."""

    async def embed(self, texts: list[str]) -> EmbeddingBatch: ...

    async def embed_query(self, text: str) -> EmbeddingBatch: ...


class EmbeddingServiceUnavailable(Exception):
    """Raised when Colab is unreachable or returns an invalid response."""


class HttpEmbeddingClient:
    """Call the bearer-authenticated embedding API over HTTP(S)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 180,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        """Return one validated finite vector per supplied text."""
        try:
            response = await self._client.post("/embed", json={"texts": texts})
            response.raise_for_status()
            batch = EmbeddingBatch.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingServiceUnavailable(
                "embedding service request failed"
            ) from exc

        try:
            validate_embedding_batch(batch, len(texts))
        except ValueError as exc:
            raise EmbeddingServiceUnavailable(str(exc)) from exc
        return batch

    async def embed_query(self, text: str) -> EmbeddingBatch:
        """Use v3's dedicated query route when the service exposes it."""
        try:
            response = await self._client.post("/embed_query", json={"text": text})
            response.raise_for_status()
            batch = EmbeddingBatch.model_validate(response.json())
            validate_embedding_batch(batch, 1)
            return batch
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise EmbeddingServiceUnavailable("query embedding request failed") from exc
            return await self.embed([text])
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingServiceUnavailable("query embedding request failed") from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
