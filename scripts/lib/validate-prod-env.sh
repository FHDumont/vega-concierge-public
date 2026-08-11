#!/usr/bin/env bash
# Production .env validation — AMI golden / clone (F-DEPLOY-PROD-1).
# Prod NEVER runs setup-wizard; the .env comes baked into the AMI or from the external playbook.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "validate-prod-env: .env missing — configure it on the golden AMI or via the external playbook." >&2
  echo "  See docs/reference/workshop-env-contract.md" >&2
  exit 1
fi

# OS wins over .env (F-REAL-ENV-2): we validate the EFFECTIVE value, same as runtime.
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/env-load.sh"
load_env_os_first

missing=0
for key in DEPLOYMENT_ENVIRONMENT OLLAMA_BASE_URL OLLAMA_CHAT_MODEL; do
  val="${!key:-}"
  if [ -z "$val" ]; then
    echo "validate-prod-env: ${key} missing or empty in .env" >&2
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo "validate-prod-env: fix .env and try again." >&2
  exit 1
fi

case "${OLLAMA_BASE_URL:-}" in
  *127.0.0.1*|*localhost*)
    echo "validate-prod-env: OLLAMA_BASE_URL=${OLLAMA_BASE_URL} is a dev URL (host)." >&2
    echo "  For ./scripts/up.sh use: OLLAMA_BASE_URL=http://host.docker.internal:11434" >&2
    echo "  (compose.plain.yml now interpolates OLLAMA_BASE_URL from .env — without a fix, the container points at itself.)" >&2
    exit 1
    ;;
esac

echo "→ validate-prod-env: OK (${DEPLOYMENT_ENVIRONMENT})"
