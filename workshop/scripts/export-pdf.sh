#!/usr/bin/env bash
# Export the Vega Concierge Hugo workshop to a single print-quality PDF.
#
# Requires macOS + Safari. Uses File → Export as PDF (AppleScript), then merges.
#
# One-time setup:
#   pip install -r workshop/requirements-export.txt
#   playwright install chromium    # page-order discovery only
#   System Settings → Privacy & Security → Accessibility → enable Terminal/Cursor
#
# Usage:
#   ./workshop/scripts/export-pdf.sh
#   ./workshop/scripts/export-pdf.sh --include-appendix

set -euo pipefail

WORKSHOP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${WORKSHOP_DIR}/export/vega-concierge-workshop.pdf"
INCLUDE_APPENDIX=false
NO_BUILD=false
PORT=""
PYTHON="${PYTHON:-python3}"

usage() {
  sed -n '2,16p' "$0" | tail -n +2
  echo ""
  echo "Options:"
  echo "  -o, --output PATH       Output PDF (default: workshop/export/vega-concierge-workshop.pdf)"
  echo "  --include-appendix      Append hidden instructor appendix pages"
  echo "  --no-build              Skip hugo --minify (use existing public/)"
  echo "  --port PORT             Static server port (default: random free port)"
  echo "  -h, --help              Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUTPUT="$2"; shift 2 ;;
    --include-appendix) INCLUDE_APPENDIX=true; shift ;;
    --no-build) NO_BUILD=true; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --engine)
      echo "error: --engine was removed — export always uses Safari WebKit on macOS." >&2
      exit 1
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: PDF export requires macOS with Safari (WebKit print engine)." >&2
  exit 1
fi

if ! command -v hugo >/dev/null 2>&1; then
  echo "error: hugo not found (need Hugo extended ≥ 0.161)" >&2
  exit 1
fi

if ! "$PYTHON" -c "import playwright" 2>/dev/null; then
  echo "error: missing Python deps. Run once:" >&2
  echo "  pip install -r ${WORKSHOP_DIR}/requirements-export.txt" >&2
  echo "  playwright install chromium" >&2
  exit 1
fi

cd "$WORKSHOP_DIR"

if [[ "$NO_BUILD" == false ]]; then
  echo "==> hugo --minify"
  hugo --minify --logLevel warn
fi

if [[ ! -d public/workshops/vega ]]; then
  echo "error: ${WORKSHOP_DIR}/public/workshops/vega not found — run hugo first" >&2
  exit 1
fi

pick_port() {
  if [[ -n "$PORT" ]]; then
    echo "$PORT"
    return
  fi
  "$PYTHON" -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()"
}

PORT="$(pick_port)"
BASE_URL="http://127.0.0.1:${PORT}"

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "==> serving public/ at ${BASE_URL}"
(
  cd public
  exec "$PYTHON" -m http.server "$PORT" --bind 127.0.0.1
) >/dev/null 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 30); do
  if curl -sf "${BASE_URL}/workshops/vega/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if ! curl -sf "${BASE_URL}/workshops/vega/" >/dev/null 2>&1; then
  echo "error: static server did not start on port ${PORT}" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

EXPORT_ARGS=(
  --base-url "$BASE_URL"
  --workshop-dir "$WORKSHOP_DIR"
  --public-dir "${WORKSHOP_DIR}/public"
  --output "$OUTPUT"
)
if [[ "$INCLUDE_APPENDIX" == true ]]; then
  EXPORT_ARGS+=(--include-appendix)
fi

echo "==> export PDF → ${OUTPUT}"
"$PYTHON" "${WORKSHOP_DIR}/scripts/export_pdf.py" "${EXPORT_ARGS[@]}"

echo "==> open with: open \"${OUTPUT}\""
