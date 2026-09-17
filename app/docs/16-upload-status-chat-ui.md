# Component 16: upload, status, and chat UI boundary

> **Historical v2 walkthrough.** The current UI also receives event updates and
> the complete startup is documented in [the current runbook](22-current-architecture.md).

The frontend workspace owns session and conversation state in a dedicated
component. Its API calls remain in `src/api.ts`; polling is deliberately used
for the current replica because the backend exposes durable chat state and does
not yet need an SSE transport. The component can later swap polling for SSE
without changing message rendering.

Upload controls should use the same pattern: request a presigned target,
upload bytes directly to storage, then confirm the file and poll its status.
The backend remains the source of truth; the browser must never mark a file
ready based only on a successful object-storage upload.
