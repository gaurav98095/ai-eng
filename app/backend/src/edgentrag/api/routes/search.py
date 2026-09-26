"""HTTP boundary for session-scoped semantic search."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.api.dependencies import (
    get_database,
    get_db_session,
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
from edgentrag.sessions.models import ChatSession
from edgentrag.shared.auth import current_user, owns

router = APIRouter(prefix="/sessions", tags=["search"])


@router.post("/{session_id}/search", response_model=SearchResponse)
async def search(
    session_id: str,
    body: SearchRequest,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    database: Annotated[Database, Depends(get_database)],
    provider: Annotated[EmbeddingProvider | None, Depends(get_embedding_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_id: Annotated[str, Depends(current_user)],
) -> SearchResponse:
    """Return source chunks ordered by similarity to the query."""
    session = await database_session.get(ChatSession, session_id)
    if session is None or not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")
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
