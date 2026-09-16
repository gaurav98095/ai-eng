#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose --profile local -f "$ROOT_DIR/compose.yaml")
"${COMPOSE[@]}" down --remove-orphans
echo "Local Compose containers and networks were destroyed."
echo "Floci was stopped with the Compose stack; persistent volumes were retained."
