"""Read a session's vectors and rank its chunks by cosine similarity."""

import asyncio
import math
from dataclasses import dataclass

from sqlalchemy import func, select

from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider, EmbeddingServiceUnavailable
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.retrieval.schemas import SearchMatch, SearchResponse
from edgentrag.sessions.models import ChatSession, SessionFile


class SessionNotFound(Exception):
    """The requested session does not exist."""


class SearchNotReady(Exception):
    """The session has no usable vectors for the query's model."""


class SearchLimitExceeded(Exception):
    """The session is too large for this bounded, in-process search."""


@dataclass(frozen=True)
class Candidate:
    """Detached source data, safe to rank after closing the database session."""

    chunk_id: str
    file_id: str
    filename: str
    chunk_index: int
    content: str
    model: str
    vector: object


def unit_vector(vector: object, dimensions: int) -> list[float] | None:
    """Reject malformed/zero vectors and normalize safely for cosine scoring."""
    if not isinstance(vector, list) or len(vector) != dimensions or not vector:
        return None
    try:
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            return None
        norm = math.hypot(*vector)
    except OverflowError:
        return None
    if not math.isfinite(norm) or norm == 0:
        return None
    return [value / norm for value in vector]


def rank_candidates(
    candidates: list[Candidate],
    *,
    query_vector: list[float],
    model: str,
    top_k: int,
) -> tuple[list[SearchMatch], int]:
    """Rank compatible vectors, with a stable chunk-ID tie breaker."""
    matches: list[SearchMatch] = []
    for candidate in candidates:
        if candidate.model != model:
            continue
        vector = unit_vector(candidate.vector, len(query_vector))
        if vector is None:
            continue
        score = math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))
        matches.append(
            SearchMatch(
                chunk_id=candidate.chunk_id,
                file_id=candidate.file_id,
                filename=candidate.filename,
                chunk_index=candidate.chunk_index,
                content=candidate.content,
                score=max(-1.0, min(1.0, score)),
            )
        )
    matches.sort(key=lambda match: (-match.score, match.chunk_id))
    return matches[:top_k], len(matches)


