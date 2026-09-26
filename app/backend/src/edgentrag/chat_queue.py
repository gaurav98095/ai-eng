"""Domain queue boundary for interactive chat jobs."""

from typing import Protocol

from edgentrag.shared.queues import QueueError, SQSQueue


class ChatQueue(Protocol):
    """The only queue operation required to accept a chat turn."""

    def enqueue_message(
        self, *, session_id: str, message_id: str, question: str
    ) -> None: ...

    def ping(self) -> bool: ...


class SQSChatQueue:
    """Publish persisted chat-turn references through the shared transport."""

    def __init__(self, queue: SQSQueue) -> None:
        self._queue = queue

    def enqueue_message(
        self, *, session_id: str, message_id: str, question: str
    ) -> None:
        self._queue.send(
            {
                "schema_version": 1,
                "session_id": session_id,
                "message_id": message_id,
                "question": question,
            }
        )

    def ping(self) -> bool:
        return self._queue.ping()

    def close(self) -> None:
        self._queue.close()


ChatQueueUnavailable = QueueError
