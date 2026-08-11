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

# SO vence .env (F-REAL-ENV-2): validamos o valor EFETIVO, igual ao runtime.
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/env-load.sh"
load_env_os_first

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

case "${OLLAMA_BASE_URL:-}" in
  *127.0.0.1*|*localhost*)
    echo "validate-prod-env: OLLAMA_BASE_URL=${OLLAMA_BASE_URL} é URL de dev (host)." >&2
    echo "  Para ./scripts/up.sh use: OLLAMA_BASE_URL=http://host.docker.internal:11434" >&2
    echo "  (compose.plain.yml agora interpola OLLAMA_BASE_URL do .env — sem correção, o container aponta p/ si mesmo.)" >&2
    exit 1
    ;;
esac

echo "→ validate-prod-env: OK (${DEPLOYMENT_ENVIRONMENT})"
