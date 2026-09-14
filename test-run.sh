#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

#
## Calling local Python and Alembic for the test environment
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

# Use color only for interactive terminals, and honor the standard NO_COLOR flag.
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  COLOR_RESET=$'\033[0m'
  COLOR_BOLD=$'\033[1m'
  COLOR_CYAN=$'\033[36m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
else
  COLOR_RESET=""
  COLOR_BOLD=""
  COLOR_CYAN=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
fi

step() {
  printf '\n%s%s==> %s%s\n' "$COLOR_BOLD" "$COLOR_CYAN" "$1" "$COLOR_RESET"
}

success() {
  printf '  %s✓%s %s\n' "$COLOR_GREEN" "$COLOR_RESET" "$1"
}

info() {
  printf '  %s•%s %s\n' "$COLOR_YELLOW" "$COLOR_RESET" "$1"
}

#
## Calling the cleanup handler for stopping test processes
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

#
## Calling local checks for required files and tools
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

#
## Calling Python settings validation for local Floci configuration
step "Check local Floci settings"
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

#
## Calling Python settings resolution for embedding and generation services
# Use the same effective settings as the worker, including app/backend/.env.
PIPELINE_SETTINGS="$("$PYTHON" -c '
import math,sys
from pathlib import Path
from edgentrag.core.config import Settings
from edgentrag.ingestion.extraction import chunk_text

settings=Settings()
enabled=settings.embedding_service_url is not None
batches=math.ceil(len(chunk_text(Path(sys.argv[1]).read_text())) / settings.embedding_batch_size)
worker_budget=math.ceil(batches * settings.embedding_request_timeout_seconds + 60) if enabled else 30
print(
    int(enabled),
    worker_budget,
    math.ceil(settings.embedding_request_timeout_seconds + 10),
    str(settings.generation_service_url or ""),
)
' "$FILE_PATH")"
read -r EMBEDDING_ENABLED DEFAULT_WORKER_WAIT_SECONDS SEARCH_WAIT_SECONDS GENERATION_SERVICE_URL <<<"$PIPELINE_SETTINGS"
WORKER_WAIT_SECONDS="${TEST_WORKER_TIMEOUT_SECONDS:-$DEFAULT_WORKER_WAIT_SECONDS}"
if [[ ! "$WORKER_WAIT_SECONDS" =~ ^[1-9][0-9]{0,5}$ ]]; then
  echo "TEST_WORKER_TIMEOUT_SECONDS must be a positive integer of at most six digits." >&2
  exit 1
fi
GENERATION_WAIT_SECONDS="${TEST_GENERATION_TIMEOUT_SECONDS:-600}"
if [[ ! "$GENERATION_WAIT_SECONDS" =~ ^[1-9][0-9]{0,5}$ ]]; then
  echo "TEST_GENERATION_TIMEOUT_SECONDS must be a positive integer of at most six digits." >&2
  exit 1
fi

#
## Calling curl for Floci and test-port availability checks
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

#
## Calling boto3 with local test credentials for Floci
# These dummy credentials are used only by the boto3 clients targeting Floci.
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1

#
## Calling Alembic for database migrations
step "Apply database migrations"
"$ALEMBIC" -c app/backend/alembic.ini upgrade head

#
## Calling Uvicorn for the temporary local API
step "Start temporary API at ${API_BASE_URL}"
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

#
## Calling the ingestion worker for queued document processing
step "Start ingestion worker"
if [[ "$EMBEDDING_ENABLED" == "1" ]]; then
  info "Chunk embeddings use the selected remote service."
else
  info "No embedding service configured; testing text-only ingestion."
fi
"$PYTHON" -m edgentrag.ingestion.worker >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!

if [[ "$(uname -s)" == "Darwin" ]]; then
  FILE_SIZE="$(stat -f%z "$FILE_PATH")"
else
  FILE_SIZE="$(stat -c%s "$FILE_PATH")"
fi

#
## Calling the sessions API for a test session
step "Create test session"
SESSION_RESPONSE="$(curl -fsS -X POST "${API_BASE_URL}/sessions")"
SESSION_ID="$(printf '%s' "$SESSION_RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')"
printf 'Session ID: %s\n' "$SESSION_ID"

