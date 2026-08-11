#!/usr/bin/env bash
# Production preflight — Docker daemon, .env, GHCR manifests (público), Ollama host probe.
#
# Imagens GHCR são PÚBLICAS — docker manifest inspect sem docker login (F-DEPLOY-PROD-1).
# Ollama roda no HOST (127.0.0.1:11434), não no container. O backend alcança via host-gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

preflight_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "preflight: docker não encontrado — instale Docker e tente novamente." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "preflight: Docker daemon parado — inicie o Docker e tente novamente." >&2
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "preflight: plugin docker compose não encontrado." >&2
    exit 1
  fi
}

preflight_env() {
  "$ROOT/scripts/lib/validate-prod-env.sh"
}

preflight_ghcr() {
  local owner="${IMAGE_OWNER:-fhdumont}"
  local tag="${IMAGE_TAG:-latest}"
  local backend_image="ghcr.io/${owner}/${BACKEND_IMAGE:-vega-backend}:${tag}"
  local frontend_image="ghcr.io/${owner}/vega-frontend:${tag}"

  echo "→ preflight: GHCR manifest (público) ${backend_image}…"
  if ! docker manifest inspect "$backend_image" >/dev/null 2>&1; then
    echo "preflight: não foi possível inspecionar ${backend_image}." >&2
    echo "  Causas comuns: tag inexistente (CI ainda não publicou), rede, IMAGE_OWNER/IMAGE_TAG errados." >&2
    echo "  Imagens são públicas — docker login NÃO é necessário." >&2
    exit 1
  fi
  echo "→ preflight: GHCR manifest (público) ${frontend_image}…"
  if ! docker manifest inspect "$frontend_image" >/dev/null 2>&1; then
    echo "preflight: não foi possível inspecionar ${frontend_image}." >&2
    exit 1
  fi
  echo "→ preflight: manifests GHCR OK (pull anônimo; amd64 + arm64 no CI)"
}

_model_in_tags() {
  local json="$1"
  local want="$2"
  local base="${want%%:*}"
  echo "$json" | grep -qE "\"name\"[[:space:]]*:[[:space:]]*\"${base}(:|\"|$)" \
    || echo "$json" | grep -qE "\"name\"[[:space:]]*:[[:space:]]*\"${want}\""
}

preflight_ollama() {
  local ollama_host_url="${OLLAMA_PREFLIGHT_URL:-http://127.0.0.1:11434}"
  local embed_model="${RAG_EMBEDDING_MODEL:-nomic-embed-text}"
  local chat_model="${OLLAMA_CHAT_MODEL:-llama3.2}"

  echo "→ preflight: Ollama no host (${ollama_host_url})…"
  local tags_json=""
  if ! tags_json="$(curl -sf "${ollama_host_url}/api/tags" 2>/dev/null)"; then
    echo "preflight: WARN — Ollama unreachable em ${ollama_host_url}." >&2
    echo "  LLM e embeddings usam Ollama no host. Inicie: ollama serve" >&2
    echo "  Continuando — /api/health pós-up confirma conectividade do container." >&2
    return 0
  fi

  if ! _model_in_tags "$tags_json" "$embed_model"; then
    if command -v ollama >/dev/null 2>&1; then
      echo "→ preflight: ollama pull ${embed_model}…"
      ollama pull "$embed_model" || echo "preflight: WARN — ollama pull ${embed_model} falhou" >&2
    else
      echo "preflight: WARN — modelo ${embed_model} ausente; rode: ollama pull ${embed_model}" >&2
    fi
  fi
  if ! _model_in_tags "$tags_json" "$chat_model"; then
    if command -v ollama >/dev/null 2>&1; then
      echo "→ preflight: ollama pull ${chat_model}…"
      ollama pull "$chat_model" || echo "preflight: WARN — ollama pull ${chat_model} falhou" >&2
    else
      echo "preflight: WARN — modelo ${chat_model} ausente; rode: ollama pull ${chat_model}" >&2
    fi
  fi
  echo "→ preflight: Ollama OK"
}

preflight_docker
preflight_env

# SO vence .env (F-REAL-ENV-2).
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/env-load.sh"
load_env_os_first

preflight_ghcr
preflight_ollama
