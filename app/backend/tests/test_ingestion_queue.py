"""Unit tests for the SQS ingestion-queue adapter."""

import json
from unittest.mock import Mock, patch

import pytest

from edgentrag.ingestion.queue import QueueUnavailable, SQSIngestionQueue


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
