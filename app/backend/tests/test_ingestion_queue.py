"""Unit tests for the shared SQS transport and ingestion adapter."""

import json
from unittest.mock import Mock, patch

import pytest

from edgentrag.ingestion.queue import QueueUnavailable, SQSIngestionQueue
from edgentrag.shared.queues import SQSQueue


def make_transport() -> SQSQueue:
    return SQSQueue(
        queue_url="http://localhost:4566/000000000000/ingestion",
        region="us-east-1",
        endpoint_url="http://localhost:4566",
    )


def test_ingestion_adapter_publishes_only_stable_file_references() -> None:
    client = Mock()
    with patch("edgentrag.shared.queues.boto3.client", return_value=client) as factory:
        queue = SQSIngestionQueue(make_transport())
        queue.enqueue_file(session_id="session-1", file_id="file-1")

    factory.assert_called_once_with(
        "sqs",
        endpoint_url="http://localhost:4566",
        region_name="us-east-1",
    )
    assert json.loads(client.send_message.call_args.kwargs["MessageBody"]) == {
        "schema_version": 1,
        "session_id": "session-1",
        "file_id": "file-1",
    }
    queue.close()


def test_transport_requires_a_configured_queue() -> None:
    queue = SQSIngestionQueue(SQSQueue(queue_url="", region="us-east-1"))

    with pytest.raises(QueueUnavailable, match="queue URL is not configured"):
        queue.enqueue_file(session_id="session-1", file_id="file-1")


def test_transport_long_polls_raw_messages_and_acknowledges_them() -> None:
    client = Mock()
    client.receive_message.return_value = {
        "Messages": [
            {
                "Body": '{"schema_version":1}',
                "ReceiptHandle": "receipt-123",
                "Attributes": {"ApproximateReceiveCount": "2"},
            }
        ]
    }
    with patch("edgentrag.shared.queues.boto3.client", return_value=client):
        queue = make_transport()
        messages = queue.receive(max_messages=1)
        queue.delete(messages[0])

    assert messages[0].body == '{"schema_version":1}'
    assert messages[0].receive_count == 2
    client.receive_message.assert_called_once_with(
        QueueUrl="http://localhost:4566/000000000000/ingestion",
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
        VisibilityTimeout=900,
        AttributeNames=["ApproximateReceiveCount"],
    )
    client.delete_message.assert_called_once_with(
        QueueUrl="http://localhost:4566/000000000000/ingestion",
        ReceiptHandle="receipt-123",
    )
    queue.close()


def test_transport_extends_visibility_for_slow_work() -> None:
    client = Mock()
    with patch("edgentrag.shared.queues.boto3.client", return_value=client):
        queue = make_transport()
        message = type(
            "Message",
            (),
            {
                "queue_url": queue.queue_url,
                "receipt_handle": "receipt-123",
            },
        )()
        queue.extend(message, seconds=120)

    client.change_message_visibility.assert_called_once_with(
        QueueUrl=queue.queue_url,
        ReceiptHandle="receipt-123",
        VisibilityTimeout=120,
    )
    queue.close()
