"""SQS adapter for scheduling uploaded files for ingestion."""

import json
from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class QueueUnavailable(Exception):
    """Raised when the ingestion queue cannot accept a job."""


@dataclass(frozen=True)
class QueueMessage:
    """SQS payload and receipt handle required to acknowledge delivery."""

    body: str
    receipt_handle: str


class IngestionQueue(Protocol):
    """Small queue interface required by the upload-confirmation route."""

    def enqueue_file(self, *, session_id: str, file_id: str) -> None: ...

    def receive_messages(
        self,
        *,
        max_messages: int = 1,
        wait_time_seconds: int = 20,
        visibility_timeout_seconds: int | None = None,
    ) -> list[QueueMessage]: ...

    def delete_message(self, *, receipt_handle: str) -> None: ...


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

    def receive_messages(
        self,
        *,
        max_messages: int = 1,
        wait_time_seconds: int = 20,
        visibility_timeout_seconds: int | None = None,
    ) -> list[QueueMessage]:
        """Long-poll a bounded batch from SQS."""
        if not self.queue_url:
            raise QueueUnavailable("ingestion queue is not configured")
        if not 1 <= max_messages <= 10:
            raise ValueError("max_messages must be between 1 and 10")
        if not 0 <= wait_time_seconds <= 20:
            raise ValueError("wait_time_seconds must be between 0 and 20")

        try:
            response = self._client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time_seconds,
                **(
                    {"VisibilityTimeout": visibility_timeout_seconds}
                    if visibility_timeout_seconds is not None
                    else {}
                ),
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailable("could not receive ingestion jobs") from exc

        messages: list[QueueMessage] = []
        for message in response.get("Messages", []):
            try:
                messages.append(
                    QueueMessage(
                        body=str(message["Body"]),
                        receipt_handle=str(message["ReceiptHandle"]),
                    )
                )
            except KeyError as exc:
                raise QueueUnavailable("SQS returned an incomplete message") from exc
        return messages

    def delete_message(self, *, receipt_handle: str) -> None:
        """Acknowledge a job only after it has been processed successfully."""
        if not self.queue_url:
            raise QueueUnavailable("ingestion queue is not configured")
        try:
            self._client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueUnavailable("could not acknowledge ingestion job") from exc

    def close(self) -> None:
        """Release the SDK connection pool if the client was used."""
        if "_client" in self.__dict__:
            self._client.close()
