# Module 13: chat message lifecycle

This module mirrors the old app’s first chat boundary. `POST /sessions/{id}/chat`
creates a completed user row and a pending assistant row, then places a compact
job on the dedicated chat queue. `GET /sessions/{id}/chat` returns the full
conversation in creation order.

The queue is intentionally separate from ingestion: a large upload must not
delay an interactive question. Workers and SSE notifications are the next
separate slices; this module only defines their stable database and queue
contracts.

## Software practice

Keep the route, persistence model, schema, and queue adapter separate. That
makes each boundary testable and lets us replace SQS or add a worker without
changing the browser-facing contract. The migration is part of the feature:
new code must work on an upgraded database, not only a fresh one.
