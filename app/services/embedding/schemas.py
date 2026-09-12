"""The contract with the monolith.

CHANGED FOR COLAB. The services no longer share a filesystem with the monolith,
so /embed can no longer be handed *locations* and go and read them. The monolith
reads its own chunk files and sends the records themselves.

/retrieve is unchanged: it only ever touched the vector database.
"""
from pydantic import BaseModel


class EmbedChunk(BaseModel):
    """One chunk record, as written in the monolith's .jsonl files."""

    chunk_id: str
    text: str
    source: str = ""
    section: str = ""
    chunks_key: str = ""     # provenance only; the service never opens it


class EmbedRequest(BaseModel):
    session_id: str
    chunks: list[EmbedChunk]


class EmbedResponse(BaseModel):
    session_id: str
    collection: str
    indexed: int


class RetrieveRequest(BaseModel):
    session_id: str
    query: str
    top_k: int = 4


class Hit(BaseModel):
    chunk_id: str
    chunks_key: str      # where to find the chunk's text -- the monolith fetches it
    score: float
    source: str = ""
    section: str = ""


class RetrieveResponse(BaseModel):
    hits: list[Hit]
