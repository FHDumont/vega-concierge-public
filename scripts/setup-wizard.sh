#!/usr/bin/env bash
# Vega Concierge — first-run setup wizard (dev + compose only).
# Production/AMI uses a baked .env — see scripts/lib/validate-prod-env.sh.
#
# Usage:
#   ./scripts/setup-wizard.sh                    # interactive mode picker
#   ./scripts/setup-wizard.sh --mode dev         # dev (local hot reload)
#   ./scripts/setup-wizard.sh --mode compose     # docker compose --build
#   ./scripts/setup-wizard.sh --if-needed        # skip when .env is complete
#   ./scripts/setup-wizard.sh --force            # always re-run
#   ./scripts/setup-wizard.sh --non-interactive  # auto-fill non-secret defaults only
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"

# shellcheck source=lib/wizard-ui.sh
source "$ROOT/scripts/lib/wizard-ui.sh"

MODE=""
IF_NEEDED=0
FORCE=0
NON_INTERACTIVE=0

usage() {
  cat <<'EOF'
usage: setup-wizard.sh [options]

Options:
  --mode dev|compose        Target startup mode (skip mode picker)
  --if-needed               Exit immediately when .env is already complete
  --force                   Re-run even when .env looks complete
  --non-interactive         Auto-fill defaults only; skip secret prompts
  -h, --help                Show this help

Production (AMI/EC2) does NOT use this wizard — configure .env on the golden AMI
or via the external playbook. See docs/reference/workshop-env-contract.md.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --if-needed)
      IF_NEEDED=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$MODE" ]; then
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    MODE="dev"
  else
    wiz_blank
    echo "Vega Concierge — setup wizard"
    echo "=============================="
    wiz_blank
    echo "How will you run Vega?"
    echo "  1) dev      — local hot reload (./scripts/dev.sh)"
    echo "  2) compose  — Docker with local build (./scripts/up.sh --build)"
    wiz_blank
    wiz_prompt MODE "Choose mode (dev/compose)" "dev"
  fi
fi

case "$MODE" in
  dev|compose) ;;
  prod)
    echo "setup-wizard: mode 'prod' removed — use validate-prod-env.sh + baked .env on AMI." >&2
    echo "  See docs/reference/workshop-env-contract.md" >&2
    exit 2
    ;;
  *)
    echo "invalid mode: $MODE (expected dev or compose)" >&2
    exit 2
    ;;
esac

if [ "$IF_NEEDED" -eq 1 ] && [ "$FORCE" -eq 0 ]; then
  if wiz_is_complete "$MODE" "$ENV_FILE"; then
    exit 0
  fi
fi

# Seed from existing .env when re-running.
wiz_load_env "$ENV_FILE"

preflight_dev() {
  local missing=0
  for cmd in python3 node npm; do
    if ! wiz_command_exists "$cmd"; then
      echo "  missing: $cmd" >&2
      missing=1
    fi
  done
  [ "$missing" -eq 0 ] || { echo "Install the missing tools and re-run the wizard." >&2; exit 1; }
}

preflight_docker() {
  local missing=0
  if ! wiz_command_exists docker; then
    echo "  missing: docker" >&2
    missing=1
  elif ! docker compose version >/dev/null 2>&1; then
    echo "  missing: docker compose plugin" >&2
    missing=1
  fi
  [ "$missing" -eq 0 ] || { echo "Install Docker + compose plugin and re-run the wizard." >&2; exit 1; }
}

run_wizard() {
  local mode="$1"

  wiz_blank
  echo "Vega Concierge — First-time setup ($mode mode)"
  echo "=============================================="
  wiz_blank
  echo "This wizard creates a .env file with the settings needed to start Vega."
  echo "Secrets are stored in .env (gitignored). Re-run anytime:"
  echo "  ./scripts/setup-wizard.sh --force"
  wiz_blank

  wiz_section "Preflight checks" "Verifying required tools are available."
  case "$mode" in
    dev) preflight_dev ;;
    compose) preflight_docker ;;
  esac
  echo "All required tools are available."

  # --- deployment identity ---
  wiz_section "Deployment identity" \
    "A short label for this instance (DEPLOYMENT_ENVIRONMENT)."

  local dep_default dep_existing
  dep_existing="$(wiz_read_env_file DEPLOYMENT_ENVIRONMENT "$ENV_FILE" 2>/dev/null || true)"
  dep_default="${dep_existing:-$(wiz_sanitize_hostname "$(hostname -s 2>/dev/null || echo LOCAL)")}"
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    wiz_write_env DEPLOYMENT_ENVIRONMENT "$dep_default"
  else
    local dep_input="$dep_default"
    wiz_prompt dep_input "Environment name" "$dep_default"
    wiz_write_env DEPLOYMENT_ENVIRONMENT "$dep_input"
  fi

  # --- Ollama (LLM default — F-REAL-ENV-1) ---
  wiz_section "Ollama (default LLM)" \
    "On first boot Vega seeds 'Ollama Local' from these settings (idempotent).
Other providers (OpenAI, Anthropic, Bedrock) are added in Admin → Config — not in .env."

  local ollama_base ollama_model ollama_base_default
  case "$mode" in
    dev) ollama_base_default="http://127.0.0.1:11434" ;;
    compose) ollama_base_default="http://host.docker.internal:11434" ;;
  esac
  ollama_base="$(wiz_read_env_file OLLAMA_BASE_URL "$ENV_FILE" 2>/dev/null || echo "$ollama_base_default")"
  ollama_model="$(wiz_read_env_file OLLAMA_CHAT_MODEL "$ENV_FILE" 2>/dev/null || echo llama3.2)"

  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    wiz_write_env OLLAMA_BASE_URL "${ollama_base:-$ollama_base_default}"
    wiz_write_env OLLAMA_CHAT_MODEL "${ollama_model:-llama3.2}"
  else
    wiz_prompt ollama_base "Ollama base URL" "${ollama_base:-$ollama_base_default}"
    wiz_write_env OLLAMA_BASE_URL "$ollama_base"
    wiz_prompt ollama_model "Ollama chat model" "${ollama_model:-llama3.2}"
    wiz_write_env OLLAMA_CHAT_MODEL "$ollama_model"
  fi

  # --- mode-specific ---
  case "$mode" in
    dev)
      wiz_section "Development settings" \
        "Local hot reload."
      wiz_write_env PUBLIC_API_BASE "http://localhost:8000"
      wiz_write_env API_INTERNAL_URL "http://localhost:8000"
      ;;
    compose)
      wiz_section "Docker Compose settings" \
        "Local Docker stack with published ports on localhost."
      wiz_write_env PUBLIC_API_BASE "http://localhost:8000"
      wiz_write_env API_INTERNAL_URL "http://backend:8000"
      ;;
  esac

  # --- summary ---
  wiz_section "Summary" "The following will be written to .env:"
  local i key val display
  for i in "${!WIZ_ENV_KEYS[@]}"; do
    key="${WIZ_ENV_KEYS[$i]}"
    val="${WIZ_ENV_VALS[$i]}"
    case "$key" in
      ENROLL_TOKEN|OWNER_PASSWORD|CONTROL_PASSWORD)
        if [ -n "$val" ]; then
          display="(set, hidden)"
        else
          display="(empty)"
        fi
        ;;
      *)
        display="$val"
        ;;
    esac
    printf '  %-28s %s\n' "$key" "$display"
  done

  wiz_blank
  echo "Setup complete."
  wiz_flush_env "$ENV_FILE" "$ENV_EXAMPLE"
}

run_wizard "$MODE"
