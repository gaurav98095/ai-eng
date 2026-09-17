"""Upload-specific domain rules."""

from pathlib import PurePath

from edgentrag.ingestion.extraction import SUPPORTED_EXTRACTION_TYPES

SUPPORTED_AUDIO_TYPES = {
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}

SUPPORTED_VIDEO_TYPES = {
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

SUPPORTED_UPLOAD_TYPES = (
    SUPPORTED_EXTRACTION_TYPES | SUPPORTED_AUDIO_TYPES | SUPPORTED_VIDEO_TYPES
)


def is_supported_filename(filename: str) -> bool:
    """Return whether the file has a text or audio processing path."""
    return PurePath(filename).suffix.lower() in SUPPORTED_UPLOAD_TYPES


def expected_content_type(filename: str) -> str | None:
    return SUPPORTED_UPLOAD_TYPES.get(PurePath(filename).suffix.lower())


def upload_kind(filename: str) -> str:
    """Classify a supported upload for the correct queue worker."""
    extension = PurePath(filename).suffix.lower()
    return (
        "audio"
        if extension in SUPPORTED_AUDIO_TYPES | SUPPORTED_VIDEO_TYPES
        else "document"
    )
