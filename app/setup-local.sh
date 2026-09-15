#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker compose --profile local -f "$ROOT_DIR/compose.yaml" up -d --build
docker compose --profile local -f "$ROOT_DIR/compose.yaml" exec -T api alembic upgrade head
echo "Local infrastructure, services, and migrations are ready."
echo "Compose owns Floci, PostgreSQL, and Redis; persistent volumes were retained."
