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
  internal_url="postgresql+psycopg://${RAG_DB_USER}:${RAG_DB_PASSWORD}@postgres:5432/${RAG_DB_NAME}"
  ollama_url="http://host.docker.internal:11434"

  # up.sh roda `up -d` antes do rag-init — exec no backend herda extra_hosts do compose.plain.yml.
  # Evita `compose run` (one-off sem extra_hosts em alguns runtimes) e ignora OLLAMA errado no .env.
  if ! docker compose -f "$COMPOSE_FILE" --profile rag ps --status running -q backend 2>/dev/null | grep -q .; then
    echo "rag-init: backend container não está running — suba o stack antes (up.sh faz up -d primeiro)." >&2
    exit 1
  fi

  echo "→ rag-init: exec backend (OLLAMA=${ollama_url})…"

  if ! docker compose -f "$COMPOSE_FILE" --profile rag exec -T \
      -e "OLLAMA_BASE_URL=${ollama_url}" backend python -c "
import urllib.request
urllib.request.urlopen('${ollama_url}/api/tags', timeout=5)
" >/dev/null 2>&1; then
    echo "rag-init: Ollama unreachable from backend container at ${ollama_url}." >&2
    echo "  On the VM host, check: curl -sf http://127.0.0.1:11434/api/tags" >&2
    echo "  Linux (VM/EC2): default Ollama binds 127.0.0.1 only — Docker cannot reach it via host-gateway." >&2
    echo "  Fix: sudo mkdir -p /etc/systemd/system/ollama.service.d" >&2
    echo "       printf '[Service]\\nEnvironment=OLLAMA_HOST=0.0.0.0:11434\\n' | sudo tee /etc/systemd/system/ollama.service.d/bind-all.conf" >&2
    echo "       sudo systemctl daemon-reload && sudo systemctl restart ollama" >&2
    echo "  Then: curl -sf http://127.0.0.1:11434/api/tags && ./scripts/up.sh" >&2
    exit 1
  fi

  docker compose -f "$COMPOSE_FILE" --profile rag exec -T \
    -e RAG_ENABLED=1 \
    -e "RAG_DATABASE_URL=${internal_url}" \
    -e "RAG_EMBEDDING_PROVIDER=${RAG_EMBEDDING_PROVIDER}" \
    -e "RAG_EMBEDDING_MODEL=${RAG_EMBEDDING_MODEL:-nomic-embed-text}" \
    -e "OLLAMA_BASE_URL=${ollama_url}" \
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
  backend="$(python3 -c 'from app.ai_agents import rag; print(rag.backend_name())')"
  echo "→ rag-init: done (retriever backend=${backend})"
fi
