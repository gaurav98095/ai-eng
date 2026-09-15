#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$ROOT_DIR/terraform"
TFVARS="${TFVARS_FILE:-$TF_DIR/prod.tfvars}"

command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
[[ -f "$TFVARS" ]] || { echo "Create $TFVARS from prod.tfvars.example" >&2; exit 1; }

terraform -chdir="$TF_DIR" init
terraform -chdir="$TF_DIR" plan -var-file="$TFVARS" -out=prod.tfplan
terraform -chdir="$TF_DIR" apply prod.tfplan

command -v aws >/dev/null || { echo "aws CLI is required for deployment" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required for image deployment" >&2; exit 1; }
[[ -n "${ECS_CLUSTER:-}" ]] || { echo "Set ECS_CLUSTER" >&2; exit 1; }
[[ -n "${ECS_SUBNETS:-}" ]] || { echo "Set ECS_SUBNETS (comma-separated)" >&2; exit 1; }
[[ -n "${ECS_SECURITY_GROUP:-}" ]] || { echo "Set ECS_SECURITY_GROUP" >&2; exit 1; }

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT_ID.dkr.ecr.${AWS_REGION:-ap-south-1}.amazonaws.com"
aws ecr get-login-password --region "${AWS_REGION:-ap-south-1}" | docker login --username AWS --password-stdin "$REGISTRY"

declare -A DOCKERFILES=(
  [api]="Dockerfile"
  [ingestion]="Dockerfile.ingestion"
  [embedding]="Dockerfile.embedding"
  [chat]="Dockerfile.chat"
  [stt]="Dockerfile.stt"
)
for service in "${!DOCKERFILES[@]}"; do
  image="$REGISTRY/${PROJECT_NAME:-edgentrag}/$service:${IMAGE_TAG:-latest}"
  docker build -f "$ROOT_DIR/backend/${DOCKERFILES[$service]}" -t "$image" "$ROOT_DIR/backend"
  docker push "$image"
done

echo "Images pushed. Run Alembic against the production DATABASE_URL before ECS rollout."
echo "Register/update the task definitions in app/deploy with your IAM role, secret ARNs,"
echo "and the pushed image URIs, then deploy them to ECS_CLUSTER=$ECS_CLUSTER."
