# Component 5: direct file uploads to private S3

This component implements the first half of uploading a file:

1. The browser asks the API for permission to upload.
2. The API validates metadata, records a pending file, and signs one S3 PUT.
3. The browser uses that URL to send bytes directly to S3.

We implement steps 1 and 2 here. The following component will register
completed uploads and start background work.

~~~text
Browser ──JSON metadata──> POST /sessions/{id}/uploads ──> API
Browser <──presigned URL── API
Browser ─────file bytes directly with HTTP PUT──────────> private S3 bucket
~~~

The API never buffers large file contents. This keeps memory use stable and
avoids making a large file pass through the EC2 web process.

## Request contract

After creating a session, request one or more upload targets:

~~~json
{
  "files": [
    {
      "filename": "notes.pdf",
      "content_type": "application/pdf",
      "size_bytes": 4096
    }
  ]
}
~~~

The API limits each request to 1–20 files, rejects path-like filenames and
unsupported extensions, and compares the declared size with the configured
maximum. It stores the filename for display, but constructs the S3 object key
from server-generated IDs. User-provided names never become storage paths.

The declared size and MIME type come from the client and are not proof of what
the bytes contain. They are useful for limits and signing; the next component
will verify the resulting S3 object before processing it.

## What is persisted

The session_files table stores:

- which session owns the upload;
- the display filename, declared MIME type, and declared byte size;
- the private S3 object key;
- a lifecycle status, initially awaiting_upload;
- when the record was created.

The object key and the presigned URL are different: the key names the object
inside S3; the signed URL is temporary authority to PUT that object. The URL
is returned once and is not stored in the database.

Alembic revision 0002 creates the table and its foreign key to chat_sessions.
Apply it before running the new route:

~~~bash
.venv/bin/alembic -c app/backend/alembic.ini upgrade head
~~~

## S3 adapter boundary

Routes depend on the small ObjectStorage protocol, not on boto3 directly. The
S3 adapter implements that protocol and lazily creates its SDK client. This
keeps credential setup out of application startup and lets tests replace
storage with an in-memory fake.

The URL is signed for:

- exactly one bucket and generated object key;
- the HTTP PUT operation;
- the requested Content-Type header;
- a short expiry, 900 seconds by default.

The browser must send the same Content-Type when it performs the PUT or S3 will
reject the signature. The adapter uses boto3's default credential chain:

- locally, configure AWS SSO/profile credentials if you want real S3 URLs;
- on EC2, attach an IAM role with s3:PutObject on only this bucket's objects;
- never put AWS access keys in the application .env or send them to Colab.

Without a bucket or valid credentials, target creation returns a safe 503
message. The API does not reveal SDK exception details to the browser.

### Use Floci for local S3 development

The example environment file points the S3 adapter at Floci on
`http://localhost:4566`, with the development bucket `edgentrag-test-1` in
`us-east-1` (Northern Virginia). In a terminal, start Floci and load its local
AWS credentials before starting the API:

~~~bash
floci start
eval "$(floci env)"
~~~

Set `EDGENTRAG_S3_BUCKET=edgentrag-test-1` and
`EDGENTRAG_AWS_REGION=us-east-1` in `app/backend/.env`. The adapter switches
to path-style S3 URLs for the emulator, which
keeps presigned URLs addressable through its single local endpoint. When the
API runs inside a container, use a Floci hostname reachable from that
container instead of `localhost` (for example, the host gateway or the Floci
service name). For real AWS, leave `EDGENTRAG_AWS_ENDPOINT_URL` unset and
configure normal AWS credentials and permissions.

If the bucket has not been created yet, run:

~~~bash
aws s3 mb s3://edgentrag-test-1 --region us-east-1
~~~

## S3 CORS is separate from signing

The browser's PUT request goes from the app's HTTPS origin to S3, so the bucket
must allow that origin through CORS. For a stable domain, allow only that
origin. A temporary Cloudflare quick-tunnel hostname changes when the tunnel
restarts; for short-lived testing the bucket may allow wildcard origin, but
avoid that for a public app. CORS is not a substitute for bucket permissions:
the bucket remains private, and the presigned URL grants only the single
signed operation.

## Try the endpoint

Create a session and copy its returned session_id:

~~~bash
curl -s -X POST http://127.0.0.1:8000/sessions
~~~

Then request a target, replacing SESSION_ID:

~~~bash
curl -X POST http://127.0.0.1:8000/sessions/SESSION_ID/uploads \
  -H 'Content-Type: application/json' \
  -d '{"files":[{"filename":"notes.pdf","content_type":"application/pdf","size_bytes":4096}]}'
~~~

The response includes file_id, the temporary upload_url, and expires_in. To
use it, HTTP PUT the file bytes to upload_url with a Content-Type header that
matches the request. The integration tests use a fake storage adapter, so they
never need AWS credentials or a real bucket.

## Tests and next step

The tests check successful signing and persistence, rejection of unsupported
extensions, rejection of files over the configured size, and the exact
parameters sent to boto3. They run against temporary SQLite databases.

The next component will add an endpoint to confirm an upload landed, verify
its object metadata, and place an ingestion job on a queue.