async def search_session(
    database: Database,
    *,
    session_id: str,
    query: str,
    top_k: int,
    provider: EmbeddingProvider | None,
    max_chunks: int,
) -> SearchResponse:
    """Search only ready files belonging to this session, without DB writes."""
    if provider is None:
        raise EmbeddingServiceUnavailable("embedding service is not configured")

    dialect = database._engine.dialect.name
    # Reject a missing, empty, or over-limit local session before spending a
    # remote embedding request. PostgreSQL still needs the query model for its
    # compatible-vector predicate, but can at least reject a missing session.
    async with database.sessions() as database_session:
        if await database_session.get(ChatSession, session_id) is None:
            raise SessionNotFound("session not found")
        if dialect != "postgresql":
            candidate_count = int(
                (
                    await database_session.scalar(
                        select(func.count())
                        .select_from(DocumentChunk)
                        .join(
                            SessionFile,
                            DocumentChunk.session_file_id == SessionFile.id,
                        )
                        .where(
                            SessionFile.session_id == session_id,
                            SessionFile.status == "ready",
                            DocumentChunk.embedding_model.is_not(None),
                        )
                    )
                )
                or 0
            )
            if candidate_count > max_chunks:
                raise SearchLimitExceeded(
                    "session exceeds the configured search chunk limit"
                )
            if candidate_count == 0:
                raise SearchNotReady(
                    "no embedded chunks are ready; upload with embeddings enabled "
                    "and wait for ingestion"
                )

    # Query embeddings are computed before opening the DB transaction so a slow
    # remote provider cannot hold a connection.  PostgreSQL can then execute
    # the ANN/vector operator in the database; SQLite retains the bounded
    # in-process reference implementation for local development.
    embed_query = getattr(provider, "embed_query", None)
    batch = await (embed_query(query) if embed_query else provider.embed([query]))
    if len(batch.embeddings) != 1:
        raise EmbeddingServiceUnavailable("embedding service returned an invalid query")
    query_vector = unit_vector(batch.embeddings[0], batch.dimensions)
    if query_vector is None:
        raise EmbeddingServiceUnavailable("embedding service returned an invalid query")

    if dialect == "postgresql" and hasattr(DocumentChunk.embedding, "cosine_distance"):
        async with database.sessions() as database_session:
            if await database_session.get(ChatSession, session_id) is None:
                raise SessionNotFound("session not found")
            compatible = (
                select(func.count())
                .select_from(DocumentChunk)
                .join(SessionFile, DocumentChunk.session_file_id == SessionFile.id)
                .where(
                    SessionFile.session_id == session_id,
                    SessionFile.status == "ready",
                    DocumentChunk.embedding_model == batch.model,
                    DocumentChunk.embedding.is_not(None),
                )
            )
            searched = int((await database_session.scalar(compatible)) or 0)
            if searched == 0:
                raise SearchNotReady(
                    "no compatible embeddings; re-upload documents using the "
                    "current model"
                )
            distance = DocumentChunk.embedding.cosine_distance(query_vector)
            rows = await database_session.execute(
                select(DocumentChunk, SessionFile.filename, distance.label("distance"))
                .join(SessionFile, DocumentChunk.session_file_id == SessionFile.id)
                .where(
                    SessionFile.session_id == session_id,
                    SessionFile.status == "ready",
                    DocumentChunk.embedding_model == batch.model,
                    DocumentChunk.embedding.is_not(None),
                )
                .order_by(distance, DocumentChunk.id)
                .limit(top_k)
            )
            matches = [
                SearchMatch(
                    chunk_id=chunk.id,
                    file_id=chunk.session_file_id,
                    filename=filename,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=max(-1.0, min(1.0, 1.0 - float(raw_distance))),
                )
                for chunk, filename, raw_distance in rows
            ]
        if not matches:
            raise SearchNotReady(
                "no compatible embeddings; re-upload documents using the current model"
            )
        return SearchResponse(
            session_id=session_id,
            query=query,
            model=batch.model,
            searched_chunks=searched,
            skipped_chunks=0,
            matches=matches,
        )

    async with database.sessions() as database_session:
        if await database_session.get(ChatSession, session_id) is None:
            raise SessionNotFound("session not found")
        rows = await database_session.execute(
            select(DocumentChunk, SessionFile.filename)
            .join(SessionFile, DocumentChunk.session_file_id == SessionFile.id)
            .where(
                SessionFile.session_id == session_id,
                SessionFile.status == "ready",
                DocumentChunk.embedding_model.is_not(None),
            )
            .order_by(DocumentChunk.id)
            .limit(max_chunks + 1)
        )
        candidates = [
            Candidate(
                chunk_id=chunk.id,
                file_id=chunk.session_file_id,
                filename=filename,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                model=chunk.embedding_model,
                vector=chunk.embedding,
            )
            for chunk, filename in rows
        ]

    # The DB connection is released before waiting for Colab.
    if len(candidates) > max_chunks:
        raise SearchLimitExceeded("session exceeds the configured search chunk limit")
    if not candidates:
        raise SearchNotReady(
            "no embedded chunks are ready; upload with embeddings enabled "
            "and wait for ingestion"
        )

    matches, searched = await asyncio.to_thread(
        rank_candidates,
        candidates,
        query_vector=query_vector,
        model=batch.model,
        top_k=top_k,
    )
    if not matches:
        raise SearchNotReady(
            "no compatible embeddings; re-upload documents using the current model"
        )
    return SearchResponse(
        session_id=session_id,
        query=query,
        model=batch.model,
        searched_chunks=searched,
        skipped_chunks=len(candidates) - searched,
        matches=matches,
    )
