#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
if ! curl -fsS --max-time 3 http://localhost:4566/_floci/health >/dev/null; then
  echo "Floci not found at http://localhost:4566. Start it first with:" >&2
  echo "docker run --rm -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock \\" >&2
  echo "  -e FLOCI_SECURITY_EXTRA_CORS_ALLOWED_ORIGINS=http://localhost:5173 floci/floci:latest" >&2
  exit 1
fi
docker compose --profile local -f "$ROOT_DIR/compose.yaml" up -d --build --force-recreate
docker compose --profile local -f "$ROOT_DIR/compose.yaml" exec -T api alembic upgrade head
docker compose --profile local -f "$ROOT_DIR/compose.yaml" exec -T api python scripts/ensure_local_queues.py
echo "Local infrastructure, services, and migrations are ready."
echo "External Floci, PostgreSQL, Redis, services, and migrations are ready."
echo "Floci API: http://localhost:4566"
echo "EdgentRAG frontend: http://localhost:5173"