#
## Calling the uploads API for a signed Floci upload URL
step "Request signed upload URL for ${FILE_NAME} (${FILE_SIZE} bytes)"
UPLOAD_REQUEST="$("$PYTHON" -c 'import json,sys; print(json.dumps({"files":[{"filename":sys.argv[1],"content_type":"text/markdown","size_bytes":int(sys.argv[2])}]}))' "$FILE_NAME" "$FILE_SIZE")"
UPLOAD_RESPONSE="$(curl -fsS -X POST \
  "${API_BASE_URL}/sessions/${SESSION_ID}/uploads" \
  -H 'Content-Type: application/json' \
  -d "$UPLOAD_REQUEST")"
FILE_ID="$(printf '%s' "$UPLOAD_RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["targets"][0]["file_id"])')"
UPLOAD_URL="$(printf '%s' "$UPLOAD_RESPONSE" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["targets"][0]["upload_url"])')"

#
## Calling Floci for the document byte upload
step "Upload file bytes to Floci"
PUT_STATUS="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT \
  -H 'Content-Type: text/markdown' \
  --upload-file "$FILE_PATH" \
  "$UPLOAD_URL")"
case "$PUT_STATUS" in
  2??) success "Floci accepted the upload (HTTP ${PUT_STATUS})." ;;
  *) echo "Floci upload failed with HTTP ${PUT_STATUS}." >&2; exit 1 ;;
esac

#
## Calling the uploads API for upload confirmation and ingestion enqueueing
step "Confirm upload and enqueue ingestion"
COMPLETE_RESPONSE="$(curl -fsS -X POST \
  "${API_BASE_URL}/sessions/${SESSION_ID}/uploads/${FILE_ID}/complete")"
printf '%s\n' "$COMPLETE_RESPONSE"
"$PYTHON" -c '
import json,sys
result=json.load(sys.stdin)
if result.get("status") != "uploaded" or result.get("ingestion_job_enqueued") is not True:
    raise SystemExit("Upload confirmation response did not indicate a queued job")
' <<<"$COMPLETE_RESPONSE"

#
## Calling the database for ingestion status and persisted chunk counts
step "Wait up to ${WORKER_WAIT_SECONDS} seconds for ingestion"
WORKER_RESULT=""
WORKER_DEADLINE=$((SECONDS + WORKER_WAIT_SECONDS))
LAST_WORKER_RESULT=""
while (( SECONDS < WORKER_DEADLINE )); do
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
        embedded=await session.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.session_file_id==sys.argv[1], DocumentChunk.embedding_model.is_not(None), DocumentChunk.embedding.is_not(None)))
    await database.dispose()
    print("{}:{}:{}".format(status or "missing", count or 0, embedded or 0))

asyncio.run(main())
' "$FILE_ID")"
  if [[ "$WORKER_RESULT" != "$LAST_WORKER_RESULT" ]]; then
    info "Ingestion status ${WORKER_RESULT} (status:chunks:embeddings)."
    LAST_WORKER_RESULT="$WORKER_RESULT"
  fi
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
  echo "The worker did not finish within ${WORKER_WAIT_SECONDS} seconds. Worker output:" >&2
  cat "$WORKER_LOG" >&2
  exit 1
fi

#
## Calling Python checks for completed chunk and embedding persistence
CHUNK_COUNT="${WORKER_RESULT#ready:}"
EMBEDDED_COUNT="${CHUNK_COUNT#*:}"
CHUNK_COUNT="${CHUNK_COUNT%%:*}"
if [[ "$CHUNK_COUNT" == "0" ]]; then
  echo "The worker returned ready without persisting any chunks." >&2
  exit 1
fi
if [[ "$EMBEDDING_ENABLED" == "1" && "$EMBEDDED_COUNT" != "$CHUNK_COUNT" ]]; then
  echo "Expected an embedding for each chunk, but got ${EMBEDDED_COUNT}/${CHUNK_COUNT}. Worker output:" >&2
  cat "$WORKER_LOG" >&2
  exit 1
