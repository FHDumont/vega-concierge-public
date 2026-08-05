#!/usr/bin/env bash
# Boot unificado — Vega stack + deps (F-DEPLOY-PROD-1, ADR-035).
# Entry point da AMI golden e clones EC2 (systemd vega-boot.service).
# Idempotente, headless: fresh-state + pull + up + rag-init + health via up.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export NON_INTERACTIVE=1

echo "→ boot-workshop: início ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

# Ollama no host — AMI golden já tem systemd unit; skip se indisponível.
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet ollama 2>/dev/null; then
    echo "→ boot-workshop: Ollama ativo (systemd)"
  elif systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    echo "→ boot-workshop: iniciando ollama.service…"
    systemctl start ollama.service || echo "→ boot-workshop: WARN — ollama.service falhou" >&2
  else
    echo "→ boot-workshop: Ollama fora do systemd — assumindo AMI já cuida ou manual"
  fi
else
  echo "→ boot-workshop: sem systemctl — pulando check Ollama"
fi

echo "→ boot-workshop: subindo stack Vega (up.sh — fresh-state ADR-035)…"
"$ROOT/scripts/up.sh"

echo "→ boot-workshop: concluído — loja :3000 · API :8000 · Ops :9000 · guia :1313 (vega-workshop)"
