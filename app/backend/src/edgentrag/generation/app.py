"""Authenticated HTTP boundary for the separately deployed language model."""

import hmac
import logging
from time import perf_counter
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from edgentrag.core.telemetry import configure
from edgentrag.generation.model import (
    GenerationInputTooLarge,
    TextGenerator,
    TransformersGenerator,
)
from edgentrag.generation.settings import GenerationSettings
from edgentrag.llm.contracts import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


def create_app(
    *,
    settings: GenerationSettings | None = None,
    generator: TextGenerator | None = None,
) -> FastAPI:
    configure("edgentrag-generation")
    configuration = settings if settings is not None else GenerationSettings()
    backend = (
        generator if generator is not None else TransformersGenerator(configuration)
    )
    app = FastAPI(title="EdgentRAG Generation Service", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "model": configuration.model_name,
            "loaded": backend.is_loaded,
            "device": getattr(backend, "device", configuration.device),
        }

    @app.post("/generate", response_model=LLMResponse)
    async def generate(
        body: LLMRequest,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
        ],
    ) -> LLMResponse:
        started = perf_counter()
        input_chars = len(body.instructions) + len(body.prompt)
        expected = configuration.api_token.get_secret_value()
        if not expected:
            raise HTTPException(
                503, "generation service authentication is not configured"
            )
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(
                401,
                "invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            result = await run_in_threadpool(backend.generate, body)
            logger.info(
                "generation_request input_chars=%d max_new_tokens=%d model=%s "
                "input_tokens=%d output_tokens=%d duration_ms=%.2f",
                input_chars,
                body.max_new_tokens,
                result.model,
                result.input_tokens,
                result.output_tokens,
                (perf_counter() - started) * 1000,
            )
            return result
        except GenerationInputTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "Generation request failed input_chars=%d max_new_tokens=%d "
                "duration_ms=%.2f",
                input_chars,
                body.max_new_tokens,
                (perf_counter() - started) * 1000,
            )
            raise HTTPException(
                503, "generation model is temporarily unavailable"
            ) from exc

    return app


app = create_app()
