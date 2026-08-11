#!/usr/bin/env bash
# Clean state on every start — fresh SQLite + pgvector recreated; preserves llm_providers (F-REAL-ENV-1).
# Sourced by dev.sh / up.sh or invoked directly: fresh-state.sh host | compose …
# `set -e` etc. only on direct invocation — sourced, it would inherit the caller's shell (dev.sh uses only `set -e`).

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PERSIST_DIR="${VEGA_PERSIST_DIR:-$ROOT/backend/.vega-persist}"

_resolve_sqlite_host_path() {
  local db="${ORDERS_DB:-$ROOT/backend/vega.db}"
  if [[ "$db" != /* ]]; then
    db="$ROOT/backend/$db"
  fi
  printf '%s' "$db"
}

_resolve_llm_backup_python() {
  local py="$ROOT/backend/.venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi
  printf '%s' "$py"
}

_run_llm_backup_python() {
  local db_path="$1" py="$2"
  mkdir -p "$PERSIST_DIR"
  export VEGA_PERSIST_DIR="$PERSIST_DIR"
  export ORDERS_DB="$db_path"
  (
    cd "$ROOT/backend"
    "$py" -c "from app.llm import llm_config; n=llm_config.export_providers_backup(); print(n)"
  )
}

# A backup that fails NEVER overwrites the JSON: the Admin's API key only lives there. Without a
# database (first boot, or host after using the compose volume) the previous backup is preserved.
# Without the old `2>/dev/null`: an import error (e.g. missing package in .venv) has to show up
# in the WARN — that's how the late-night incident degraded silently (backup "failed" with no clue).
backup_llm_providers_host() {
  local db n err py
  db="$(_resolve_sqlite_host_path)"
  mkdir -p "$PERSIST_DIR"
  if [[ ! -f "$db" ]]; then
    echo "→ fresh-state: llm_providers backup skipped (no SQLite on the host — current backup preserved)"
    return 0
  fi
  py="$(_resolve_llm_backup_python)"
  echo "→ fresh-state: using python '$py' for llm_providers backup"
  err="$(mktemp)"
  if ! n="$(_run_llm_backup_python "$db" "$py" 2>"$err")" || [[ ! "$n" =~ ^[0-9]+$ ]]; then
    echo "→ fresh-state: WARN llm_providers backup failed — current backup preserved" >&2
    sed 's/^/  stderr: /' "$err" >&2
    rm -f "$err"
    return 0
  fi
  rm -f "$err"
  echo "→ fresh-state: llm_providers backup ($n row(s) → $PERSIST_DIR/llm_providers.json)"
}

backup_llm_providers_compose() {
  mkdir -p "$PERSIST_DIR"
  local out err
  echo "→ fresh-state: using python 'python3' (backend container) for llm_providers backup"
  err="$(mktemp)"
  out="$(docker compose "$@" run --rm --no-deps \
    -v "$PERSIST_DIR:/persist" \
    -e VEGA_PERSIST_DIR=/persist \
    -e ORDERS_DB=/data/vega.db \
    backend python3 -c "from app.llm import llm_config; print(llm_config.export_providers_backup())" \
    2>"$err" | tail -1 || true)"
  if [[ ! "$out" =~ ^[0-9]+$ ]]; then
    echo "→ fresh-state: WARN llm_providers backup failed — current backup preserved" >&2
    sed 's/^/  stderr: /' "$err" >&2
    rm -f "$err"
    return 0
  fi
  rm -f "$err"
  echo "→ fresh-state: llm_providers backup ($out row(s) → $PERSIST_DIR/llm_providers.json)"
}

fresh_sqlite_host() {
  backup_llm_providers_host
  local db wal shm
  db="$(_resolve_sqlite_host_path)"
  wal="${db}-wal"
  shm="${db}-shm"
  rm -f "$db" "$wal" "$shm"
  echo "→ fresh-state: SQLite wiped ($(basename "$db")) — orders/users/agents reset"
}

fresh_sqlite_compose() {
  backup_llm_providers_compose "$@"
  echo "→ fresh-state: SQLite wiped (vega-db volume)…"
  docker compose "$@" run --rm --no-deps --entrypoint sh backend \
    -c 'rm -f /data/vega.db /data/vega.db-wal /data/vega.db-shm; echo "  /data/vega.db removed"'
}

_compose_project_name() {
  docker compose "$@" config --format json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true
}

# Before `name: vega` in docker-compose.yml, dev.sh ran in the `vega-concierge` project and up.sh in
# `vega`: two stacks alive at the same time. The legacy one has to go, otherwise the old postgres keeps
# holding :5434 and the "clean" start doesn't even come up.
remove_legacy_project_stack() {
  local legacy="vega-concierge" ids vols
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$legacy" 2>/dev/null || true)"
  vols="$(docker volume ls -q --filter "label=com.docker.compose.project=$legacy" 2>/dev/null || true)"
  [ -n "$ids$vols" ] || return 0
  echo "→ fresh-state: removing legacy stack for project '$legacy' (port/volume conflict)…"
  [ -n "$ids" ] && docker rm -f $ids >/dev/null 2>&1 || true
  [ -n "$vols" ] && docker volume rm $vols >/dev/null 2>&1 || true
}

fresh_rag_postgres() {
  echo "→ fresh-state: pgvector volume (reindex from scratch)…"
  docker compose "$@" stop postgres 2>/dev/null || true
  local proj vol
  proj="$(_compose_project_name "$@")"
  if [ -z "$proj" ]; then
    echo "  warn: compose project not resolved — volume preserved (won't risk deleting another stack's)" >&2
    return 0
  fi
  # Searching with `docker volume ls | grep vega-vectors | head -1` deleted the volume of ANY
  # project: it would take down the Postgres of a live backend and every query became a psycopg AdminShutdown.
  vol="$(docker volume ls -q \
    --filter "label=com.docker.compose.project=$proj" \
    --filter "label=com.docker.compose.volume=vega-vectors" 2>/dev/null | head -1 || true)"
  if [ -n "$vol" ]; then
    if docker volume rm "$vol" >/dev/null 2>&1; then
      echo "  removed $vol"
    else
      echo "  warn: could not remove $vol (in use?)" >&2
    fi
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
  # OS wins over .env (F-REAL-ENV-2).
  # shellcheck disable=SC1091
  . "$ROOT/scripts/lib/env-load.sh"
  load_env_os_first
  case "${1:-host}" in
    host) fresh_sqlite_host ;;
    compose) shift; fresh_sqlite_compose "$@"; fresh_rag_postgres "$@" ;;
    *) echo "usage: fresh-state.sh host | compose [docker compose args…]" >&2; exit 2 ;;
  esac
fi
