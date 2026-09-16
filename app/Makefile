.PHONY: infra-local infra-prod infra-prod-plan destroy-local destroy-prod migrate-local migrate-prod

COMPOSE = docker compose --profile local -f compose.yaml

infra-local:
	./setup-local.sh

infra-prod:
	./setup-prod.sh

infra-prod-plan:
	TFVARS_FILE=$${TFVARS_FILE:-terraform/prod.tfvars} terraform -chdir=terraform init
	terraform -chdir=terraform plan -var-file=$${TFVARS_FILE:-prod.tfvars}

destroy-local:
	./destroy-local.sh

destroy-prod:
	./destroy-prod.sh

migrate-local:
	$(COMPOSE) exec -T api alembic upgrade head

migrate-prod:
	@test -n "$${EDGENTRAG_DATABASE_URL:-}" || (echo "EDGENTRAG_DATABASE_URL is required" && exit 1)
	cd backend && alembic upgrade head
