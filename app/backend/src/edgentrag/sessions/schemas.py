"""HTTP request and response schemas for sessions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SessionResponse(BaseModel):
    """Public representation returned when a session is created."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    status: Literal["created", "processing", "ready", "failed"]
    created_at: datetime


class SessionFileResponse(BaseModel):
    """Public metadata and lifecycle state for one uploaded file."""

    model_config = ConfigDict(from_attributes=True)

    file_id: str
    filename: str
    content_type: str
    kind: Literal["document", "audio"]
    size_bytes: int
    status: Literal[
        "awaiting_upload", "uploaded", "processing", "ready", "failed"
    ]


class SessionDetailResponse(SessionResponse):
    """Session state together with its upload records."""

    files: list[SessionFileResponse]
