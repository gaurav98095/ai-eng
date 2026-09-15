#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker compose --profile local -f "$ROOT_DIR/compose.yaml" up -d --build
docker compose --profile local -f "$ROOT_DIR/compose.yaml" exec -T api alembic upgrade head
docker compose --profile local -f "$ROOT_DIR/compose.yaml" exec -T api python scripts/ensure_local_queues.py
echo "Local infrastructure, services, and migrations are ready."
echo "Compose owns Floci, PostgreSQL, and Redis; persistent volumes were retained."
echo "Floci API: http://localhost:4566"
echo "Floci UI:  http://localhost:4500"
