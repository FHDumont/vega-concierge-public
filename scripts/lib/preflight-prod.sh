#!/usr/bin/env bash
# Production preflight — Docker daemon, .env, GHCR manifests (public), Ollama host probe.
#
# GHCR images are PUBLIC — docker manifest inspect without docker login (F-DEPLOY-PROD-1).
# Ollama runs on the HOST (127.0.0.1:11434), not in the container. The backend reaches it via host-gateway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

preflight_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "preflight: docker not found — install Docker and try again." >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "preflight: Docker daemon stopped — start Docker and try again." >&2
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "preflight: docker compose plugin not found." >&2
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

  echo "→ preflight: GHCR manifest (public) ${backend_image}…"
  if ! docker manifest inspect "$backend_image" >/dev/null 2>&1; then
    echo "preflight: could not inspect ${backend_image}." >&2
    echo "  Common causes: tag doesn't exist yet (CI hasn't published it), network, wrong IMAGE_OWNER/IMAGE_TAG." >&2
    echo "  Images are public — docker login is NOT required." >&2
    exit 1
  fi
  echo "→ preflight: GHCR manifest (public) ${frontend_image}…"
  if ! docker manifest inspect "$frontend_image" >/dev/null 2>&1; then
    echo "preflight: could not inspect ${frontend_image}." >&2
    exit 1
  fi
  echo "→ preflight: GHCR manifests OK (anonymous pull; amd64 + arm64 in CI)"
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

  echo "→ preflight: Ollama on the host (${ollama_host_url})…"
  local tags_json=""
  if ! tags_json="$(curl -sf "${ollama_host_url}/api/tags" 2>/dev/null)"; then
    echo "preflight: WARN — Ollama unreachable at ${ollama_host_url}." >&2
    echo "  LLM and embeddings use Ollama on the host. Start it: ollama serve" >&2
    echo "  Continuing — /api/health after up confirms container connectivity." >&2
    return 0
  fi

  if ! _model_in_tags "$tags_json" "$embed_model"; then
    if command -v ollama >/dev/null 2>&1; then
      echo "→ preflight: ollama pull ${embed_model}…"
      ollama pull "$embed_model" || echo "preflight: WARN — ollama pull ${embed_model} failed" >&2
    else
      echo "preflight: WARN — model ${embed_model} missing; run: ollama pull ${embed_model}" >&2
    fi
  fi
  if ! _model_in_tags "$tags_json" "$chat_model"; then
    if command -v ollama >/dev/null 2>&1; then
      echo "→ preflight: ollama pull ${chat_model}…"
      ollama pull "$chat_model" || echo "preflight: WARN — ollama pull ${chat_model} failed" >&2
    else
      echo "preflight: WARN — model ${chat_model} missing; run: ollama pull ${chat_model}" >&2
    fi
  fi
  echo "→ preflight: Ollama OK"
}

preflight_docker
preflight_env

# OS wins over .env (F-REAL-ENV-2).
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/env-load.sh"
load_env_os_first

preflight_ghcr
preflight_ollama
