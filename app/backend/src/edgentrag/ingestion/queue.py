"""Domain queue boundary for scheduling uploaded files."""

from typing import Protocol

from edgentrag.shared.queues import QueueError, SQSQueue


class IngestionQueue(Protocol):
    """The only queue operation required by upload confirmation."""

    def enqueue_file(self, *, session_id: str, file_id: str) -> None: ...

    def ping(self) -> bool: ...


class SQSIngestionQueue:
    """Publish stable file references through the shared SQS transport."""

    def __init__(self, queue: SQSQueue) -> None:
        self._queue = queue

    def enqueue_file(self, *, session_id: str, file_id: str) -> None:
        self._queue.send(
            {"schema_version": 1, "session_id": session_id, "file_id": file_id}
        )

    def ping(self) -> bool:
        return self._queue.ping()

    def close(self) -> None:
        self._queue.close()


QueueUnavailable = QueueError
