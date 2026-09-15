"""Queue boundary for interactive chat jobs."""

import json
from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class ChatQueueUnavailable(Exception):
    """Raised when a chat job cannot be accepted."""


@dataclass(frozen=True)
class ChatQueueMessage:
    """Chat payload and receipt handle required to acknowledge delivery."""

    body: str
    receipt_handle: str


class ChatQueue(Protocol):
    def enqueue_message(
        self, *, session_id: str, message_id: str, question: str
    ) -> None: ...

    def receive_messages(
        self,
        *,
        max_messages: int = 1,
        wait_time_seconds: int = 20,
        visibility_timeout_seconds: int | None = None,
    ) -> list[ChatQueueMessage]: ...

    def delete_message(self, *, receipt_handle: str) -> None: ...


class SQSChatQueue:
    """Publish a compact chat job to an AWS-compatible SQS queue."""

    def __init__(
        self, *, queue_url: str, region: str, endpoint_url: str | None = None
    ) -> None:
        self.queue_url = queue_url
        self.region = region
        self.endpoint_url = endpoint_url

    @cached_property
    def _client(self):
        return boto3.client(
            "sqs", endpoint_url=self.endpoint_url, region_name=self.region
        )

    def enqueue_message(
        self, *, session_id: str, message_id: str, question: str
    ) -> None:
        if not self.queue_url:
            raise ChatQueueUnavailable("chat queue is not configured")
        body = {
            "schema_version": 1,
            "session_id": session_id,
            "message_id": message_id,
            "question": question,
        }
        try:
            self._client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(body, separators=(",", ":")),
            )
        except (BotoCoreError, ClientError) as exc:
            raise ChatQueueUnavailable("could not enqueue the chat message") from exc

    def receive_messages(
        self, *, max_messages: int = 1, wait_time_seconds: int = 20
    ) -> list[ChatQueueMessage]:
        """Long-poll a bounded batch from SQS."""
        if not self.queue_url:
            raise ChatQueueUnavailable("chat queue is not configured")
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
            raise ChatQueueUnavailable("could not receive chat jobs") from exc
        try:
            return [
                ChatQueueMessage(
                    body=str(message["Body"]),
                    receipt_handle=str(message["ReceiptHandle"]),
                )
                for message in response.get("Messages", [])
            ]
        except KeyError as exc:
            raise ChatQueueUnavailable(
                "SQS returned an incomplete chat message"
            ) from exc

    def delete_message(self, *, receipt_handle: str) -> None:
        """Acknowledge a job only after its answer is durably stored."""
        if not self.queue_url:
            raise ChatQueueUnavailable("chat queue is not configured")
        try:
            self._client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ChatQueueUnavailable("could not acknowledge chat job") from exc

    def close(self) -> None:
        if "_client" in self.__dict__:
            self._client.close()
