"""Every setting, for all three programs.

The API, the ingest worker and the chat worker share this file. Each reads the
settings it recognises and ignores the rest.

There is no "local vs cloud" switch here the way there was in version 1. There
is one shape — SQLite, Redis, S3, SQS — and it is the same shape everywhere.
S3 and SQS are real AWS in both cases; only the URLs differ.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"                      # "local" or "aws"
    log_level: str = "INFO"

    # --- database -----------------------------------------------------------
    # SQLite in WAL mode, on a volume every container mounts. Chunk text lives
    # here too, so retrieval needs no round trip to storage.
    #
    # Point this at Postgres and nothing else changes -- that is the whole
    # reason it is a URL. See db.py for why WAL makes this safe and where the
    # limit is.
    database_url: str = "sqlite:////data/edgentrag.db"
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # --- redis --------------------------------------------------------------
    # Two jobs: the conversation window, and the pub/sub channel that carries
    # progress and tokens from a worker to whichever API task holds the
    # browser's connection.
    redis_url: str = "redis://localhost:6379/0"
    history_turns: int = 6

    # --- object storage -----------------------------------------------------
    s3_bucket: str = "edgentrag-local"
    s3_endpoint_url: str | None = None      # None for real S3; set for an S3-compatible store
    aws_region: str = "us-east-1"
    presign_expiry_seconds: int = 3600
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024      # 2 GiB

    # --- queues -------------------------------------------------------------
    sqs_endpoint_url: str | None = None     # None for real SQS; set only to point elsewhere
    ingest_queue_url: str = ""
    chat_queue_url: str = ""
    # The two queues the GPU drains through the broker. It never sees these
    # URLs -- only the broker does.
    stt_queue_url: str = ""
    embed_queue_url: str = ""
    queue_wait_seconds: int = 20            # long polling: one call, twenty seconds
    queue_visibility_seconds: int = 900     # how long a worker owns a message
    queue_batch_size: int = 5
    # How many deliveries before SQS sets a message aside in the dead-letter
    # queue. bootstrap.py configures the queue with this; the broker uses it to
    # recognise a last attempt and give up cleanly. One value, two readers.
    queue_max_receives: int = 5

    # --- the three model services (unchanged from version 1) ----------------
    embedding_service_url: str = "http://localhost:8001"
    stt_service_url: str = "http://localhost:8002"
    llm_service_url: str = "http://localhost:8003"
    service_timeout_seconds: int = 900
    # Transcription is the one call that can outlast the others by a wide
    # margin -- an hour of video is a long job, and nobody is waiting on a
    # connection for it. Keep this above the longest video you expect.
    stt_timeout_seconds: int = 3600
    service_retries: int = 3
    service_backoff_seconds: float = 1.5

    # --- retrieval and generation -------------------------------------------
    chunk_words: int = 200
    chunk_overlap_words: int = 40
    embed_batch_size: int = 256
    top_k: int = 4
    max_new_tokens: int = 400
    temperature: float = 0.3

    # --- the broker ----------------------------------------------------------
    # One shared token, which is all the GPU ever holds. It grants "ask for a
    # job" and nothing else -- no bucket, no queue, no other session. Revoking
    # it is a line in .env. Generate one with:  openssl rand -hex 32
    broker_token: str = ""
    # How long a signed URL inside a queued job stays valid. This has to
    # outlive the *backlog*, not the job: a message sitting behind two hours of
    # other work still needs a working link when it finally gets picked up.
    job_url_expiry_seconds: int = 6 * 3600

    # --- api ----------------------------------------------------------------
    cors_origins: str = "*"
    sse_heartbeat_seconds: int = 15         # keeps intermediaries from closing the stream


@lru_cache
def get_settings() -> Settings:
    return Settings()
