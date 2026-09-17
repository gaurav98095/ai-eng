"""Small, dependency-free text extraction and chunking for plain text files."""

from pathlib import PurePath
from tempfile import NamedTemporaryFile

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
SUPPORTED_TEXT_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
}
SUPPORTED_DOCUMENT_TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
SUPPORTED_EXTRACTION_TYPES = SUPPORTED_TEXT_TYPES | SUPPORTED_DOCUMENT_TYPES


class DocumentExtractionError(Exception):
    """Raised when a stored object is not a supported text document."""


def extract_text(*, filename: str, content_type: str, content: bytes) -> str:
    """Decode supported plain-text files and reject mislabeled/binary content."""
    extension = PurePath(filename).suffix.lower()
    expected_content_type = SUPPORTED_EXTRACTION_TYPES.get(extension)
    if expected_content_type is None:
        raise DocumentExtractionError("no text extractor for this file extension")
    if content_type.lower() != expected_content_type:
        raise DocumentExtractionError("content type does not match the file extension")
    if extension in SUPPORTED_DOCUMENT_TYPES:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise DocumentExtractionError(
                "document extraction support is not installed"
            ) from exc
        try:
            with NamedTemporaryFile(suffix=extension) as source:
                source.write(content)
                source.flush()
                result = DocumentConverter().convert(source.name)
                text = result.document.export_to_markdown()
        except Exception as exc:
            raise DocumentExtractionError(
                "document could not be converted to text"
            ) from exc
        text = text.strip()
        if not text:
            raise DocumentExtractionError("document contains no extractable text")
        return text

    if b"\x00" in content:
        raise DocumentExtractionError("binary content is not a text document")

    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise DocumentExtractionError("text document must use UTF-8 encoding") from exc

    text = text.strip()
    if not text:
        raise DocumentExtractionError("text document contains no extractable text")
    return text


def chunk_text(
    text: str,
    *,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks without dropping the source text."""
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk size must be positive and overlap smaller than size")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start + overlap:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)

    return chunks
