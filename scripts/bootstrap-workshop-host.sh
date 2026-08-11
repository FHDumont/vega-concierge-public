#!/usr/bin/env bash
# Idempotent bootstrap — Ubuntu host for the Vega workshop (EC2 / bare-metal VM).
# Installs host deps, writes .env, systemd + GHCR pull + stack via up.sh.
#
# Prerequisite: clone of this repo (e.g. /opt/vega-concierge).
#
#   git clone https://github.com/FHDumont/vega-concierge-public.git /opt/vega-concierge
#   cd /opt/vega-concierge
#   sudo ./scripts/bootstrap-workshop-host.sh
#
# Options:
#   --control-password PASS   Ops terminal password (default: vega-workshop)
#   --deployment-env NAME     DEPLOYMENT_ENVIRONMENT (default: user-<hostname>)
#   --repo-dir PATH           repo root (default: parent of scripts/)
#   --repo-owner USER         owner of the repo files (default: SUDO_USER or ubuntu)
#   --hugo-version VER        Hugo extended .deb (default: 0.164.0)
#   --skip-models             skip ollama pull
#   --skip-up                 host + systemd only; skip up.sh
#   --force-env               rewrite .env even if it already exists
#   --check                   check only (no install)
#   -h, --help
#
# Runbook: docs/reference/runbooks/bootstrap-workshop-host.md
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$ROOT"
REPO_OWNER="${SUDO_USER:-ubuntu}"
CONTROL_PASSWORD="vega-workshop"
DEPLOYMENT_ENV=""
HUGO_VERSION="${HUGO_VERSION:-0.164.0}"
SKIP_MODELS=0
SKIP_UP=0
FORCE_ENV=0
CHECK_ONLY=0

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \?//'
}

log() { echo "→ bootstrap: $*"; }
die() { echo "bootstrap: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --control-password) CONTROL_PASSWORD="${2:?}"; shift 2 ;;
    --deployment-env) DEPLOYMENT_ENV="${2:?}"; shift 2 ;;
    --repo-dir) REPO_DIR="${2:?}"; ROOT="$REPO_DIR"; shift 2 ;;
    --repo-owner) REPO_OWNER="${2:?}"; shift 2 ;;
    --hugo-version) HUGO_VERSION="${2:?}"; shift 2 ;;
    --skip-models) SKIP_MODELS=1; shift ;;
    --skip-up) SKIP_UP=1; shift ;;
    --force-env) FORCE_ENV=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (use --help)" ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  die "run with sudo (root)."
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  die "supports amd64 (x86_64) only — arm64 (Mac mini): manual deps + up.sh (deploy-pull-only.md)."
fi

if [[ ! -f "$REPO_DIR/scripts/up.sh" || ! -f "$REPO_DIR/.env.example" ]]; then
  die "invalid repo at $REPO_DIR — missing scripts/up.sh or .env.example"
fi

DEPLOYMENT_ENV="${DEPLOYMENT_ENV:-user-$(hostname)}"

run_check() {
  local p health
  echo "=== VEGA HOST CHECK $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "repo=$REPO_DIR owner=$REPO_OWNER env=${DEPLOYMENT_ENV:-?}"
  echo "docker=$(systemctl is-active docker 2>/dev/null || echo ?) ollama=$(systemctl is-active ollama 2>/dev/null || echo ?)"
  echo "hugo=$(readlink -f "$(command -v hugo 2>/dev/null || echo missing)" 2>/dev/null || echo missing)"
  for p in 3000 8000 9000 1313 11434; do
    ss -tln 2>/dev/null | grep -q ":$p " && echo ":$p=up" || echo ":$p=down"
  done
  echo "vega_ws=$(systemctl is-active vega-workshop 2>/dev/null || echo ?) vega_ops=$(systemctl is-active vega-control 2>/dev/null || echo ?)"
  health="$(curl -sf --max-time 5 http://localhost:8000/api/health 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'),'ollama='+str(d.get('ollama',{}).get('reachable','?')))" 2>/dev/null || echo FAIL)"
  echo "health=$health"
  echo "=== FIM CHECK ==="
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  run_check
  exit 0
fi

log "starting — repo=$REPO_DIR owner=$REPO_OWNER env=$DEPLOYMENT_ENV"

export DEBIAN_FRONTEND=noninteractive

log "base packages (apt)…"
apt-get update -qq
apt-get install -y -qq ca-certificates curl git gnupg lsb-release python3-venv python3-pip ttyd

# ttyd from apt enables its default unit — conflicts with vega-ttyd on :7681
if systemctl is-active ttyd.service &>/dev/null; then
  systemctl disable --now ttyd.service
fi

# Docker via snap is confined (can't read /opt — compose/env_file/bind-mounts fail): swap for docker-ce.
if command -v docker &>/dev/null && readlink -f "$(command -v docker)" | grep -q '^/snap/'; then
  log "docker via snap detected — removing (confined, can't read ${REPO_DIR})…"
  snap remove --purge docker
fi
if ! command -v docker &>/dev/null; then
  log "Docker…"
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
usermod -aG docker "$REPO_OWNER" 2>/dev/null || true

