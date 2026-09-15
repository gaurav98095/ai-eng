"""HTTP contract for accepting and reading chat messages."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class ChatAccepted(BaseModel):
    message_id: str


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str | None
    status: str
    sources: list[dict] | None = None
    created_at: datetime
