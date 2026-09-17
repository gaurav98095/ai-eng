"""Stable request and response contracts for a text-generation provider.

These Pydantic models are shared by the application-side LLM client and the
separately deployed generation service. Their JSON shape is the ``/generate``
wire contract; changing it requires coordinating both deployments.
"""

from pydantic import BaseModel, ConfigDict, Field


class LLMRequest(BaseModel):
    """One instruction-and-prompt completion request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    instructions: str = Field(
        default="Answer the user's question clearly and concisely.",
        min_length=1,
        max_length=2000,
    )
    prompt: str = Field(min_length=1, max_length=16000)
    max_new_tokens: int = Field(default=256, ge=1, le=512, strict=True)


class LLMResponse(BaseModel):
    """Validated completion metadata returned by a text-generation provider."""

    model_config = ConfigDict(str_strip_whitespace=True)

    model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
