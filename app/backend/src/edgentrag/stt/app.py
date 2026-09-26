"""Authenticated speech-to-text API for Colab or Lightning hosting."""

from __future__ import annotations

import hmac
import logging
import os
import tempfile
from time import perf_counter
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .settings import load_settings, resolve_compute_type, resolve_device

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
settings = load_settings()
_model = None


def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        device = resolve_device(settings.device)
        _model = WhisperModel(
            settings.model_name,
            device=device,
            compute_type=resolve_compute_type(settings.compute_type, device),
        )
    return _model


app = FastAPI(title="EdgentRAG Speech-to-Text Service", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "model": settings.model_name,
        "device": resolve_device(settings.device),
        "compute_type": resolve_compute_type(
            settings.compute_type, resolve_device(settings.device)
        ),
        "loaded": _model is not None,
    }


@app.post("/transcribe")
def transcribe(
    session_id: Annotated[str, Form(min_length=1)],
    file_id: Annotated[str, Form(min_length=1)],
    file: Annotated[UploadFile, File(...)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, object]:
    started = perf_counter()
    expected = settings.api_token.get_secret_value()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "STT authentication is not configured",
        )
    if credentials is None or not hmac.compare_digest(
        credentials.credentials, expected
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token"
        )

    suffix = os.path.splitext(file.filename or "audio.bin")[1] or ".bin"
    size = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        "media exceeds upload limit",
                    )
                target.write(chunk)
            target.flush()
            segments, info = _load_model().transcribe(
                target.name, beam_size=1, vad_filter=True
            )
            rows = [
                {
                    "start": round(s.start, 2),
                    "end": round(s.end, 2),
                    "text": s.text.strip(),
                }
                for s in segments
                if s.text.strip()
            ]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "STT request failed session_id=%s file_id=%s input_bytes=%d "
            "duration_ms=%.2f",
            session_id,
            file_id,
            size,
            (perf_counter() - started) * 1000,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "speech-to-text model is temporarily unavailable",
        ) from exc
    logger.info(
        "stt_request session_id=%s file_id=%s input_bytes=%d segments=%d "
        "output_chars=%d model=%s duration_ms=%.2f",
        session_id,
        file_id,
        size,
        len(rows),
        sum(len(row["text"]) for row in rows),
        settings.model_name,
        (perf_counter() - started) * 1000,
    )
    return {
        "session_id": session_id,
        "file_id": file_id,
        "model": settings.model_name,
        "seconds": info.duration,
        "segments": rows,
    }
