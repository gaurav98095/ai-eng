"""Injectable transport adapters for hosted LLM inference."""

from typing import Protocol

import httpx

from edgentrag.core.telemetry import capture_content, set_span_attributes, span
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
        attributes = {
            "openinference.span.kind": "LLM",
            "llm.input_messages": request.instructions,
            "llm.request.max_tokens": request.max_new_tokens,
        }
        if not capture_content():
            attributes.pop("llm.input_messages")
            attributes["llm.input_chars"] = len(request.instructions) + len(
                request.prompt
            )
        with span("llm.generate", **attributes) as current:
            try:
                response = await self._client.post(
                    "/generate", json=request.model_dump()
                )
                if response.status_code == 413:
                    raise LLMInputTooLarge(
                        "the generated prompt exceeds the model service input limit"
                    )
                response.raise_for_status()
                result = LLMResponse.model_validate(response.json())
                set_span_attributes(
                    current,
                    **{
                        "llm.model_name": result.model,
                        "llm.output_chars": len(result.content),
                        "llm.token_count.prompt": result.input_tokens,
                        "llm.token_count.completion": result.output_tokens,
                        "llm.output_value": (
                            result.content if capture_content() else None
                        ),
                    },
                )
                return result
            except LLMInputTooLarge:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise LLMServiceUnavailable("LLM service request failed") from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
