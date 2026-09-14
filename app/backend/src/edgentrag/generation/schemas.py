"""Small, bounded request and response contracts for model inference."""

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    instructions: str = Field(
        default="Answer the user's question clearly and concisely.",
        min_length=1,
        max_length=2000,
    )
    prompt: str = Field(min_length=1, max_length=16000)
    max_new_tokens: int = Field(default=256, ge=1, le=512, strict=True)


class GenerateResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
