#!/usr/bin/env bash
# Deprecated: use ./scripts/up.sh instead.
# Thin wrapper kept for backward compatibility (F-034).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/up.sh" "$@"
