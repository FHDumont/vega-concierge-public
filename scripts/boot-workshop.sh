#!/usr/bin/env bash
# Unified boot — Vega stack + deps (F-DEPLOY-PROD-1, ADR-035).
# Entry point for the golden AMI and EC2 clones (systemd vega-boot.service).
# Idempotent, headless: fresh-state + pull + up + rag-init + health via up.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export NON_INTERACTIVE=1

echo "→ boot-workshop: starting ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

# Ollama on the host — golden AMI already has the systemd unit; skip if unavailable.
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet ollama 2>/dev/null; then
    echo "→ boot-workshop: Ollama active (systemd)"
  elif systemctl list-unit-files ollama.service >/dev/null 2>&1; then
    echo "→ boot-workshop: starting ollama.service…"
    systemctl start ollama.service || echo "→ boot-workshop: WARN — ollama.service failed" >&2
  else
    echo "→ boot-workshop: Ollama not under systemd — assuming the AMI handles it or manual start"
  fi
else
  echo "→ boot-workshop: no systemctl — skipping Ollama check"
fi

echo "→ boot-workshop: bringing up the Vega stack (up.sh — fresh-state ADR-035)…"
"$ROOT/scripts/up.sh"

echo "→ boot-workshop: done — store :3000 · API :8000 · Ops :9000 · guide :1313 (vega-workshop)"
