#!/usr/bin/env bash
#
# Run on YOUR LAPTOP. Copies the project to the server and rebuilds it there.
#
#     ./deploy.sh ubuntu@1.2.3.4 ~/.ssh/key.pem
#
# The alternative, if the code is in git, is to skip this entirely:
#     ssh <host> 'cd edgentrag-v2 && git pull && docker compose up -d --build'
#
# Written for macOS's bash 3.2 and rsync 2.6.9: no arrays, no --info.
set -euo pipefail

HOST="${1:-}"
KEY="${2:-}"
REMOTE="${REMOTE_DIR:-edgentrag-v2}"

if [ -z "$HOST" ]; then
  echo "usage: ./deploy.sh user@host [path/to/key.pem]" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ -n "$KEY" ]; then
  RSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
else
  RSH="ssh -o StrictHostKeyChecking=accept-new"
fi

echo
echo "Copying to $HOST:$REMOTE"

# The frontend is built inside its image, so node_modules and dist stay here.
# .env is excluded: the server's own copy holds its queue URLs and bucket.
rsync -az --delete --stats -e "$RSH" \
  --exclude '.venv' \
  --exclude '**/node_modules' \
  --exclude '**/__pycache__' \
  --exclude '*.pyc' \
  --exclude 'frontend/dist' \
  --exclude '.env' \
  --exclude '.git' \
  ./ "$HOST:$REMOTE/" | tail -4

cat <<EOF

Copied. Now on the box:

  ssh ${KEY:+-i $KEY }$HOST
  cd $REMOTE
  docker compose up -d --build
  docker compose logs cloudflared | grep -o 'https://.*trycloudflare.com'

First time only: cp .env.example .env and fill in the queue URLs
(python -m scripts.bootstrap prints them).

EOF
