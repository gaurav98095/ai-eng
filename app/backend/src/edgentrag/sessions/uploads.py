"""Upload-specific domain rules."""

from pathlib import PurePath

SUPPORTED_EXTENSIONS = frozenset(
    {".txt", ".md", ".pdf", ".docx", ".mp3", ".mp4", ".wav", ".m4a"}
)


def is_supported_filename(filename: str) -> bool:
    """Return whether the file extension has a processor in the planned app."""
    return PurePath(filename).suffix.lower() in SUPPORTED_EXTENSIONS
