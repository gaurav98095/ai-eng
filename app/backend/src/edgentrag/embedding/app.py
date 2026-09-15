"""Authenticated HTTP interface for producing text embeddings on Colab."""

import hmac
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from edgentrag.embedding.model import SentenceTransformerEmbedder, TextEmbedder
from edgentrag.embedding.schemas import EmbeddingBatch as EmbedResponse
from edgentrag.embedding.schemas import validate_embedding_batch
from edgentrag.embedding.settings import EmbeddingSettings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


class EmbedRequest(BaseModel):
    """A bounded batch of texts to encode with the configured model."""

    texts: list[str] = Field(min_length=1, max_length=64)


def create_app(
    *,
    settings: EmbeddingSettings | None = None,
    embedder: TextEmbedder | None = None,
) -> FastAPI:
    """Build the service with injectable settings and model for tests."""
    app_settings = settings or EmbeddingSettings()
    app_embedder = embedder or SentenceTransformerEmbedder(
        model_name=app_settings.model_name,
        device=app_settings.device,
    )
    app = FastAPI(
        title="EdgentRAG Embedding Service",
        version="0.1.0",
        description="Creates normalized text vectors for retrieval.",
    )
    app.state.settings = app_settings
    app.state.embedder = app_embedder

    @app.get("/health")
    async def health() -> dict[str, str | bool]:
        """Report service availability without loading the model weights."""
        return {
            "status": "ok",
            "model": app_settings.model_name,
            "device": getattr(app_embedder, "device", app_settings.device),
            "loaded": app_embedder.is_loaded,
        }

    @app.post("/embed", response_model=EmbedResponse)
    async def embed_texts(
        body: EmbedRequest,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer_scheme),
        ],
    ) -> EmbedResponse:
        """Embed a batch after authenticating the caller."""
        expected_token = app_settings.api_token.get_secret_value()
        if not expected_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="embedding service authentication is not configured",
            )
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"),
            expected_token.encode("utf-8"),
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if len(body.texts) > app_settings.max_batch_size:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"a batch may contain at most {app_settings.max_batch_size} texts"
                ),
            )
        if any(
            not text.strip() or len(text) > app_settings.max_text_characters
            for text in body.texts
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "each text must contain non-whitespace content and stay "
                    f"within {app_settings.max_text_characters} characters"
                ),
            )

        try:
            vectors = await run_in_threadpool(app_embedder.embed, body.texts)
            if len(vectors) != len(body.texts) or not vectors or not vectors[0]:
                raise RuntimeError("model returned an unexpected vector batch")
            dimensions = len(vectors[0])
            result = EmbedResponse(
                model=app_settings.model_name, dimensions=dimensions, embeddings=vectors
            )
            validate_embedding_batch(result, len(body.texts))
        except Exception as exc:
            logger.exception("Embedding request failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="embedding model is temporarily unavailable",
            ) from exc

        return result

    return app


app = create_app()