if ! command -v ollama &>/dev/null; then
  log "Ollama…"
  curl -fsSL https://ollama.com/install.sh | sh
fi
mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0:11434\n' > /etc/systemd/system/ollama.service.d/bind-all.conf
systemctl daemon-reload
systemctl enable --now ollama

if [[ "$SKIP_MODELS" -eq 0 ]]; then
  log "Ollama models (llama3.2, nomic-embed-text)…"
  sudo -u "$REPO_OWNER" ollama pull llama3.2
  sudo -u "$REPO_OWNER" ollama pull nomic-embed-text
fi

log "Hugo extended ${HUGO_VERSION} (.deb — not snap; snap can't read /opt)…"
snap remove hugo 2>/dev/null || true
HUGO_DEB="/tmp/hugo_extended_${HUGO_VERSION}_linux-amd64.deb"
curl -fsSL -o "$HUGO_DEB" "https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/hugo_extended_${HUGO_VERSION}_linux-amd64.deb"
dpkg -i "$HUGO_DEB" 2>/dev/null || apt-get install -fy -qq
ln -sf /usr/local/bin/hugo /usr/bin/hugo 2>/dev/null || true

chown -R "$REPO_OWNER:$REPO_OWNER" "$REPO_DIR"
git config --global --add safe.directory "$REPO_DIR"
sudo -u "$REPO_OWNER" git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

if [[ ! -f "$REPO_DIR/.env" || "$FORCE_ENV" -eq 1 ]]; then
  log "writing .env…"
  cat > "$REPO_DIR/.env" <<EOF
# Generated by scripts/bootstrap-workshop-host.sh ($(date -u +%Y-%m-%d))
DEPLOYMENT_ENVIRONMENT=${DEPLOYMENT_ENV}

IMAGE_OWNER=fhdumont
IMAGE_TAG=latest

LLM_PROVIDER_PROMPT_CACHE=0
LLM_CACHE_ENABLED=0
LLM_RATE_MAX=20
LLM_RATE_WINDOW_S=60
API_RATE_ENABLED=1
API_RATE_AI_MAX=12
API_RATE_AI_WINDOW_S=60
API_RATE_DEFAULT_MAX=60
API_RATE_DEFAULT_WINDOW_S=60
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_CHAT_MODEL=llama3.2
LLM_PROVIDER_PRIORITY=BEDROCK,OPENAI,ANTHROPIC,OLLAMA
OPENAI_CHAT_MODEL=gpt-4o-mini
ANTHROPIC_CHAT_MODEL=claude-sonnet-4-5
BEDROCK_CHAT_MODEL=us.anthropic.claude-sonnet-4-5-20250929-v1:0
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
AWS_BEARER_TOKEN_BEDROCK=
AWS_DEFAULT_REGION=us-east-1

GALILEO_API_KEY=
GALILEO_CONSOLE_URL=https://console.multitenant.galileocloud.io
GALILEO_PROJECT=vega-concierge-dev
GALILEO_LOG_STREAM=default

VEGA_SESSION_IDLE_MINUTES=5

RAG_ENABLED=1
RAG_EMBEDDING_PROVIDER=ollama
RAG_EMBEDDING_MODEL=nomic-embed-text
RAG_DB_USER=vega
RAG_DB_PASSWORD=vega
RAG_DB_NAME=vega_rag
RAG_DATABASE_URL=postgresql+psycopg://vega:vega@postgres:5432/vega_rag
RAG_TOP_K=3
REFUND_WINDOW_DAYS=30

CONTROL_PASSWORD=${CONTROL_PASSWORD}
CONTROL_TTYD_PORT=7681

PUBLIC_API_BASE=
API_INTERNAL_URL=http://backend:8000

ENROLL_TOKEN=

TIER_GOLD_USD=1000
TIER_PLATINUM_USD=5000
EOF
  chown "$REPO_OWNER:$REPO_OWNER" "$REPO_DIR/.env"
  chmod 600 "$REPO_DIR/.env"
fi

log "workshop: Hugo permissions (avoids public/ being root-owned)…"
rm -rf "$REPO_DIR/workshop/public" "$REPO_DIR/workshop/.hugo_build.lock"
chown -R "$REPO_OWNER:$REPO_OWNER" "$REPO_DIR/workshop"

mkdir -p /etc/systemd/system/vega-workshop.service.d
cat > /etc/systemd/system/vega-workshop.service.d/override.conf <<EOF
[Service]
User=${REPO_OWNER}
Group=${REPO_OWNER}
EOF

chmod +x "$REPO_DIR/scripts/boot-workshop.sh"
log "systemd (vega-boot, control, workshop, ttyd)…"
REPO_DIR="$REPO_DIR" "$REPO_DIR/control/systemd/install.sh"

if [[ "$SKIP_UP" -eq 0 ]]; then
  log "up.sh (pull GHCR + stack)…"
  sudo -u "$REPO_OWNER" bash -c "cd '$REPO_DIR' && sg docker -c './scripts/up.sh'"
fi

run_check
log "done — store :3000 · API :8000 · Ops :9000 · guide :1313"
