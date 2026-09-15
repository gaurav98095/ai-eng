"""HTTP boundary for grounded answers with their retrieved source chunks."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from edgentrag.api.dependencies import (
    get_database,
    get_embedding_provider,
    get_generation_provider,
    get_settings,
)
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider, EmbeddingServiceUnavailable
from edgentrag.generation.client import (
    GenerationInputTooLarge,
    GenerationProvider,
    GenerationServiceUnavailable,
)
from edgentrag.retrieval.answer import answer_session
from edgentrag.retrieval.answer_schemas import AnswerRequest, AnswerResponse
from edgentrag.retrieval.prompting import AnswerContextTooLarge
from edgentrag.retrieval.service import (
    SearchLimitExceeded,
    SearchNotReady,
    SessionNotFound,
)

router = APIRouter(prefix="/sessions", tags=["answers"])


@router.post("/{session_id}/answers", response_model=AnswerResponse)
async def answer(
    session_id: str,
    body: AnswerRequest,
    database: Annotated[Database, Depends(get_database)],
    embedding_provider: Annotated[
        EmbeddingProvider | None, Depends(get_embedding_provider)
    ],
    generation_provider: Annotated[
        GenerationProvider | None, Depends(get_generation_provider)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnswerResponse:
    """Retrieve session evidence, generate a response, and return the sources."""
    try:
        return await answer_session(
            database,
            session_id=session_id,
            query=body.query,
            top_k=body.top_k,
            max_new_tokens=body.max_new_tokens,
            embedding_provider=embedding_provider,
            generation_provider=generation_provider,
            max_chunks=settings.search_max_chunks,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SearchNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SearchLimitExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except AnswerContextTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except GenerationInputTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (EmbeddingServiceUnavailable, GenerationServiceUnavailable) as exc:
        raise HTTPException(
            status_code=503,
            detail="embedding or generation service is unavailable; check API settings",
        ) from exc
