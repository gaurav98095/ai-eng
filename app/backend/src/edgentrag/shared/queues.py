"""At-least-once SQS contracts shared by API and worker processes."""

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
    body: dict[str, Any]
    receipt_handle: str
    receive_count: int
    queue_url: str


class SQSQueue:
    """Small, dependency-light SQS adapter with long polling and batching."""

    def __init__(self, *, queue_url: str, region: str, endpoint_url: str | None = None,
                 visibility_timeout: int = 900, wait_seconds: int = 20) -> None:
        self.queue_url = queue_url
        self.region = region
        self.endpoint_url = endpoint_url
        self.visibility_timeout = visibility_timeout
        self.wait_seconds = wait_seconds

    @cached_property
    def client(self):
        return boto3.client("sqs", region_name=self.region, endpoint_url=self.endpoint_url)

    def send(self, body: dict[str, Any]) -> None:
        if not self.queue_url:
            raise QueueError("queue URL is not configured")
        try:
            self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(body, separators=(",", ":")))
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not send queue message") from exc

    def send_many(self, bodies: list[dict[str, Any]]) -> None:
        if len(bodies) > 10:
            raise ValueError("SQS batches cannot contain more than 10 messages")
        if not bodies:
            return
        try:
            self.client.send_message_batch(
                QueueUrl=self.queue_url,
                Entries=[{"Id": str(i), "MessageBody": json.dumps(body, separators=(",", ":"))} for i, body in enumerate(bodies)],
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not send queue batch") from exc

    def receive(self, *, max_messages: int = 10) -> list[Message]:
        if not 1 <= max_messages <= 10:
            raise ValueError("max_messages must be between 1 and 10")
        try:
            response = self.client.receive_message(
                QueueUrl=self.queue_url, MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=self.wait_seconds, VisibilityTimeout=self.visibility_timeout,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError) as exc:
            raise QueueError("could not receive queue messages") from exc
        return [Message(json.loads(item["Body"]), item["ReceiptHandle"],
                        int(item.get("Attributes", {}).get("ApproximateReceiveCount", "1")), self.queue_url)
                for item in response.get("Messages", [])]

    def delete(self, message: Message) -> None:
        self.client.delete_message(QueueUrl=message.queue_url, ReceiptHandle=message.receipt_handle)

    def extend(self, message: Message, seconds: int | None = None) -> None:
        self.client.change_message_visibility(QueueUrl=message.queue_url,
                                              ReceiptHandle=message.receipt_handle,
                                              VisibilityTimeout=seconds or self.visibility_timeout)

    def close(self) -> None:
        if "client" in self.__dict__:
            self.client.close()
