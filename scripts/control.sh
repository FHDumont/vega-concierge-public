#!/usr/bin/env bash
# Vega Ops Console — ON-DEMAND launcher for DEVELOPMENT (F-047, ADR-025).
#
# On the laptop (dev) the panel does NOT stay running all the time: run this script when you need
# to test/use the Ops Console; Ctrl+C stops it. (On the workshop EC2 it's the opposite: the panel
# comes up as a host service via systemd, always on + watchdog — see control/systemd/ and the Ansible playbook.)
#
# Usage:
#   ./scripts/control.sh                 # brings up the panel :9000 (foreground) + ttyd :7681 if installed
#   ./scripts/control.sh --no-terminal   # panel only (no ttyd)
#   ./scripts/control.sh --port 9100     # alternate panel port
#   CONTROL_PASSWORD=mypassword ./scripts/control.sh   # explicit password (otherwise reads from .env / default dev)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTROL_DIR="$ROOT/control"

PORT=9000
WITH_TERMINAL=1

while [ $# -gt 0 ]; do
  case "$1" in
    --no-terminal) WITH_TERMINAL=0; shift ;;
    --port) PORT="${2:?--port requires a value}"; shift 2 ;;
    -h|--help)
      cat <<'EOF'
usage: control.sh [--no-terminal] [--port N]

On-demand launcher for the Vega Ops Console in DEVELOPMENT (foreground; Ctrl+C stops it).
Panel :9000 (or --port) · web terminal ttyd :7681 (if the binary is installed).
Password: CONTROL_PASSWORD from the environment/.env; if absent, uses 'dev' (local dev only).
EOF
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Loads .env from the repo (if present) — reuses CONTROL_PASSWORD/VEGA_REPO_DIR etc. without duplicating config.
# OS wins over .env (F-REAL-ENV-2): CONTROL_PASSWORD=x ./scripts/control.sh works again.
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/env-load.sh"
load_env_os_first

# Password: priority to what came from the environment/.env; fallback 'dev' ONLY for local development.
export CONTROL_PASSWORD="${CONTROL_PASSWORD:-dev}"
export VEGA_REPO_DIR="$ROOT"
export CONTROL_TTYD_PORT="${CONTROL_TTYD_PORT:-7681}"

# panel's own venv (lean; separate from the backend).
python3 -m venv "$CONTROL_DIR/.venv" 2>/dev/null || true
# shellcheck source=/dev/null
source "$CONTROL_DIR/.venv/bin/activate"
pip install -q -r "$CONTROL_DIR/requirements.txt"

TTYD_PID=""
cleanup() {
  [ -n "$TTYD_PID" ] && kill "$TTYD_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Web terminal (optional): only starts if the ttyd binary exists and wasn't disabled.
if [ "$WITH_TERMINAL" -eq 1 ]; then
  if command -v ttyd >/dev/null 2>&1; then
    LOOP_IF=lo; [ "$(uname -s)" = "Darwin" ] && LOOP_IF=lo0
    echo "→ local ttyd (${LOOP_IF}:${CONTROL_TTYD_PORT}/shell) — access via the panel's /shell/"
    ttyd -b /shell -i "$LOOP_IF" -p "$CONTROL_TTYD_PORT" -W bash &
    TTYD_PID=$!
  else
    echo ""
    echo "⚠  ttyd NOT installed — the SSH terminal won't work."
    echo "   macOS: brew install ttyd"
    echo "   Then restart: ./scripts/control.sh"
    echo ""
  fi
fi

echo "→ Vega Ops Console at http://localhost:${PORT}  (panel OPEN; only the SSH terminal asks for a password). Ctrl+C to stop."
cd "$CONTROL_DIR"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
