#!/usr/bin/env bash
# Estado limpo a cada start — SQLite novo + pgvector recriado; preserva llm_providers (F-REAL-ENV-1).
# Sourced por dev.sh / up.sh ou invocado direto: fresh-state.sh host | compose …
# `set -e` etc. só na invocação direta — sourced, herdaria o shell do chamador (dev.sh usa só `set -e`).

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PERSIST_DIR="${VEGA_PERSIST_DIR:-$ROOT/backend/.vega-persist}"

_resolve_sqlite_host_path() {
  local db="${ORDERS_DB:-$ROOT/backend/vega.db}"
  if [[ "$db" != /* ]]; then
    db="$ROOT/backend/$db"
  fi
  printf '%s' "$db"
}

_run_llm_backup_python() {
  local db_path="${1:-$(_resolve_sqlite_host_path)}"
  mkdir -p "$PERSIST_DIR"
  export VEGA_PERSIST_DIR="$PERSIST_DIR"
  export ORDERS_DB="$db_path"
  local py="$ROOT/backend/.venv/bin/python"
  if [[ ! -x "$py" ]]; then
    py="python3"
  fi
  (
    cd "$ROOT/backend"
    "$py" -c "from app import llm_config; n=llm_config.export_providers_backup(); print(n)"
  )
}

# Backup que falha NUNCA sobrescreve o JSON: a chave de API do Admin só existe ali. Sem banco
# (primeira subida, ou host depois de usar o volume do compose) o backup anterior é preservado.
backup_llm_providers_host() {
  local db n
  db="$(_resolve_sqlite_host_path)"
  mkdir -p "$PERSIST_DIR"
  if [[ ! -f "$db" ]]; then
    echo "→ fresh-state: llm_providers backup pulado (sem SQLite no host — backup atual preservado)"
    return 0
  fi
  if ! n="$(_run_llm_backup_python "$db" 2>/dev/null)" || [[ ! "$n" =~ ^[0-9]+$ ]]; then
    echo "→ fresh-state: WARN backup de llm_providers falhou — backup atual preservado" >&2
    return 0
  fi
  echo "→ fresh-state: llm_providers backup ($n row(s) → $PERSIST_DIR/llm_providers.json)"
}

backup_llm_providers_compose() {
  mkdir -p "$PERSIST_DIR"
  local out
  out="$(docker compose "$@" run --rm --no-deps \
    -v "$PERSIST_DIR:/persist" \
    -e VEGA_PERSIST_DIR=/persist \
    -e ORDERS_DB=/data/vega.db \
    backend python3 -c "from app import llm_config; print(llm_config.export_providers_backup())" \
    2>/dev/null | tail -1 || true)"
  if [[ ! "$out" =~ ^[0-9]+$ ]]; then
    echo "→ fresh-state: WARN backup de llm_providers falhou — backup atual preservado" >&2
    return 0
  fi
  echo "→ fresh-state: llm_providers backup ($out row(s) → $PERSIST_DIR/llm_providers.json)"
}

fresh_sqlite_host() {
  backup_llm_providers_host
  local db wal shm
  db="$(_resolve_sqlite_host_path)"
  wal="${db}-wal"
  shm="${db}-shm"
  rm -f "$db" "$wal" "$shm"
  echo "→ fresh-state: SQLite limpo ($(basename "$db")) — pedidos/usuários/agentes resetados"
}

fresh_sqlite_compose() {
  backup_llm_providers_compose "$@"
  echo "→ fresh-state: SQLite limpo (volume vega-db)…"
  docker compose "$@" run --rm --no-deps --entrypoint sh backend \
    -c 'rm -f /data/vega.db /data/vega.db-wal /data/vega.db-shm; echo "  /data/vega.db removed"'
}

_compose_project_name() {
  docker compose "$@" config --format json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))' 2>/dev/null || true
}

# Antes de `name: vega` no docker-compose.yml, dev.sh rodava no projeto `vega-concierge` e up.sh no
# `vega`: dois stacks vivos ao mesmo tempo. O legado precisa sair, senão o postgres antigo continua
# segurando :5434 e o start "limpo" nem sobe.
remove_legacy_project_stack() {
  local legacy="vega-concierge" ids vols
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$legacy" 2>/dev/null || true)"
  vols="$(docker volume ls -q --filter "label=com.docker.compose.project=$legacy" 2>/dev/null || true)"
  [ -n "$ids$vols" ] || return 0
  echo "→ fresh-state: removendo stack legado do projeto '$legacy' (conflito de porta/volume)…"
  [ -n "$ids" ] && docker rm -f $ids >/dev/null 2>&1 || true
  [ -n "$vols" ] && docker volume rm $vols >/dev/null 2>&1 || true
}

fresh_rag_postgres() {
  echo "→ fresh-state: pgvector volume (reindex do zero)…"
  docker compose "$@" stop postgres 2>/dev/null || true
  local proj vol
  proj="$(_compose_project_name "$@")"
  if [ -z "$proj" ]; then
    echo "  warn: projeto compose não resolvido — volume preservado (não arrisca apagar o de outro stack)" >&2
    return 0
  fi
  # Buscar por `docker volume ls | grep vega-vectors | head -1` apagava o volume de QUALQUER
  # projeto: derrubava o Postgres de um backend vivo e toda consulta virava psycopg AdminShutdown.
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
  set -a
  # shellcheck disable=SC1091
  [ -f "$ROOT/.env" ] && . "$ROOT/.env"
  set +a
  case "${1:-host}" in
    host) fresh_sqlite_host ;;
    compose) shift; fresh_sqlite_compose "$@"; fresh_rag_postgres "$@" ;;
    *) echo "usage: fresh-state.sh host | compose [docker compose args…]" >&2; exit 2 ;;
  esac
fi