fi
#
## Calling the local search API for semantic retrieval verification
success "Document uploaded and processed into ${CHUNK_COUNT} chunk(s)."
if [[ "$EMBEDDING_ENABLED" == "1" ]]; then
  info "Persisted embeddings: ${EMBEDDED_COUNT}/${CHUNK_COUNT}."
  step "Search the uploaded document"
  SEARCH_RESPONSE="$(curl --connect-timeout 5 --max-time "$SEARCH_WAIT_SECONDS" -fsS \
    -X POST "${API_BASE_URL}/sessions/${SESSION_ID}/search" \
    -H 'Content-Type: application/json' \
    -d '{"query":"How is the FastAPI application started?","top_k":3}')"
  "$PYTHON" -c '
import json,sys
result=json.load(sys.stdin)
matches=result.get("matches", [])
if result.get("session_id") != sys.argv[1] or not matches:
    raise SystemExit("Search did not return matches for the test session")
if any(match["file_id"] != sys.argv[2] for match in matches):
    raise SystemExit("Search returned a file outside the test upload")
for match in matches:
    print("  Match: {} chunk {} (score {:.3f})".format(match["filename"], match["chunk_index"], match["score"]))
' "$SESSION_ID" "$FILE_ID" <<<"$SEARCH_RESPONSE"
else
  info "Search skipped; configure an embedding URL and token to test retrieval."
fi

#
## Calling the selected Colab or Lightning generation API for /generate verification
if [[ -n "$GENERATION_SERVICE_URL" ]]; then
  if [[ -z "${EDGENTRAG_GENERATION_API_TOKEN:-}" ]]; then
    printf '\n%sGeneration is configured, but EDGENTRAG_GENERATION_API_TOKEN is not set.%s\n' \
      "$COLOR_YELLOW" "$COLOR_RESET" >&2
    printf 'Export the token used by the selected Colab or Lightning service and rerun:\n' >&2
    printf "  export EDGENTRAG_GENERATION_API_TOKEN='your-token'\n" >&2
    exit 1
  fi
  step "Call generation service"
  if ! GENERATION_HTTP_RESULT="$(curl --connect-timeout 5 \
    --max-time "$GENERATION_WAIT_SECONDS" \
    -sS -w $'\n%{http_code}' \
    -X POST "${GENERATION_SERVICE_URL%/}/generate" \
    -H "Authorization: Bearer ${EDGENTRAG_GENERATION_API_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d '{"instructions":"Answer briefly and naturally.","prompt":"Reply with a short greeting.","max_new_tokens":32}')"; then
    printf '\n%sCould not reach the generation service at %s.%s\n' \
      "$COLOR_YELLOW" "$GENERATION_SERVICE_URL" "$COLOR_RESET" >&2
    exit 1
  fi
  GENERATION_HTTP_STATUS="${GENERATION_HTTP_RESULT##*$'\n'}"
  GENERATION_RESPONSE="${GENERATION_HTTP_RESULT%$'\n'*}"
  if [[ ! "$GENERATION_HTTP_STATUS" =~ ^2[0-9][0-9]$ ]]; then
    printf '\n%sGeneration request failed with HTTP %s:%s\n' \
      "$COLOR_YELLOW" "$GENERATION_HTTP_STATUS" "$COLOR_RESET" >&2
    printf '%s\n' "$GENERATION_RESPONSE" >&2
    exit 1
  fi
  success "Generation service returned a response."
  "$PYTHON" -c '
import json,sys
result=json.loads(sys.stdin.read())
for field in ("model", "content", "input_tokens", "output_tokens"):
    if field not in result:
        raise SystemExit("Generation response is missing {!r}".format(field))
if not isinstance(result["content"], str) or not result["content"].strip():
    raise SystemExit("Generation service returned empty content")
if not isinstance(result["input_tokens"], int) or not isinstance(result["output_tokens"], int):
    raise SystemExit("Generation response token counts are not integers")
print("  Model: {} ({} output tokens)".format(
    result["model"], result["output_tokens"]
))
print("  Generated text: {}".format(result["content"]))
' <<<"$GENERATION_RESPONSE"
else
  info "Generation skipped; configure a generation URL to test /generate."
fi

#
## Calling the cleanup trap after reporting the test results
printf '\nSession: %s\nFile: %s (%s)\n' "$SESSION_ID" "$FILE_ID" "$FILE_NAME"
success "Temporary API and worker will now shut down."
