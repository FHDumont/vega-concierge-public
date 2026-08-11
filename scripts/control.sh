#!/usr/bin/env bash
# Vega Ops Console — launcher ON-DEMAND para DESENVOLVIMENTO (F-047, ADR-025).
#
# No laptop (dev) o painel NÃO fica rodando o tempo todo: rode este script quando precisar
# testar/usar o Ops Console; Ctrl+C encerra. (Na EC2 do workshop é o oposto: o painel sobe como
# serviço de host via systemd, sempre no ar + watchdog — ver control/systemd/ e o playbook Ansible.)
#
# Uso:
#   ./scripts/control.sh                 # sobe o painel :9000 (foreground) + ttyd :7681 se instalado
#   ./scripts/control.sh --no-terminal   # só o painel (sem ttyd)
#   ./scripts/control.sh --port 9100     # porta alternativa do painel
#   CONTROL_PASSWORD=minhasenha ./scripts/control.sh   # senha explícita (senão lê do .env / default dev)
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

Launcher on-demand do Vega Ops Console para DESENVOLVIMENTO (foreground; Ctrl+C encerra).
Painel :9000 (ou --port) · terminal web ttyd :7681 (se o binário estiver instalado).
Senha: CONTROL_PASSWORD do ambiente/.env; se ausente, usa 'dev' (só p/ desenvolvimento local).
EOF
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Carrega .env do repo (se existir) — reusa CONTROL_PASSWORD/VEGA_REPO_DIR etc. sem duplicar config.
# SO vence .env (F-REAL-ENV-2): CONTROL_PASSWORD=x ./scripts/control.sh volta a funcionar.
# shellcheck disable=SC1091
. "$ROOT/scripts/lib/env-load.sh"
load_env_os_first

# Senha: prioridade p/ o que veio do ambiente/.env; fallback 'dev' SÓ para desenvolvimento local.
export CONTROL_PASSWORD="${CONTROL_PASSWORD:-dev}"
export VEGA_REPO_DIR="$ROOT"
export CONTROL_TTYD_PORT="${CONTROL_TTYD_PORT:-7681}"

# venv própria do painel (enxuta; separada do backend).
python3 -m venv "$CONTROL_DIR/.venv" 2>/dev/null || true
# shellcheck source=/dev/null
source "$CONTROL_DIR/.venv/bin/activate"
pip install -q -r "$CONTROL_DIR/requirements.txt"

TTYD_PID=""
cleanup() {
  [ -n "$TTYD_PID" ] && kill "$TTYD_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Terminal web (opcional): só sobe se o binário ttyd existir e não foi desabilitado.
if [ "$WITH_TERMINAL" -eq 1 ]; then
  if command -v ttyd >/dev/null 2>&1; then
    LOOP_IF=lo; [ "$(uname -s)" = "Darwin" ] && LOOP_IF=lo0
    echo "→ ttyd local (${LOOP_IF}:${CONTROL_TTYD_PORT}/shell) — acesso via painel /shell/"
    ttyd -b /shell -i "$LOOP_IF" -p "$CONTROL_TTYD_PORT" -W bash &
    TTYD_PID=$!
  else
    echo ""
    echo "⚠  ttyd NÃO instalado — o terminal SSH não vai funcionar."
    echo "   macOS: brew install ttyd"
    echo "   Depois reinicie: ./scripts/control.sh"
    echo ""
  fi
fi

echo "→ Vega Ops Console em http://localhost:${PORT}  (painel ABERTO; só o terminal SSH pede senha). Ctrl+C para parar."
cd "$CONTROL_DIR"
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
