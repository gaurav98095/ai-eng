"""Upload-specific domain rules."""

from pathlib import PurePath

SUPPORTED_EXTENSIONS = frozenset({".txt", ".md"})


def is_supported_filename(filename: str) -> bool:
    """Return whether the file extension has an implemented text processor."""
    return PurePath(filename).suffix.lower() in SUPPORTED_EXTENSIONS
