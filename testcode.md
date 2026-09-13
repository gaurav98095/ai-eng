# End-to-end API test with Floci

This walkthrough runs the pipeline currently implemented in the backend:

1. Create a chat session.
2. Ask the API for an S3/Floci upload URL.
3. PUT one Markdown file from `app/docs` directly to Floci.
4. Confirm the upload; the API checks S3 metadata and enqueues the file IDs in
   SQS.

The current pipeline ends when the job is queued. A worker to extract text and
build a searchable index will be added in a later component.

To run the full sequence automatically instead, open a terminal at the
repository root and run `./test-run.sh`. The steps below show the individual
requests for learning and troubleshooting.

## 1. Start the API

Open Terminal 1 at the repository root:

~~~bash
cd /Users/gaurav/Desktop/ai-eng
~~~

The local `app/backend/.env` should contain these Floci settings:

~~~dotenv
EDGENTRAG_AWS_REGION=us-east-1
EDGENTRAG_AWS_ENDPOINT_URL=http://localhost:4566
EDGENTRAG_S3_BUCKET=edgentrag-test-1
EDGENTRAG_INGESTION_QUEUE_URL=http://localhost:4566/000000000000/edgentrag-test-queue
~~~

Export Floci's local test credentials in the same terminal, apply database
migrations, and start Uvicorn:

~~~bash
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
.venv/bin/alembic -c app/backend/alembic.ini upgrade head
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

Leave Terminal 1 running. The AWS credentials are only for local Floci use;
don't add them to `.env` or commit them.

## 2. Check that the API is running

In Terminal 2, go to the repository root:

~~~bash
cd /Users/gaurav/Desktop/ai-eng
~~~

Check liveness and database readiness:

~~~bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/ready
~~~

Expect `200 OK` from both. `/ready` should report `"status":"ready"` and the
database check should be `ok`.

## 3. Choose the test document and read its size

This test uploads the existing Markdown tutorial
`app/docs/01-backend-application-shell.md`. These shell commands run from the
repository root and calculate the file's exact byte size:

~~~bash
FILE_PATH="$(pwd)/app/docs/01-backend-application-shell.md"
FILE_NAME="${FILE_PATH##*/}"
FILE_SIZE=$(stat -f%z "$FILE_PATH")
printf 'File: %s (%s bytes)\n' "$FILE_NAME" "$FILE_SIZE"
~~~

`FILE_NAME` is just the basename sent to the API. `FILE_PATH` is the full local
path used by `curl` to read the file bytes.

## 4. Create a session

Call `POST /sessions`:

~~~bash
SESSION_RESPONSE=$(curl -fsS -X POST http://127.0.0.1:8000/sessions)
printf '%s\n' "$SESSION_RESPONSE"
SESSION_ID=$(printf '%s' "$SESSION_RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
printf 'Session ID: %s\n' "$SESSION_ID"
~~~

The response includes the new `session_id`. The commands above save it so the
next requests use the same session automatically.

## 5. Request an upload URL

Send the filename, MIME type, and file size as JSON metadata:

~~~bash
UPLOAD_RESPONSE=$(curl -fsS -X POST \
  "http://127.0.0.1:8000/sessions/$SESSION_ID/uploads" \
  -H 'Content-Type: application/json' \
  -d "{\"files\":[{\"filename\":\"$FILE_NAME\",\"content_type\":\"text/markdown\",\"size_bytes\":$FILE_SIZE}]}" \
)
printf '%s\n' "$UPLOAD_RESPONSE"
FILE_ID=$(printf '%s' "$UPLOAD_RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["targets"][0]["file_id"])')
UPLOAD_URL=$(printf '%s' "$UPLOAD_RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["targets"][0]["upload_url"])')
printf 'File ID: %s\n' "$FILE_ID"
~~~

The response contains a generated `file_id` and a short-lived `upload_url`.
The database stores the file metadata and marks it `awaiting_upload`.

## 6. Upload the actual bytes to Floci

Use the signed URL from the previous response. The `Content-Type` must match
the value used when requesting the URL:

~~~bash
curl -i -X PUT \
  -H 'Content-Type: text/markdown' \
  --upload-file "$FILE_PATH" \
  "$UPLOAD_URL"
~~~

Expect a successful `200` response. This PUT goes directly from your terminal
to Floci; it does not send the file through FastAPI.

## 7. Confirm the upload and enqueue it

Now ask the API to inspect the stored object's size and content type. This
endpoint has no request body:

~~~bash
curl -i -X POST \
  "http://127.0.0.1:8000/sessions/$SESSION_ID/uploads/$FILE_ID/complete"
~~~

Expect `202 Accepted`, with a response similar to:

~~~json
{
  "session_id": "...",
  "file_id": "...",
  "status": "uploaded",
  "ingestion_job_enqueued": true
}
~~~

If the object is missing, confirmation returns `409`. A size or content-type
mismatch returns `422`; those failures do not enqueue a job. A queue problem
returns `503`, which usually means to check the queue URL and Floci connection.

## 8. Verify the message in Floci

Open the Floci dashboard, select SQS queue `edgentrag-test-queue`, and refresh
its details. Its approximate message count should increase. The message body
contains the `session_id`, `file_id`, and schema version—not the document
bytes. No worker currently consumes the queue, so the message should remain
available until you remove or consume it.

## FastAPI `/docs` alternative

You can perform the API steps in `/docs` instead of the API `curl` commands:

- Use `POST /sessions` to create the session.
- Use `POST /sessions/{session_id}/uploads` with
  `filename: "01-backend-application-shell.md"`, `content_type:
  "text/markdown"`, and the size printed in Step 3.
- Use `curl --upload-file` from Step 6 to PUT the local file bytes to the
  returned URL. `/docs` cannot read a path from your computer.
- Use `POST /sessions/{session_id}/uploads/{file_id}/complete` to confirm it.

The same Floci and credentials requirements apply whichever client you use.
