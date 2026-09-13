"""SQS adapter for scheduling uploaded files for ingestion."""

import json
from functools import cached_property
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class QueueUnavailable(Exception):
    """Raised when the ingestion queue cannot accept a job."""


class IngestionQueue(Protocol):
    """Small queue interface required by the upload-confirmation route."""

    def enqueue_file(self, *, session_id: str, file_id: str) -> None: ...


class SQSIngestionQueue:
    """Publish compact file references to an AWS-compatible SQS queue."""

    def __init__(
        self,
        *,
        queue_url: str,
        region: str,
        endpoint_url: str | None = None,
    ) -> None:
        self.queue_url = queue_url
        self.region = region
        self.endpoint_url = endpoint_url

    @cached_property
    def _client(self):
        """Create the boto3 client on first use, not during app startup."""
        return boto3.client(
            "sqs",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
        )

    def enqueue_file(self, *, session_id: str, file_id: str) -> None:
        """Queue IDs only; the worker will load trusted metadata from the DB."""
        if not self.queue_url:
            raise QueueUnavailable("ingestion queue is not configured")

        message = {
            "schema_version": 1,
            "session_id": session_id,
            "file_id": file_id,
        }
        try:
            self._client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message, separators=(",", ":")),
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailable("could not enqueue the uploaded file") from exc

    def close(self) -> None:
        """Release the SDK connection pool if the client was used."""
        if "_client" in self.__dict__:
            self._client.close()
