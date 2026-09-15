"""Queue boundary for interactive chat jobs."""

import json
from functools import cached_property
from typing import Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class ChatQueueUnavailable(Exception):
    """Raised when a chat job cannot be accepted."""


class ChatQueue(Protocol):
    def enqueue_message(
        self, *, session_id: str, message_id: str, question: str
    ) -> None: ...


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

    def close(self) -> None:
        if "_client" in self.__dict__:
            self._client.close()
