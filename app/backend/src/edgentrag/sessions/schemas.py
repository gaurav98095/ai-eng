"""HTTP request and response schemas for sessions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SessionResponse(BaseModel):
    """Public representation returned when a session is created."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    status: Literal["created"]
    created_at: datetime
