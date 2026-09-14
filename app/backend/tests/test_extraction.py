"""Tests for text validation and chunk-boundary behavior."""

import pytest

from edgentrag.ingestion.extraction import (
    DocumentExtractionError,
    chunk_text,
    extract_text,
)


def test_markdown_is_decoded_and_split_with_overlap() -> None:
    text = "alpha beta gamma delta"

    assert (
        extract_text(
            filename="notes.md",
            content_type="text/markdown",
            content=b"\xef\xbb\xbfalpha beta gamma delta\r\n",
        )
        == text
    )
    assert chunk_text(text, chunk_size=12, overlap=5) == [
        "alpha beta",
        "beta gamma",
        "gamma delta",
    ]


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("notes.pdf", "application/pdf", b"%PDF-1.7"),
        ("notes.md", "text/markdown", b"\xff\xfe"),
        ("notes.md", "text/markdown", b"text\x00binary"),
        ("notes.txt", "application/octet-stream", b"plain text"),
        ("notes.md", "text/markdown", b"   \n"),
    ],
)
def test_extractor_rejects_unsupported_or_invalid_documents(
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    with pytest.raises(DocumentExtractionError):
        extract_text(filename=filename, content_type=content_type, content=content)


def test_chunker_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap smaller than size"):
        chunk_text("text", chunk_size=10, overlap=10)
