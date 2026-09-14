"""Public request and response contracts for semantic search."""

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    """A bounded question and number of source chunks to return."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)


class SearchMatch(BaseModel):
    """A source chunk and its cosine similarity to the query."""

    chunk_id: str
    file_id: str
    filename: str
    chunk_index: int
    content: str
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class SearchResponse(BaseModel):
    """Ranked evidence, not an LLM-generated answer."""

    session_id: str
    query: str
    model: str
    searched_chunks: int
    skipped_chunks: int
    matches: list[SearchMatch]
