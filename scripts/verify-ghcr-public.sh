#!/usr/bin/env bash
# Verifies anonymous pull from GHCR (F-DEPLOY-PROD-1, ADR-036).
# Does not require docker login — mirrors preflight_ghcr from scripts/lib/preflight-prod.sh.
#
# Usage:
#   ./scripts/verify-ghcr-public.sh                    # IMAGE_OWNER from .env or fhdumont
#   ./scripts/verify-ghcr-public.sh --owner fhdumont
#   ./scripts/verify-ghcr-public.sh --browser          # includes vega-backend-browser
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OWNER=""
TAG="latest"
INCLUDE_BROWSER=0

usage() {
  cat <<'EOF'
usage: verify-ghcr-public.sh [options]

Verifies docker manifest inspect without login (public GHCR packages).

Options:
  --owner NAME    GHCR namespace (default: IMAGE_OWNER from .env or fhdumont)
  --tag TAG       Image tag (default: latest)
  --browser       Also checks vega-backend-browser
  -h, --help      This help

If it fails with "denied" or unauthorized, make the package Public in the GitHub UI:
  GitHub → Packages → vega-backend → Package settings → Change visibility → Public
  (repeat for vega-frontend and, if used, vega-backend-browser)
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
  echo "verify-ghcr-public: docker not found" >&2
  exit 1
fi

# Avoids a false positive: temporary logout only if already logged in to ghcr.io.
_was_logged_in=0
if [ -f "${HOME}/.docker/config.json" ] && grep -q '"ghcr.io"' "${HOME}/.docker/config.json" 2>/dev/null; then
  _was_logged_in=1
  echo "→ verify-ghcr-public: removing local ghcr.io credential (anonymous test)…"
  docker logout ghcr.io >/dev/null 2>&1 || true
fi

cleanup() {
  if [ "$_was_logged_in" -eq 1 ]; then
    echo "→ verify-ghcr-public: log back into ghcr.io if needed (test used anonymous pull)" >&2
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
echo "→ verify-ghcr-public: owner=${OWNER} tag=${TAG} (no docker login)"
for img in "${images[@]}"; do
  echo -n "  ${img} … "
  if docker manifest inspect "$img" >/dev/null 2>&1; then
    echo "OK"
  else
    echo "FAILED"
    echo "    Package still private, tag doesn't exist, or network issue. UI: Packages → $(basename "${img%%:*}" | sed 's|.*/||') → Public" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "verify-ghcr-public: one or more manifests unreachable (anonymous pull)" >&2
  exit 1
fi

echo "→ verify-ghcr-public: all manifests OK (GHCR public for anonymous pull)"
