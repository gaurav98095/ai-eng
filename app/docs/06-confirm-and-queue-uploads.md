# Component 6: confirm uploads and queue ingestion

Component 5 created a database record and a presigned S3 URL. That URL only
grants permission to upload; it does not prove that bytes arrived. Here the
client confirms completion, the API checks the object metadata in S3, and only
then sends a small job message to SQS.

~~~text
Client ── PUT file bytes ───────────────────────────> S3 / Floci
Client ── POST /sessions/{id}/uploads/{file}/complete ──> API
API ───── HEAD object ──────────────────────────────> S3 / Floci
API ───── {session_id, file_id} ────────────────────> SQS / Floci
~~~

The file contents stay out of the API and queue. The worker in a later
component will load trusted metadata from the database and fetch the object by
its generated key.

## Verify before scheduling

The completion endpoint looks up the file record and checks the S3 object's
Content-Length and Content-Type against the values supplied when the upload
target was created. If the object is missing, it returns `409`; if the metadata
doesn't match, it returns `422`. Neither case queues work.

When the object matches, the API sends the session and file IDs to SQS, then
marks the file `uploaded` and its session `processing`. Repeating a successful
confirmation is safe: the API returns the existing status without enqueueing a
second message. SQS is an at-least-once queue, so the future worker must still
be idempotent in case a message is delivered more than once.

The queue adapter is behind a small `IngestionQueue` protocol, like the S3
adapter is behind `ObjectStorage`. Tests use fakes, while local development and
production use the same boto3 adapter pointed at Floci or AWS.

## Configure an SQS queue in Floci

The local queue already created in Floci is `edgentrag-test-queue`, with URL
`http://localhost:4566/000000000000/edgentrag-test-queue`. This URL is set in
the local `.env` and example configuration. If you need to create another
queue later, use the Floci UI or AWS CLI against Floci. The CLI route needs
dummy credentials for signing; it does not contact real AWS:

~~~bash
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
aws sqs create-queue \
  --queue-name edgentrag-ingestion \
  --endpoint-url http://localhost:4566 \
  --region us-east-1
~~~

For a newly-created queue, copy its returned `QueueUrl` into
`app/backend/.env`. The existing queue uses:

~~~dotenv
EDGENTRAG_INGESTION_QUEUE_URL=http://localhost:4566/000000000000/edgentrag-test-queue
~~~

Prefer the exact URL Floci returns if its account ID or path differs. Keep
`EDGENTRAG_AWS_ENDPOINT_URL=http://localhost:4566`,
`EDGENTRAG_AWS_REGION=us-east-1`, and
`EDGENTRAG_S3_BUCKET=edgentrag-test-1` in that same file. Restart Uvicorn after
changing settings. If Uvicorn runs inside a container, configure an endpoint
hostname that the API container can reach rather than `localhost`.

For Floci's local development credentials, export these in the same terminal
before starting Uvicorn. Keep them out of `.env` and source control:

~~~bash
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

## Use the flow from FastAPI `/docs`

1. Call `POST /sessions` and copy its `session_id`.
2. Call `POST /sessions/{session_id}/uploads` with file metadata. The API
   returns a `file_id` and `upload_url`.
3. PUT the actual file bytes to `upload_url` (for example, with `curl
   --upload-file /absolute/path/to/file.md`). Match the signed `Content-Type`.
4. Call `POST /sessions/{session_id}/uploads/{file_id}/complete` in `/docs`.
   It has no request body. A `202` response means the upload was checked and
   queued.

`/docs` can call the metadata and confirmation endpoints, but it cannot read an
arbitrary path from your computer. Keep the plain basename in the JSON
`filename`; use the actual path only with the command that PUTs file bytes.

## Tests and next step

The tests verify that incomplete or mismatched objects are not queued, a
matching object is queued once, the database status changes only after queue
acceptance, and the SQS adapter publishes IDs rather than file data. They use
temporary SQLite databases and mocked AWS clients, so they do not require a
running Floci instance.

The next component can consume these jobs and begin extracting text from
uploaded documents.
