#!/usr/bin/env bash
# DEV mode (no Docker for the app). Back + Front as local processes.
#
#   ./scripts/dev.sh              # hot reload + Postgres/pgvector RAG (default)
#   ./scripts/dev.sh --no-rag     # no Postgres — in-process keyword retriever
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

RAG=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-rag) RAG=0; shift ;;
    --rag) shift ;;  # legacy no-op — RAG has been the default since F-RAG-LIVE
    -h|--help)
      cat <<'EOF'
usage: dev.sh [--no-rag]

  (default)  Postgres/pgvector + automatic index + backend --reload + frontend hot reload
  --no-rag   Skip Postgres; keyword retriever only (RAG_ENABLED=0 in .env recommended)
EOF
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

"$ROOT/scripts/setup-wizard.sh" --mode dev --if-needed
set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

# shellcheck disable=SC1091
. "$ROOT/scripts/lib/fresh-state.sh"
remove_legacy_project_stack
# dev.sh runs the app on the HOST. A still-running containerized backend/frontend holds :8000/:3000, and the
# browser ends up talking to the old container — which, after the pgvector reindex, responds with a
# dead connection pool ("Something went wrong" on every chat).
(cd "$ROOT" && docker compose stop backend frontend >/dev/null 2>&1) || true
fresh_sqlite_host

if [ "$RAG" = "1" ]; then
  echo "→ postgres (profile rag — pgvector)"
  if [ "${RAG_ENABLED:-0}" = "1" ]; then
    fresh_rag_postgres -f "$ROOT/docker-compose.yml" --profile rag
  fi
  (cd "$ROOT" && docker compose --profile rag up -d postgres)
  "$ROOT/scripts/lib/rag-init.sh"
fi

echo "→ backend (uvicorn --reload :8000)"
cd "$ROOT/backend"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
if [ "$RAG" = "1" ] || [ "${RAG_ENABLED:-0}" = "1" ]; then
  pip install -q -r requirements-rag.txt
fi

DEPLOYMENT_ENVIRONMENT="${DEPLOYMENT_ENVIRONMENT:-dev}"
uvicorn app.api:app --reload --port 8000 &
BACK=$!

echo "→ frontend (next dev :3000)"
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
# Webpack dev + Turbopack build share .next — a corrupted cache breaks RSC/providers
# (e.g., useShop outside ShopProvider on /checkout). Clearing it keeps dev reliable.
rm -rf .next
npm run dev &
FRONT=$!

trap "kill $BACK $FRONT 2>/dev/null" EXIT
echo "→ http://localhost:3000  (API at :8000). Ctrl+C to stop."
if [ "$RAG" = "1" ]; then
  echo "  rag: pgvector default — use --no-rag for keyword-only"
fi
wait
