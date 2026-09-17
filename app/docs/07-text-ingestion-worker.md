# Component 7: extract text in a background worker

> **Historical v2 walkthrough.** The worker is now a Compose service. Start it
> with [`make -C app infra-local`](22-current-architecture.md); do not run the
> old host-side command or use the old `document_chunks` table name.

Component 6 verified an uploaded object and enqueued its `session_id` and
`file_id`. This component adds a separate process that long-polls SQS, loads the
trusted file record, downloads the object from S3/Floci, validates its actual
bytes, extracts text, and stores overlapping chunks.

~~~text
Floci SQS ── {session_id, file_id} ──> worker
worker ───── object bytes ───────────> Floci S3
worker ───── extracted chunks ───────> document_chunks table
~~~

The worker is separate from FastAPI so slow document processing does not block
HTTP requests. The API and worker share the same database and configuration,
but they are independent processes.

## Supported input in this component

For now, only `.md` and `.txt` files are accepted:

- Markdown must declare `text/markdown`.
- Plain text must declare `text/plain`.
- Bytes must be valid UTF-8, with no NUL bytes, and contain non-whitespace
  text.
- Uploads are limited to 20 MiB by default through
  `EDGENTRAG_MAX_UPLOAD_BYTES`.
- The worker independently reads at most `EDGENTRAG_MAX_TEXT_EXTRACT_BYTES`
  (also 20 MiB by default). It reads one extra byte to detect overflow without
  loading an oversized object into memory.

Other extensions are rejected with `415` when requesting an upload target.
PDF, Office, audio, and video parsers can be added as later components. The
worker limit is an independent guard in case an object changes after upload
confirmation or configuration differs between API and worker.

## Chunking and persistence

The first chunker splits text into 1,200-character pieces with 150 characters
of overlap. Character windows keep this component dependency-free; future
retrieval work can replace the policy with tokenizer-aware chunking. Each row
in `document_chunks` has a file ID and stable chunk index. A uniqueness
constraint prevents duplicate chunk numbers for the same file, and the
foreign-key cascade removes chunks if their file record is deleted.

The worker stores chunks and marks a file `ready` in one database transaction.
If a session still has files being uploaded or processed, it remains
`processing`; it becomes `ready` when all files are ready, or `failed` if all
work has finished and at least one file failed.

## Apply the migration

Revision `0003_create_document_chunks` adds the chunk table. From the project
root, run:

~~~bash
.venv/bin/alembic -c app/backend/alembic.ini upgrade head
~~~

The end-to-end script also applies pending migrations automatically.

## Run the worker with Floci

Start Floci and the complete application stack using the [current runbook](22-current-architecture.md).
The ingestion worker is managed by Compose:

~~~bash
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
docker compose -f app/compose.yaml logs --tail=100 ingestion-worker
~~~

Submit the session/upload/PUT/complete requests from FastAPI `/docs`. The
worker receives the SQS job, stores chunks, and acknowledges the message only
after the database commit. Stop the application with `make -C app destroy-local`
when you are done.

## Retry and failure behavior

The worker deletes a queue message only after the job is complete or known to
be invalid. S3/DB failures leave it for SQS redelivery. If a message arrives
during the API's short window between sending it and committing the file's
`uploaded` status, the worker leaves it for retry rather than discarding it.
Already-ready or already-failed files are treated idempotently on redelivery.
Concurrent deliveries may perform duplicate external work, but only the first
terminal database transition replaces chunks and updates the parent session.
There is no lease/claim protocol yet; multi-worker claiming and a dead-letter
queue remain deferred.

Invalid text (wrong MIME type, binary/NUL bytes, invalid UTF-8, empty text, or
over the worker's byte limit) is marked `failed` and acknowledged so a
permanently bad document is not retried forever. This worker is intentionally
single-process for the tutorial; later work can add multi-worker claiming and a
dead-letter queue.

## Tests and next step

The tests cover UTF-8 validation, chunk overlap, size limits, migration shape,
and a worker run that persists chunks and updates file/session status. They use
temporary SQLite databases and fake storage, so they do not need Floci.

The next component adds a separate, authenticated embedding service for
Colab. A later component will connect it to these stored chunks and add vector
search.
