"""HTTP boundary for session-scoped semantic search."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from edgentrag.api.dependencies import (
    get_database,
    get_embedding_provider,
    get_settings,
)
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider, EmbeddingServiceUnavailable
from edgentrag.retrieval.schemas import SearchRequest, SearchResponse
from edgentrag.retrieval.service import (
    SearchLimitExceeded,
    SearchNotReady,
    SessionNotFound,
    search_session,
)

router = APIRouter(prefix="/sessions", tags=["search"])


@router.post("/{session_id}/search", response_model=SearchResponse)
async def search(
    session_id: str,
    body: SearchRequest,
    database: Annotated[Database, Depends(get_database)],
    provider: Annotated[EmbeddingProvider | None, Depends(get_embedding_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SearchResponse:
    """Return source chunks ordered by similarity to the query."""
    try:
        return await search_session(
            database,
            session_id=session_id,
            query=body.query,
            top_k=body.top_k,
            provider=provider,
            max_chunks=settings.search_max_chunks,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SearchNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EmbeddingServiceUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="embedding service is unavailable; check the API's URL and token",
        ) from exc
