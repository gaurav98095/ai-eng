"""Shared embedding response contract for serving, HTTP, and worker adapters."""

import math

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingBatch(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, strict=True)

    model: str = Field(min_length=1, max_length=255)
    dimensions: int = Field(gt=0)
    embeddings: list[list[float]]


def validate_embedding_batch(batch: EmbeddingBatch, expected_count: int) -> None:
    """Reject vectors that cannot participate in cosine search."""
    if len(batch.embeddings) != expected_count:
        raise ValueError("embedding service returned a different number of vectors")
    for vector in batch.embeddings:
        if len(vector) != batch.dimensions:
            raise ValueError(
                "embedding service returned inconsistent vector dimensions"
            )
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("embedding service returned a non-finite or zero vector")
