"""Contracts for grounded, session-scoped answer requests and responses."""

from pydantic import BaseModel, ConfigDict, Field

from edgentrag.retrieval.schemas import SearchMatch


class AnswerRequest(BaseModel):
    """A bounded question plus retrieval and generation budgets."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)
    max_new_tokens: int = Field(default=256, ge=1, le=512, strict=True)


class AnswerSource(BaseModel):
    """A retrieved chunk actually included in the model's context."""

    citation: str
    match: SearchMatch


class AnswerResponse(BaseModel):
    """Generated answer and the evidence supplied to the generation model."""

    session_id: str
    query: str
    answer: str
    generation_model: str
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)
    sources: list[AnswerSource]

