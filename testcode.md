# End-to-end API test with Floci

This walkthrough runs the pipeline currently implemented in the backend:

1. Create a chat session.
2. Ask the API for an S3/Floci upload URL.
3. PUT one Markdown file from `app/docs` directly to Floci.
4. Confirm the upload; the API checks S3 metadata and enqueues the file IDs in
   SQS.
5. Run the worker to validate the text, persist chunks, and mark the file
   ready.

The current pipeline ends after storing text chunks. Embeddings and searchable
vector retrieval will be added in later components.

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

## 8. Run the ingestion worker

Open Terminal 3 at the repository root. Export the local Floci credentials and
start the separate worker process:

~~~bash
cd /Users/gaurav/Desktop/ai-eng
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
.venv/bin/python -m edgentrag.ingestion.worker
~~~

The worker long-polls `edgentrag-test-queue`. For a valid Markdown file it
downloads the bytes, checks UTF-8 and content type, stores extracted text in
`document_chunks`, marks the file and session `ready`, and removes the SQS
message after the database commit. Its terminal should log the file ID and
chunk count. Stop it with Ctrl+C when you are done.

PDF, Office, audio, and video parsers are not implemented yet; upload targets
currently accept only `.md` and `.txt` files.

## 9. Search the ingested document

To include embeddings and search, configure
`EDGENTRAG_EMBEDDING_SERVICE_URL` and `EDGENTRAG_EMBEDDING_API_TOKEN` for both
the API and worker **before starting them and ingesting the file**. Use the
current Colab tunnel URL and shared token as described in
[Component 10](app/docs/10-semantic-search.md). Keep Colab running.

Once the worker has saved embeddings and marked the file ready:

~~~bash
curl -i -X POST "http://127.0.0.1:8000/sessions/$SESSION_ID/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"How is the FastAPI application started?","top_k":3}'
~~~

Expect `200` with `matches` containing source text, filenames, chunk indices,
and cosine scores. `409` means there are no ready compatible embeddings; older
text-only documents need a fresh upload with embeddings enabled. `503` means
to check the main API's embedding configuration and the running Colab service.

## FastAPI `/docs` alternative

You can perform the API steps in `/docs` instead of the API `curl` commands:

- Use `POST /sessions` to create the session.
- Use `POST /sessions/{session_id}/uploads` with
  `filename: "01-backend-application-shell.md"`, `content_type:
  "text/markdown"`, and the size printed in Step 3.
- Use `curl --upload-file` from Step 6 to PUT the local file bytes to the
  returned URL. `/docs` cannot read a path from your computer.
- Use `POST /sessions/{session_id}/uploads/{file_id}/complete` to confirm it.
- After ingestion finishes with embeddings enabled, use
  `POST /sessions/{session_id}/search` with a question and `top_k`.

The same Floci and credentials requirements apply whichever client you use.
