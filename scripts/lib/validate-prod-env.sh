#!/usr/bin/env bash
# Production .env validation — AMI golden / clone (F-DEPLOY-PROD-1).
# Prod NUNCA roda setup-wizard; o .env vem baked na AMI ou do playbook externo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "validate-prod-env: .env ausente — configure na AMI golden ou via playbook externo." >&2
  echo "  Ver docs/reference/workshop-env-contract.md" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. "$ENV_FILE"
set +a

missing=0
for key in DEPLOYMENT_ENVIRONMENT OLLAMA_BASE_URL OLLAMA_CHAT_MODEL; do
  val="${!key:-}"
  if [ -z "$val" ]; then
    echo "validate-prod-env: ${key} ausente ou vazio no .env" >&2
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo "validate-prod-env: corrija o .env e tente novamente." >&2
  exit 1
fi

echo "→ validate-prod-env: OK (${DEPLOYMENT_ENVIRONMENT})"
