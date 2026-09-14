"""Async HTTP client used by the ingestion worker to call Colab."""

import math
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EmbeddingBatch(BaseModel):
    """Validated vectors returned by the embedding service for one batch."""

    model_config = ConfigDict(allow_inf_nan=False)

    model: str = Field(min_length=1, max_length=255)
    dimensions: int = Field(gt=0)
    embeddings: list[list[float]]


class EmbeddingProvider(Protocol):
    """Async interface used by the worker and fake test providers."""

    async def embed(self, texts: list[str]) -> EmbeddingBatch: ...


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
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise EmbeddingServiceUnavailable(
                "embedding service request failed"
            ) from exc

        if len(batch.embeddings) != len(texts):
            raise EmbeddingServiceUnavailable(
                "embedding service returned a different number of vectors"
            )
        if any(len(vector) != batch.dimensions for vector in batch.embeddings):
            raise EmbeddingServiceUnavailable(
                "embedding service returned inconsistent vector dimensions"
            )
        if any(
            not math.isfinite(value)
            for vector in batch.embeddings
            for value in vector
        ):
            raise EmbeddingServiceUnavailable(
                "embedding service returned non-finite vector values"
            )
        return batch

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
