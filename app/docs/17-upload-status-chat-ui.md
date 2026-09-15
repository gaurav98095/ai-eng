# Component 17: upload, status, and chat UI

The React workspace now combines three browser responsibilities without
blurring their boundaries:

- `api.ts` owns HTTP contracts and the direct-to-storage PUT;
- `UploadPanel` requests signed targets, uploads bytes, confirms each object,
  and polls the backend for durable file/session status;
- `SessionWorkspace` renders the conversation and polls durable chat messages.

The browser never marks an upload ready from a successful PUT alone. The
completion endpoint verifies storage metadata and queues ingestion; the status
read endpoint remains authoritative while the worker progresses a file from
`uploaded` to `processing`, `ready`, or `failed`.

The panel intentionally supports the backend's current Markdown/plain-text
scope. Future parsers can expand the `accept` list and validation contract
without changing the chat component.
