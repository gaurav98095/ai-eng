"""Queue boundary for durable speech-to-text jobs."""

from typing import Protocol

from edgentrag.shared.queues import QueueError, SQSQueue


class STTQueue(Protocol):
    """Minimal operation required by upload confirmation."""

    def enqueue_file(self, *, session_id: str, file_id: str) -> None: ...


class SQSSTTQueue:
    """Publish file identifiers, never media bytes, to the STT queue."""

    def __init__(self, queue: SQSQueue) -> None:
        self._queue = queue

    def enqueue_file(self, *, session_id: str, file_id: str) -> None:
        self._queue.send(
            {"schema_version": 1, "session_id": session_id, "file_id": file_id}
        )

    def close(self) -> None:
        self._queue.close()


STTQueueUnavailable = QueueError
