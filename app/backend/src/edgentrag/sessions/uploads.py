"""Upload-specific domain rules."""

from pathlib import PurePath

from edgentrag.ingestion.extraction import SUPPORTED_TEXT_TYPES


def is_supported_filename(filename: str) -> bool:
    """Return whether the file extension has an implemented text processor."""
    return PurePath(filename).suffix.lower() in SUPPORTED_TEXT_TYPES


def expected_content_type(filename: str) -> str | None:
    return SUPPORTED_TEXT_TYPES.get(PurePath(filename).suffix.lower())
