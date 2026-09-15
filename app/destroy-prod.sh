#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TFVARS="${TFVARS_FILE:-$ROOT_DIR/terraform/prod.tfvars}"
[[ -f "$TFVARS" ]] || { echo "Create $TFVARS first" >&2; exit 1; }
[[ "${CONFIRM_DESTROY:-}" == "DESTROY EDGENTRAG PRODUCTION" ]] || {
  echo "Refusing production destruction. Set CONFIRM_DESTROY='DESTROY EDGENTRAG PRODUCTION'." >&2
  exit 1
}
terraform -chdir="$ROOT_DIR/terraform" init
terraform -chdir="$ROOT_DIR/terraform" destroy -var-file="$TFVARS"
