"""HTTP boundary for grounded answers with their retrieved source chunks."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.api.dependencies import (
    get_database,
    get_db_session,
    get_embedding_provider,
    get_llm_provider,
    get_settings,
)
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider, EmbeddingServiceUnavailable
from edgentrag.llm.client import LLMInputTooLarge, LLMProvider, LLMServiceUnavailable
from edgentrag.llm.prompts.grounded_answer import PromptContextTooLarge
from edgentrag.retrieval.answer import answer_session
from edgentrag.retrieval.answer_schemas import AnswerRequest, AnswerResponse
from edgentrag.retrieval.service import (
    SearchLimitExceeded,
    SearchNotReady,
    SessionNotFound,
)
from edgentrag.sessions.models import ChatSession
from edgentrag.shared.auth import current_user, owns

router = APIRouter(prefix="/sessions", tags=["answers"])


@router.post("/{session_id}/answers", response_model=AnswerResponse)
async def answer(
    session_id: str,
    body: AnswerRequest,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    database: Annotated[Database, Depends(get_database)],
    embedding_provider: Annotated[
        EmbeddingProvider | None, Depends(get_embedding_provider)
    ],
    llm_provider: Annotated[LLMProvider | None, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_id: Annotated[str, Depends(current_user)],
) -> AnswerResponse:
    """Retrieve session evidence, generate a response, and return the sources."""
    session = await database_session.get(ChatSession, session_id)
    if session is None or not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return await answer_session(
            database,
            session_id=session_id,
            query=body.query,
            top_k=body.top_k,
            max_new_tokens=body.max_new_tokens,
            embedding_provider=embedding_provider,
            llm_provider=llm_provider,
            max_chunks=settings.search_max_chunks,
            max_prompt_chars=settings.generation_prompt_max_chars,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SearchNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except PromptContextTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except LLMInputTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (EmbeddingServiceUnavailable, LLMServiceUnavailable) as exc:
        raise HTTPException(
            status_code=503,
            detail="embedding or generation service is unavailable; check API settings",
        ) from exc
