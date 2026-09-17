# Module 13: chat message lifecycle

> **Historical v2 walkthrough.** The durable contract remains, but this page's
> “old app” wording and standalone assumptions are superseded by
> [the current architecture guide](22-current-architecture.md).

This module mirrors the old app’s first chat boundary. `POST /sessions/{id}/chat`
creates a completed user row and a pending assistant row, then places a compact
job on the dedicated chat queue. `GET /sessions/{id}/chat` returns the full
conversation in creation order.

The database commit happens before queue publication so a consumer can see the
rows referenced by its job. If publication fails, the assistant row is marked
`failed` and the API returns `503`; the user turn is retained for diagnosis.
Database commit and queue send are separate operations, so a network failure
can still be ambiguous. A production implementation should add a transactional
outbox before promising exactly-once delivery.

The queue is intentionally separate from ingestion: a large upload must not
delay an interactive question. Component 14 adds the worker that consumes this
contract. SSE notifications remain a presentation-layer extension; polling the
same chat endpoint is sufficient for the current replica.

## Software practice

Keep the route, persistence model, schema, and queue adapter separate. That
makes each boundary testable and lets us replace SQS or add a worker without
changing the browser-facing contract. The migration is part of the feature:
new code must work on an upgraded database, not only a fresh one.
