"""Unit tests for the SQS ingestion-queue adapter."""

import json
from unittest.mock import Mock, patch

import pytest

from edgentrag.ingestion.queue import QueueMessage, QueueUnavailable, SQSIngestionQueue


def test_sqs_adapter_publishes_only_stable_file_references() -> None:
    client = Mock()

    with patch(
        "edgentrag.ingestion.queue.boto3.client", return_value=client
    ) as factory:
        queue = SQSIngestionQueue(
            queue_url="http://localhost:4566/000000000000/ingestion",
            region="us-east-1",
            endpoint_url="http://localhost:4566",
        )
        queue.enqueue_file(session_id="session-1", file_id="file-1")

    factory.assert_called_once_with(
        "sqs",
        endpoint_url="http://localhost:4566",
        region_name="us-east-1",
    )
    client.send_message.assert_called_once()
    call = client.send_message.call_args.kwargs
    assert call["QueueUrl"] == "http://localhost:4566/000000000000/ingestion"
    assert json.loads(call["MessageBody"]) == {
        "schema_version": 1,
        "session_id": "session-1",
        "file_id": "file-1",
    }
    queue.close()


def test_sqs_adapter_requires_a_configured_queue() -> None:
    queue = SQSIngestionQueue(queue_url="", region="us-east-1")

    with pytest.raises(QueueUnavailable, match="queue is not configured"):
        queue.enqueue_file(session_id="session-1", file_id="file-1")


def test_sqs_adapter_long_polls_and_acknowledges_messages() -> None:
    client = Mock()
    client.receive_message.return_value = {
        "Messages": [
            {
                "Body": '{"schema_version":1}',
                "ReceiptHandle": "receipt-123",
            }
        ]
    }

    with patch("edgentrag.ingestion.queue.boto3.client", return_value=client):
        queue = SQSIngestionQueue(
            queue_url="http://localhost:4566/000000000000/ingestion",
            region="us-east-1",
        )
        messages = queue.receive_messages(max_messages=1, wait_time_seconds=20)
        queue.delete_message(receipt_handle=messages[0].receipt_handle)

    assert messages == [
        QueueMessage(body='{"schema_version":1}', receipt_handle="receipt-123")
    ]
    client.receive_message.assert_called_once_with(
        QueueUrl="http://localhost:4566/000000000000/ingestion",
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
    )
    client.delete_message.assert_called_once_with(
        QueueUrl="http://localhost:4566/000000000000/ingestion",
        ReceiptHandle="receipt-123",
    )
    queue.close()
