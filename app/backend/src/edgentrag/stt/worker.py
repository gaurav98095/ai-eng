"""Idempotent STT queue consumer skeleton for the v3 deployment."""

from __future__ import annotations

import json
import logging
import time

from edgentrag.core.config import load_settings
from edgentrag.shared.queues import QueueError, SQSQueue

logger = logging.getLogger(__name__)


def handle_job(body: dict) -> None:
    """Validate a compact job; provider integration is injectable."""
    if body.get("schema_version") != 1 or not body.get("file_id"):
        raise ValueError("invalid STT job")
    raise RuntimeError("STT provider is not configured")


def run_worker() -> None:
    settings = load_settings()
    if not settings.stt_queue_url:
        logger.info("STT queue is not configured; worker is disabled")
        return
    queue = SQSQueue(queue_url=settings.stt_queue_url, region=settings.aws_region,
                     endpoint_url=settings.aws_endpoint_url,
                     visibility_timeout=settings.queue_visibility_timeout_seconds,
                     wait_seconds=settings.queue_wait_seconds)
    try:
        while True:
            try:
                messages = queue.receive(max_messages=settings.queue_batch_size)
            except QueueError:
                logger.exception("STT queue unavailable")
                time.sleep(2)
                continue
            for message in messages:
                try:
                    handle_job(message.body)
                except ValueError:
                    logger.exception("Discarding poison STT message")
                    queue.delete(message)
                except RuntimeError:
                    # Do not acknowledge a valid job until transcription and
                    # durable result persistence are implemented/configured.
                    logger.error("STT job unavailable; leaving message for retry")
                else:
                    queue.delete(message)
    finally:
        queue.close()


if __name__ == "__main__":
    logging.basicConfig(level=load_settings().log_level)
    run_worker()
