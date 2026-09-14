#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON="$PROJECT_ROOT/.venv/bin/python"
ALEMBIC="$PROJECT_ROOT/.venv/bin/alembic"
API_HOST="127.0.0.1"
API_PORT="8010"
API_BASE_URL="http://${API_HOST}:${API_PORT}"
FILE_PATH="$PROJECT_ROOT/app/docs/01-backend-application-shell.md"
FILE_NAME="${FILE_PATH##*/}"
API_LOG="$(mktemp)"
WORKER_LOG="$(mktemp)"
API_PID=""
WORKER_PID=""

cleanup() {
  if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
  rm -f "$API_LOG"
  rm -f "$WORKER_LOG"
}
trap cleanup EXIT

if [[ ! -x "$PYTHON" || ! -x "$ALEMBIC" ]]; then
  echo "The project virtual environment is missing. Create it and install app/backend first." >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/app/backend/.env" ]]; then
  echo "Missing app/backend/.env. Copy it from app/backend/.env.example first." >&2
  exit 1
fi

if [[ ! -f "$FILE_PATH" ]]; then
  echo "Test Markdown file not found: $FILE_PATH" >&2
  exit 1
fi

echo "Checking local Floci settings..."
"$PYTHON" -c '
from edgentrag.core.config import Settings

settings = Settings()
expected = {
    "aws_endpoint_url": "http://localhost:4566",
    "aws_region": "us-east-1",
    "s3_bucket": "edgentrag-test-1",
    "ingestion_queue_url": "http://localhost:4566/000000000000/edgentrag-test-queue",
}
actual = {name: getattr(settings, name) for name in expected}
if actual != expected:
    raise SystemExit(f"Floci settings do not match this local test: {actual!r}")
print("Floci bucket and SQS queue settings look good.")
'

if ! curl --connect-timeout 2 --max-time 4 -sS -o /dev/null \
  "http://localhost:4566"; then
  echo "Floci is not reachable at http://localhost:4566. Check its Docker port mapping." >&2
  exit 1
fi

if curl --connect-timeout 1 --max-time 2 -fsS \
  "${API_BASE_URL}/health" >/dev/null 2>&1; then
  echo "Port ${API_PORT} is already serving an API. Stop it and rerun this script." >&2
  exit 1
fi

# These dummy credentials are used only by the boto3 clients targeting Floci.
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1

echo "Applying database migrations..."
"$ALEMBIC" -c app/backend/alembic.ini upgrade head

echo "Starting a temporary API on ${API_BASE_URL}..."
"$PYTHON" -m uvicorn edgentrag.api.app:app \
  --host "$API_HOST" \
  --port "$API_PORT" >"$API_LOG" 2>&1 &
API_PID=$!

API_READY="false"
for attempt in {1..30}; do
  if curl --connect-timeout 1 --max-time 2 -fsS \
    "${API_BASE_URL}/ready" >/dev/null 2>&1; then
    API_READY="true"
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [[ "$API_READY" != "true" ]]; then
  echo "The API did not become ready. Server output:" >&2
  cat "$API_LOG" >&2
  exit 1
fi

echo "Starting the ingestion worker..."
"$PYTHON" -m edgentrag.ingestion.worker >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!

if [[ "$(uname -s)" == "Darwin" ]]; then
  FILE_SIZE="$(stat -f%z "$FILE_PATH")"
else
  FILE_SIZE="$(stat -c%s "$FILE_PATH")"
fi

echo "Creating a session..."
SESSION_RESPONSE="$(curl -fsS -X POST "${API_BASE_URL}/sessions")"
SESSION_ID="$(printf '%s' "$SESSION_RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
printf 'Session ID: %s\n' "$SESSION_ID"

echo "Requesting a signed upload URL for ${FILE_NAME} (${FILE_SIZE} bytes)..."
UPLOAD_REQUEST="$("$PYTHON" -c 'import json,sys; print(json.dumps({"files":[{"filename":sys.argv[1],"content_type":"text/markdown","size_bytes":int(sys.argv[2])}]}))' "$FILE_NAME" "$FILE_SIZE")"
UPLOAD_RESPONSE="$(curl -fsS -X POST \
  "${API_BASE_URL}/sessions/${SESSION_ID}/uploads" \
  -H 'Content-Type: application/json' \
  -d "$UPLOAD_REQUEST")"
FILE_ID="$(printf '%s' "$UPLOAD_RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["targets"][0]["file_id"])')"
UPLOAD_URL="$(printf '%s' "$UPLOAD_RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["targets"][0]["upload_url"])')"

echo "Uploading the file bytes directly to Floci..."
PUT_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT \
  -H 'Content-Type: text/markdown' \
  --upload-file "$FILE_PATH" \
  "$UPLOAD_URL")"
case "$PUT_STATUS" in
  2??) printf 'Floci accepted the upload (HTTP %s).\n' "$PUT_STATUS" ;;
  *) echo "Floci upload failed with HTTP ${PUT_STATUS}." >&2; exit 1 ;;
esac

echo "Confirming the upload and enqueueing ingestion..."
COMPLETE_RESPONSE="$(curl -fsS -X POST \
  "${API_BASE_URL}/sessions/${SESSION_ID}/uploads/${FILE_ID}/complete")"
printf '%s\n' "$COMPLETE_RESPONSE"
"$PYTHON" -c '
import json,sys
result=json.load(sys.stdin)
if result.get("status") != "uploaded" or result.get("ingestion_job_enqueued") is not True:
    raise SystemExit("Upload confirmation response did not indicate a queued job")
' <<<"$COMPLETE_RESPONSE"

echo "Waiting for the worker to persist extracted text chunks..."
WORKER_RESULT=""
for attempt in {1..30}; do
  WORKER_RESULT="$("$PYTHON" -c '
import asyncio,sys
from sqlalchemy import func,select
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.sessions.models import SessionFile

async def main():
    database=Database(Settings().database_url)
    async with database.sessions() as session:
        status=await session.scalar(select(SessionFile.status).where(SessionFile.id==sys.argv[1]))
        count=await session.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.session_file_id==sys.argv[1]))
    await database.dispose()
    print("{}:{}".format(status or "missing", count or 0))

asyncio.run(main())
' "$FILE_ID")"
  case "$WORKER_RESULT" in
    ready:*) break ;;
    failed:*) echo "Worker marked the file failed. Worker output:" >&2; cat "$WORKER_LOG" >&2; exit 1 ;;
  esac
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    echo "The ingestion worker stopped unexpectedly. Worker output:" >&2
    cat "$WORKER_LOG" >&2
    exit 1
  fi
  sleep 1
done

if [[ "$WORKER_RESULT" != ready:* ]]; then
  echo "The worker did not finish within 30 seconds. Worker output:" >&2
  cat "$WORKER_LOG" >&2
  exit 1
fi

CHUNK_COUNT="${WORKER_RESULT#ready:}"
echo "Success: the document was uploaded, verified, and processed into ${CHUNK_COUNT} chunk(s)."
echo "Session: ${SESSION_ID}"
echo "File: ${FILE_ID} (${FILE_NAME})"
echo "The temporary API and worker will now shut down."
