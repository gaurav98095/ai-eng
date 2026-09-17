# Component 14: queued chat worker

> **Historical v2 walkthrough.** Run the current worker as the Compose service
> started by `make -C app infra-local`; the host-side command below is useful
> only for isolated development.

The chat API deliberately acknowledges a turn before doing remote inference.
`POST /sessions/{session_id}/chat` writes a user message and a pending
assistant row, then publishes only their IDs to SQS. This keeps HTTP latency
bounded and means a browser disconnect cannot cancel the answer.

Run the consumer separately from the API:

~~~bash
.venv/bin/python -m edgentrag.chat.worker
~~~

The worker claims the assistant row (`pending` → `answering`), performs the
same session-scoped search and grounded generation as the synchronous answers
endpoint, and stores the answer plus its source citations before acknowledging
the queue message. Duplicate deliveries are safe: completed or failed rows
are no-ops. Remote dependency failures reset the row to `pending` and leave
the message visible for SQS retry; invalid input or a bounded prompt failure
is recorded as `failed` and acknowledged.

This is intentionally a polling contract for now. A later UI can poll the
chat endpoint or add an SSE projection without changing durable message state
or the queue schema.
