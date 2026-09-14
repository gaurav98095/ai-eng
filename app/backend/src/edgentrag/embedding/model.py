"""Lazy adapter around the Sentence Transformers model used on Colab."""

import logging
from threading import Lock
from typing import Protocol

logger = logging.getLogger(__name__)


class TextEmbedder(Protocol):
    """Minimal interface the HTTP service needs from an embedding model."""

    @property
    def is_loaded(self) -> bool: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Load one model on first use and return normalized dense vectors."""

    def __init__(self, *, model_name: str, device: str = "auto") -> None:
        self.model_name = model_name
        self.requested_device = device
        self.device = device
        self._model = None
        self._model_lock = Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch
        except ImportError:
            return "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch using unit-length vectors suitable for cosine search."""
        with self._model_lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError(
                        "install the embedding extra to load Sentence Transformers"
                    ) from exc

                logger.info(
                    "Loading embedding model %s on %s",
                    self.model_name,
                    self.device,
                )
                resolved_device = self._resolve_device(self.requested_device)
                self._model = SentenceTransformer(
                    self.model_name,
                    device=resolved_device,
                )
                self.device = resolved_device

            vectors = self._model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )

        return vectors.astype(float).tolist()
