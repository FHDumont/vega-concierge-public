#!/usr/bin/env bash
# Production / Docker startup for Vega Concierge (portas diretas, sem Traefik).
#
#   ./scripts/up.sh              # wizard + pull + up -d + Postgres/pgvector RAG (default)
#   ./scripts/up.sh --no-rag       # sem Postgres — keyword retriever only
#   ./scripts/up.sh --build        # local compose with build (docker-compose.yml)
#   ./scripts/up.sh down           # stop production stack
#   ./scripts/up.sh logs           # follow logs (production)
#   ./scripts/up.sh update         # pull + up -d + health + digests (production)
#   ./scripts/up.sh --force-setup  # re-run setup wizard before starting (compose only)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

wait_for_health() {
  echo "→ aguardando GET /api/health…"
  local i health_json
  for i in $(seq 1 60); do
    if health_json="$(curl -sf http://localhost:8000/api/health 2>/dev/null)"; then
      echo "→ backend healthy:"
      echo "$health_json" | python3 -m json.tool 2>/dev/null || echo "$health_json"
      local ver
      ver="$(echo "$health_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','?'))" 2>/dev/null || echo "?")"
      echo "→ VEGA_VERSION=${ver}"
      return 0
    fi
    sleep 2
  done
  echo "up.sh: backend não respondeu em /api/health após 120s" >&2
  exit 1
}

print_image_digests() {
  local owner="${IMAGE_OWNER:-fhdumont}"
  local tag="${IMAGE_TAG:-latest}"
  local backend_image="ghcr.io/${owner}/${BACKEND_IMAGE:-vega-backend}:${tag}"
  local frontend_image="ghcr.io/${owner}/vega-frontend:${tag}"
  echo "→ image digests (local):"
  for img in "$backend_image" "$frontend_image"; do
    local digest
    digest="$(docker inspect --format='{{index .RepoDigests 0}}' "$img" 2>/dev/null || echo "${img} (not pulled locally)")"
    echo "  ${digest}"
  done
}

prod_up_detached() {
  "$ROOT/scripts/lib/preflight-prod.sh"
  echo "→ pulling published images from GHCR…"
  docker compose "${COMPOSE_ARGS[@]}" pull
  fresh_sqlite_compose "${COMPOSE_ARGS[@]}"
  echo "→ up -d (pull-only; no local build)…"
  docker compose "${COMPOSE_ARGS[@]}" up -d
  if [ "$RAG" -eq 1 ] && [ "${RAG_ENABLED:-0}" = "1" ]; then
    RAG_INIT_VIA=docker "$ROOT/scripts/lib/rag-init.sh"
  fi
  wait_for_health
  print_image_digests
  echo "→ Store http://<VM-IP>:3000  ·  API http://<VM-IP>:8000  ·  Ops Console http://<VM-IP>:9000"
  if [ "$RAG" -eq 1 ]; then
    echo "  rag: pgvector (default) — use --no-rag p/ keyword-only"
  fi
  echo "  './scripts/up.sh logs' to follow, './scripts/up.sh down' to stop."
}

BUILD=0
FORCE_SETUP=0
RAG=1
CMD="up"

while [ $# -gt 0 ]; do
  case "$1" in
    --build)
      BUILD=1
      shift
      ;;
    --no-rag)
      RAG=0
      shift
      ;;
    --rag)
      shift ;;  # legacy no-op — RAG é default desde F-RAG-LIVE
    --force-setup)
      FORCE_SETUP=1
      shift
      ;;
    down|logs|up|update)
      CMD="$1"
      shift
      ;;
    -h|--help)
      cat <<'EOF'
usage: up.sh [options] [up|down|logs|update]

Options:
  --build         Local Docker with build (docker-compose.yml, foreground up)
  --no-rag        Skip Postgres/pgvector (keyword retriever only)
  --force-setup   Re-run the setup wizard before starting (compose/--build only)

Default: production pull via compose.plain.yml (detached) + Postgres RAG index.
Ports are published directly on the VM: store :3000 · API :8000 (no Traefik).

Examples:
  ./scripts/up.sh                 # production: pull + up -d + rag index
  ./scripts/up.sh update          # production: pull + up -d + health + digests
  ./scripts/up.sh --no-rag        # production without Postgres
  ./scripts/up.sh --build         # local build
  ./scripts/up.sh down            # stop production stack
  ./scripts/up.sh logs            # follow production logs
EOF
      exit 0
      ;;
    *)
      echo "unknown option or command: $1" >&2
      exit 2
      ;;
  esac
done

if [ "$BUILD" -eq 1 ]; then
  WIZ_MODE="compose"
  COMPOSE_ARGS=(-f docker-compose.yml)
  export COMPOSE_FILE="$ROOT/docker-compose.yml"
  WIZ_ARGS=(--mode "$WIZ_MODE")
  if [ "$CMD" = "update" ]; then
    WIZ_ARGS+=(--if-needed)
  elif [ "$FORCE_SETUP" -eq 1 ]; then
    WIZ_ARGS+=(--force)
  else
    WIZ_ARGS+=(--if-needed)
  fi
  "$ROOT/scripts/setup-wizard.sh" "${WIZ_ARGS[@]}"
else
  COMPOSE_ARGS=(-f compose.plain.yml)
  export COMPOSE_FILE="$ROOT/compose.plain.yml"
  "$ROOT/scripts/lib/validate-prod-env.sh"
fi

if [ "$RAG" -eq 1 ]; then
  COMPOSE_ARGS+=(--profile rag)
fi

set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

# shellcheck disable=SC1091
. "$ROOT/scripts/lib/fresh-state.sh"

case "$CMD" in
  down)
    docker compose "${COMPOSE_ARGS[@]}" down --remove-orphans
    ;;
  logs)
    docker compose "${COMPOSE_ARGS[@]}" logs -f
    ;;
  up|update)
    remove_legacy_project_stack
    if [ "$RAG" -eq 1 ] && [ "${RAG_ENABLED:-0}" = "1" ]; then
      fresh_rag_postgres "${COMPOSE_ARGS[@]}"
      echo "→ postgres (profile rag — pgvector)"
      docker compose "${COMPOSE_ARGS[@]}" up -d postgres
    fi
    if [ "$BUILD" -eq 1 ]; then
      fresh_sqlite_compose "${COMPOSE_ARGS[@]}"
      if [ "$RAG" -eq 1 ] && [ "${RAG_ENABLED:-0}" = "1" ]; then
        COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}" RAG_INIT_VIA=host \
          "$ROOT/scripts/lib/rag-init.sh"
      fi
      echo "→ docker compose up --build (${COMPOSE_ARGS[*]})"
      docker compose "${COMPOSE_ARGS[@]}" up --build
    else
      prod_up_detached
    fi
    ;;
esac
