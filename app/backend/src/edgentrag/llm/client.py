"""Injectable transport adapters for hosted LLM inference."""

from typing import Protocol

import httpx

from edgentrag.llm.contracts import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    """Application-facing LLM seam, independent of the hosting provider."""

    async def generate(self, request: LLMRequest) -> LLMResponse: ...


class LLMServiceUnavailable(Exception):
    """Raised when the remote LLM endpoint cannot serve a valid answer."""


class LLMInputTooLarge(Exception):
    """Raised when the remote model rejects the assembled prompt budget."""


class HttpLLMClient:
    """Call the current bearer-authenticated LLM HTTP contract over HTTPS."""

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

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Return a validated response without exposing remote error details."""
        try:
            response = await self._client.post("/generate", json=request.model_dump())
            if response.status_code == 413:
                raise LLMInputTooLarge(
                    "the generated prompt exceeds the model service input limit"
                )
            response.raise_for_status()
            return LLMResponse.model_validate(response.json())
        except LLMInputTooLarge:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMServiceUnavailable("LLM service request failed") from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
