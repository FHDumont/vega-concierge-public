#!/usr/bin/env bash
# Instala os serviços de host do Vega (F-047 + F-DEPLOY-PROD-1) — watchdog via systemd.
# Idempotente: venv do painel, copia units, habilita e (re)inicia no boot.
# Requer: docker + compose, python3/venv, ttyd, hugo extended (workshop).
#
# Uso (na VM, como root ou via sudo):
#   sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/vega-concierge}"
CONTROL_DIR="$REPO_DIR/control"
SYSTEMD_SRC="$CONTROL_DIR/systemd"

echo "→ Vega host services: setup em $REPO_DIR"

# 1) venv do painel (FastAPI/uvicorn enxutos).
python3 -m venv "$CONTROL_DIR/.venv"
"$CONTROL_DIR/.venv/bin/pip" install -q --upgrade pip
"$CONTROL_DIR/.venv/bin/pip" install -q -r "$CONTROL_DIR/requirements.txt"

# 2) boot script executável.
chmod +x "$REPO_DIR/scripts/boot-workshop.sh"

# 3) units → /etc/systemd/system (substituir REPO_DIR nos templates).
install_unit() {
  local src="$1"
  local dest="/etc/systemd/system/$(basename "$src")"
  sed "s|/opt/vega-concierge|${REPO_DIR}|g" "$src" > "$dest"
  chmod 0644 "$dest"
}

for unit in vega-boot.service vega-workshop.service vega-control.service vega-ttyd.service; do
  install_unit "$SYSTEMD_SRC/$unit"
done

# 4) recarrega, habilita no boot e (re)inicia.
systemctl daemon-reload
systemctl enable --now vega-boot.service
systemctl enable --now vega-workshop.service
systemctl enable --now vega-control.service
systemctl enable --now vega-ttyd.service

echo "→ pronto. Loja :3000 · API :8000 · Ops :9000 · guia :1313 · terminal :7681"
