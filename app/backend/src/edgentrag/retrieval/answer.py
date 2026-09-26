"""Retrieve session evidence, then delegate prompt and inference to ``llm``."""

from edgentrag.core.database import Database
from edgentrag.core.telemetry import capture_content, set_span_attributes, span
from edgentrag.embedding.client import EmbeddingProvider
from edgentrag.llm.client import LLMProvider, LLMServiceUnavailable
from edgentrag.llm.contracts import LLMRequest
from edgentrag.llm.prompts.grounded_answer import (
    GROUNDED_ANSWER_INSTRUCTIONS,
    build_grounded_answer_prompt,
)
from edgentrag.retrieval.answer_schemas import AnswerResponse
from edgentrag.retrieval.service import search_session


async def answer_session(
    database: Database,
    *,
    session_id: str,
    query: str,
    top_k: int,
    max_new_tokens: int,
    embedding_provider: EmbeddingProvider | None,
    llm_provider: LLMProvider | None,
    max_chunks: int,
    max_prompt_chars: int,
) -> AnswerResponse:
    """Answer from only the retrieved chunks for the requested session."""
    if llm_provider is None:
        raise LLMServiceUnavailable("LLM service is not configured")
    include_content = capture_content()
    with span(
        "rag.answer",
        **{
            "openinference.span.kind": "CHAIN",
            "session.id": session_id,
            "input.value": query if include_content else None,
            "input.mime_type": "text/plain" if include_content else None,
            "input.chars": len(query),
        },
    ) as current:
        with span(
            "rag.retrieve",
            **{
                "openinference.span.kind": "RETRIEVER",
                "input.value": query if include_content else None,
                "input.mime_type": "text/plain" if include_content else None,
            },
        ) as retrieval_span:
            search = await search_session(
                database,
                session_id=session_id,
                query=query,
                top_k=top_k,
                provider=embedding_provider,
                max_chunks=max_chunks,
            )
            set_span_attributes(
                retrieval_span,
                **{
                    "retrieval.match_count": len(search.matches),
                    "retrieval.searched_chunks": search.searched_chunks,
                },
            )
        context = build_grounded_answer_prompt(
            query,
            search.matches,
            max_prompt_chars=max_prompt_chars,
        )
        set_span_attributes(
            current,
            **{
                "retrieval.match_count": len(search.matches),
                "llm.input_chars": len(context.prompt),
            },
        )
        generated = await llm_provider.generate(
            LLMRequest(
                instructions=GROUNDED_ANSWER_INSTRUCTIONS,
                prompt=context.prompt,
                max_new_tokens=max_new_tokens,
            )
        )
        set_span_attributes(
            current,
            **{
                "output.value": generated.content if include_content else None,
                "output.mime_type": "text/plain" if include_content else None,
                "llm.output_chars": len(generated.content),
            },
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
