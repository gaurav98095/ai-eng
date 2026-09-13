"""Validation and response schemas for upload-link requests."""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

Filename = Annotated[str, Field(min_length=1, max_length=255)]
ContentType = Annotated[str, Field(min_length=1, max_length=255)]
PositiveSize = Annotated[int, Field(gt=0)]


class UploadFileSpec(BaseModel):
    """Metadata the API needs to create a file record and signed URL."""

    filename: Filename
    content_type: ContentType = "application/octet-stream"
    size_bytes: PositiveSize

    @field_validator("filename")
    @classmethod
    def filename_must_be_a_name_not_a_path(cls, value: str) -> str:
        """Do not accept path separators or control characters from clients."""
        if "/" in value or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError("filename must be a plain file name")
        if value in {".", ".."}:
            raise ValueError("filename must be a plain file name")
        return value


class UploadRequest(BaseModel):
    """A bounded batch of files for one existing session."""

    files: Annotated[list[UploadFileSpec], Field(min_length=1, max_length=20)]


class UploadTarget(BaseModel):
    """One file record and the URL the browser should PUT bytes to."""

    file_id: str
    filename: str
    content_type: str
    upload_url: str
    expires_in: int


class UploadResponse(BaseModel):
    """All upload targets created for the request."""

    session_id: str
    targets: list[UploadTarget]
