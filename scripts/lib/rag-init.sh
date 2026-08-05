#!/usr/bin/env bash
# Wait for Postgres (profile rag) and run setup_vectordb.py — idempotent index (F-RAG-LIVE).
#
# RAG_INIT_VIA=host   — dev.sh: Python no host, RAG_DATABASE_URL → localhost:RAG_DB_PORT (default 5434)
# RAG_INIT_VIA=docker — up.sh prod: one-off no backend container, URL interna @postgres:5432 (sem porta publicada)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"
RAG_INIT_VIA="${RAG_INIT_VIA:-host}"

set -a
# shellcheck disable=SC1091
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a

if [ "${RAG_ENABLED:-0}" != "1" ]; then
  echo "→ rag-init: RAG_ENABLED!=1 — skip index"
  exit 0
fi

RAG_EMBEDDING_PROVIDER="${RAG_EMBEDDING_PROVIDER:-ollama}"
if [ "$RAG_EMBEDDING_PROVIDER" = "openai" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "rag-init: OPENAI_API_KEY vazia — necessária p/ embeddings (RAG_EMBEDDING_PROVIDER=openai)" >&2
  exit 1
fi

RAG_DB_USER="${RAG_DB_USER:-vega}"
RAG_DB_PASSWORD="${RAG_DB_PASSWORD:-vega}"
RAG_DB_NAME="${RAG_DB_NAME:-vega_rag}"
RAG_DB_PORT="${RAG_DB_PORT:-5434}"

echo "→ rag-init: waiting for Postgres (${RAG_DB_USER}@${RAG_DB_NAME})…"
ready=0
for _ in $(seq 1 60); do
  if docker compose -f "$COMPOSE_FILE" --profile rag exec -T postgres \
      pg_isready -U "$RAG_DB_USER" -d "$RAG_DB_NAME" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "rag-init: Postgres not ready after 60s" >&2
  exit 1
fi

echo "→ rag-init: indexing corpora (via=${RAG_INIT_VIA})…"

# Host-side index (dev.sh / up.sh --build): host.docker.internal só resolve DENTRO de containers.
# .env.example usa host.docker.internal — correto p/ backend em Docker, errado p/ Python no host.
if [ "$RAG_INIT_VIA" = "host" ]; then
  case "${OLLAMA_BASE_URL:-}" in
    *host.docker.internal*)
      OLLAMA_BASE_URL="http://127.0.0.1:11434"
      export OLLAMA_BASE_URL
      echo "→ rag-init: OLLAMA_BASE_URL ajustado p/ host → ${OLLAMA_BASE_URL}"
      ;;
  esac
  case "${RAG_DATABASE_URL:-}" in
    *@postgres:*)
      RAG_DATABASE_URL="postgresql+psycopg://${RAG_DB_USER}:${RAG_DB_PASSWORD}@127.0.0.1:${RAG_DB_PORT}/${RAG_DB_NAME}"
      export RAG_DATABASE_URL
      echo "→ rag-init: RAG_DATABASE_URL ajustado p/ host → ${RAG_DATABASE_URL}"
      ;;
  esac
fi

if [ "$RAG_INIT_VIA" = "docker" ]; then
  # Container: 127.0.0.1/localhost apontam pro loopback DO container, não pro Ollama no host.
  case "${OLLAMA_BASE_URL:-}" in
    *127.0.0.1*|*localhost*)
      echo "→ rag-init: WARN — OLLAMA_BASE_URL=${OLLAMA_BASE_URL} inválido dentro do container." >&2
      echo "  Ajustando p/ http://host.docker.internal:11434 (up.sh pull-only / compose)." >&2
      OLLAMA_BASE_URL="http://host.docker.internal:11434"
      export OLLAMA_BASE_URL
      ;;
  esac
  internal_url="postgresql+psycopg://${RAG_DB_USER}:${RAG_DB_PASSWORD}@postgres:5432/${RAG_DB_NAME}"
  # shellcheck disable=SC2086
  docker compose -f "$COMPOSE_FILE" --profile rag run --rm --no-deps \
    --add-host=host.docker.internal:host-gateway \
    -e RAG_ENABLED=1 \
    -e "RAG_DATABASE_URL=${internal_url}" \
    -e "RAG_EMBEDDING_PROVIDER=${RAG_EMBEDDING_PROVIDER}" \
    -e "RAG_EMBEDDING_MODEL=${RAG_EMBEDDING_MODEL:-nomic-embed-text}" \
    -e "OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://host.docker.internal:11434}" \
    -e "OPENAI_API_KEY=${OPENAI_API_KEY:-}" \
    -e "REFUND_WINDOW_DAYS=${REFUND_WINDOW_DAYS:-30}" \
    backend python setup_vectordb.py
else
  export RAG_DATABASE_URL="${RAG_DATABASE_URL:-postgresql+psycopg://${RAG_DB_USER}:${RAG_DB_PASSWORD}@127.0.0.1:${RAG_DB_PORT}/${RAG_DB_NAME}}"
  cd "$ROOT/backend"
  python3 -m venv .venv 2>/dev/null || true
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt -r requirements-rag.txt
  python3 setup_vectordb.py
  backend="$(python3 -c 'from app import rag; print(rag.backend_name())')"
  echo "→ rag-init: done (retriever backend=${backend})"
fi
