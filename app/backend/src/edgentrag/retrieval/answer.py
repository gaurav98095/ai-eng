"""Retrieve bounded evidence and pass it to the standalone generator."""

from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider
from edgentrag.generation.client import (
    GenerationProvider,
    GenerationServiceUnavailable,
)
from edgentrag.generation.schemas import GenerateRequest
from edgentrag.retrieval.answer_schemas import AnswerResponse
from edgentrag.retrieval.prompting import (
    ANSWER_INSTRUCTIONS,
    build_answer_context,
)
from edgentrag.retrieval.service import search_session


async def answer_session(
    database: Database,
    *,
    session_id: str,
    query: str,
    top_k: int,
    max_new_tokens: int,
    embedding_provider: EmbeddingProvider | None,
    generation_provider: GenerationProvider | None,
    max_chunks: int,
) -> AnswerResponse:
    """Answer from only the retrieved chunks for the requested session."""
    if generation_provider is None:
        raise GenerationServiceUnavailable("generation service is not configured")
    search = await search_session(
        database,
        session_id=session_id,
        query=query,
        top_k=top_k,
        provider=embedding_provider,
        max_chunks=max_chunks,
    )
    context = build_answer_context(query, search.matches)
    generated = await generation_provider.generate(
        GenerateRequest(
            instructions=ANSWER_INSTRUCTIONS,
            prompt=context.prompt,
            max_new_tokens=max_new_tokens,
        )
    )
    return AnswerResponse(
        session_id=session_id,
        query=query,
        answer=generated.content,
        generation_model=generated.model,
        input_tokens=generated.input_tokens,
        output_tokens=generated.output_tokens,
        sources=context.sources,
    )
