"""At-least-once SQS transport shared by API and worker processes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class QueueError(RuntimeError):
    """Raised when an SQS operation cannot be completed."""


@dataclass(frozen=True)
class Message:
    """Raw SQS payload and delivery metadata.

    The transport deliberately leaves payload validation to the consuming
    domain. That lets a worker acknowledge malformed jobs instead of failing
    an entire receive batch before it reaches its poison-message handling.
    """

    body: str
    receipt_handle: str
    receive_count: int
    queue_url: str


class SQSQueue:
    """Small, dependency-light SQS adapter with long polling and batching."""

    def __init__(
        self,
        *,
        queue_url: str,
        region: str,
        endpoint_url: str | None = None,
        visibility_timeout: int = 900,
        wait_seconds: int = 20,
    ) -> None:
        self.queue_url = queue_url
        self.region = region
        self.endpoint_url = endpoint_url
        self.visibility_timeout = visibility_timeout
        self.wait_seconds = wait_seconds

    @cached_property
    def client(self):
        """Create the boto3 client lazily, after configuration is validated."""
        return boto3.client(
            "sqs",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
        )

    def _require_queue_url(self) -> None:
        if not self.queue_url:
            raise QueueError("queue URL is not configured")

    def send(self, body: dict[str, Any]) -> None:
        self._require_queue_url()
        try:
            self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(body, separators=(",", ":")),
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not send queue message") from exc

    def send_many(self, bodies: list[dict[str, Any]]) -> None:
        if len(bodies) > 10:
            raise ValueError("SQS batches cannot contain more than 10 messages")
        if not bodies:
            return
        self._require_queue_url()
        try:
            response = self.client.send_message_batch(
                QueueUrl=self.queue_url,
                Entries=[
                    {
                        "Id": str(index),
                        "MessageBody": json.dumps(body, separators=(",", ":")),
                    }
                    for index, body in enumerate(bodies)
                ],
            )
            if response.get("Failed"):
                raise QueueError("SQS rejected one or more queue messages")
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not send queue batch") from exc

    def receive(self, *, max_messages: int = 10) -> list[Message]:
        if not 1 <= max_messages <= 10:
            raise ValueError("max_messages must be between 1 and 10")
        self._require_queue_url()
        try:
            response = self.client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=self.wait_seconds,
                VisibilityTimeout=self.visibility_timeout,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not receive queue messages") from exc
        try:
            return [
                Message(
                    body=str(item["Body"]),
                    receipt_handle=item["ReceiptHandle"],
                    receive_count=int(
                        item.get("Attributes", {}).get("ApproximateReceiveCount", "1")
                    ),
                    queue_url=self.queue_url,
                )
                for item in response.get("Messages", [])
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise QueueError("SQS returned an invalid queue message") from exc

    def delete(self, message: Message) -> None:
        try:
            self.client.delete_message(
                QueueUrl=message.queue_url,
                ReceiptHandle=message.receipt_handle,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not acknowledge queue message") from exc

    def extend(self, message: Message, seconds: int | None = None) -> None:
        try:
            self.client.change_message_visibility(
                QueueUrl=message.queue_url,
                ReceiptHandle=message.receipt_handle,
                VisibilityTimeout=seconds or self.visibility_timeout,
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not extend queue message visibility") from exc

    def close(self) -> None:
        if "client" in self.__dict__:
            self.client.close()
