#!/usr/bin/env bash
# Installs Vega's host services (F-047 + F-DEPLOY-PROD-1) — watchdog via systemd.
# Idempotent: panel venv, copies units, enables and (re)starts on boot.
# Requires: docker + compose, python3/venv, ttyd, hugo extended (workshop).
#
# Usage (on the VM, as root or via sudo):
#   sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/vega-concierge}"
CONTROL_DIR="$REPO_DIR/control"
SYSTEMD_SRC="$CONTROL_DIR/systemd"
# The units run as this user, NOT root. Default: whoever owns the repo — root-owned runtime
# artifacts (.env.runtime, control-audit.log) lock the owner out of running scripts/up.sh by hand.
VEGA_USER="${VEGA_USER:-$(stat -c '%U' "$REPO_DIR")}"
VEGA_GROUP="${VEGA_GROUP:-$(id -gn "$VEGA_USER")}"

echo "→ Vega host services: setup at $REPO_DIR (service user: $VEGA_USER:$VEGA_GROUP)"

# 0) the service user needs the docker socket (panel + boot shell out to `docker compose`).
if getent group docker >/dev/null 2>&1 && ! id -nG "$VEGA_USER" | tr ' ' '\n' | grep -qx docker; then
  echo "→ adding $VEGA_USER to the docker group"
  usermod -aG docker "$VEGA_USER"
fi

# 1) panel venv (lean FastAPI/uvicorn) — created AS the service user so it can update it later.
as_user() {
  if [ "$(id -un)" = "$VEGA_USER" ]; then "$@"
  else runuser -u "$VEGA_USER" -- "$@"; fi
}
as_user python3 -m venv "$CONTROL_DIR/.venv"
as_user "$CONTROL_DIR/.venv/bin/pip" install -q --upgrade pip
as_user "$CONTROL_DIR/.venv/bin/pip" install -q -r "$CONTROL_DIR/requirements.txt"

# 2) make the boot script executable.
chmod +x "$REPO_DIR/scripts/boot-workshop.sh"

# 2b) repair artifacts a previous root-run install/boot left behind (idempotent on a clean host).
chown -R "$VEGA_USER:$VEGA_GROUP" "$CONTROL_DIR/.venv"
for artifact in "$REPO_DIR/.env.runtime" "$REPO_DIR/control-audit.log" "$CONTROL_DIR/app/__pycache__"; do
  [ -e "$artifact" ] && chown -R "$VEGA_USER:$VEGA_GROUP" "$artifact" || true
done

# 3) units → /etc/systemd/system (substitute REPO_DIR and the service user in the templates).
install_unit() {
  local src="$1"
  local dest="/etc/systemd/system/$(basename "$src")"
  sed -e "s|/opt/vega-concierge|${REPO_DIR}|g" \
      -e "s|^User=splunk$|User=${VEGA_USER}|" \
      -e "s|^Group=splunk$|Group=${VEGA_GROUP}|" \
      "$src" > "$dest"
  chmod 0644 "$dest"
}

for unit in vega-boot.service vega-workshop.service vega-control.service vega-ttyd.service; do
  install_unit "$SYSTEMD_SRC/$unit"
done

# 4) reload, enable on boot, and (re)start.
systemctl daemon-reload
systemctl enable --now vega-boot.service
systemctl enable --now vega-workshop.service
systemctl enable --now vega-control.service
systemctl enable --now vega-ttyd.service

echo "→ done. Store :3000 · API :8000 · Ops :9000 · guide :1313 · terminal :7681"
