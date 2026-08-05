#!/usr/bin/env bash
# Verifica pull anônimo no GHCR (F-DEPLOY-PROD-1, ADR-036).
# Não exige docker login — espelha o preflight_ghcr de scripts/lib/preflight-prod.sh.
#
# Uso:
#   ./scripts/verify-ghcr-public.sh                    # IMAGE_OWNER do .env ou fhdumont
#   ./scripts/verify-ghcr-public.sh --owner fhdumont
#   ./scripts/verify-ghcr-public.sh --browser          # inclui vega-backend-browser
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OWNER=""
TAG="latest"
INCLUDE_BROWSER=0

usage() {
  cat <<'EOF'
usage: verify-ghcr-public.sh [options]

Verifica docker manifest inspect sem login (packages GHCR públicos).

Options:
  --owner NAME    GHCR namespace (default: IMAGE_OWNER do .env ou fhdumont)
  --tag TAG       Tag da imagem (default: latest)
  --browser       Também verifica vega-backend-browser
  -h, --help      Esta ajuda

Se falhar com "denied" ou unauthorized, torne o package Public na UI GitHub:
  GitHub → Packages → vega-backend → Package settings → Change visibility → Public
  (repita para vega-frontend e, se usar, vega-backend-browser)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --owner)
      OWNER="${2:-}"
      shift 2
      ;;
    --tag)
      TAG="${2:-}"
      shift 2
      ;;
    --browser)
      INCLUDE_BROWSER=1
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

if [ -z "$OWNER" ] && [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi
OWNER="${OWNER:-${IMAGE_OWNER:-fhdumont}}"

if ! command -v docker >/dev/null 2>&1; then
  echo "verify-ghcr-public: docker não encontrado" >&2
  exit 1
fi

# Evita falso positivo: logout temporário só se já logado no ghcr.io.
_was_logged_in=0
if [ -f "${HOME}/.docker/config.json" ] && grep -q '"ghcr.io"' "${HOME}/.docker/config.json" 2>/dev/null; then
  _was_logged_in=1
  echo "→ verify-ghcr-public: removendo credencial ghcr.io local (teste anônimo)…"
  docker logout ghcr.io >/dev/null 2>&1 || true
fi

cleanup() {
  if [ "$_was_logged_in" -eq 1 ]; then
    echo "→ verify-ghcr-public: re-logue em ghcr.io se precisar (teste usou pull anônimo)" >&2
  fi
}
trap cleanup EXIT

images=(
  "ghcr.io/${OWNER}/vega-backend:${TAG}"
  "ghcr.io/${OWNER}/vega-frontend:${TAG}"
)
if [ "$INCLUDE_BROWSER" -eq 1 ]; then
  images+=("ghcr.io/${OWNER}/vega-backend-browser:${TAG}")
fi

failed=0
echo "→ verify-ghcr-public: owner=${OWNER} tag=${TAG} (sem docker login)"
for img in "${images[@]}"; do
  echo -n "  ${img} … "
  if docker manifest inspect "$img" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FALHOU"
    echo "    Package ainda privado, tag inexistente ou rede. UI: Packages → $(basename "${img%%:*}" | sed 's|.*/||') → Public" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "verify-ghcr-public: um ou mais manifests inacessíveis (pull anônimo)" >&2
  exit 1
fi

echo "→ verify-ghcr-public: todos os manifests OK (GHCR público para pull anônimo)"
