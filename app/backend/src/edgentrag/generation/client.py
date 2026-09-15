"""Async HTTP client for the separately hosted generation service."""

from typing import Protocol

import httpx

from edgentrag.generation.schemas import GenerateRequest, GenerateResponse


class GenerationProvider(Protocol):
    """The answer flow only depends on this small inference interface."""

    async def generate(self, request: GenerateRequest) -> GenerateResponse: ...


class GenerationServiceUnavailable(Exception):
    """Raised when the remote generation endpoint cannot serve a valid answer."""


class GenerationInputTooLarge(Exception):
    """Raised when the remote model's tokenizer rejects the assembled prompt."""


class HttpGenerationClient:
    """Call a bearer-authenticated generation API over HTTP(S)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 300,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Return validated generated text without leaking remote error bodies."""
        try:
            response = await self._client.post("/generate", json=request.model_dump())
            if response.status_code == 413:
                raise GenerationInputTooLarge(
                    "the generated prompt exceeds the model service input limit"
                )
            response.raise_for_status()
            return GenerateResponse.model_validate(response.json())
        except GenerationInputTooLarge:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GenerationServiceUnavailable(
                "generation service request failed"
            ) from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
