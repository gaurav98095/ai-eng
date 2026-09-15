# EdgentRAG frontend

This is the deliberately small React shell for the replica. It includes a
backend health check, with upload and chat screens kept in separate modules.
The workspace uploads supported `.md`/`.txt` files directly to the returned
storage URL, confirms each upload, and polls durable session/chat state; the
backend remains authoritative for processing and readiness.

```bash
npm install
npm run dev
```
