#!/usr/bin/env bash
# Carga do .env com a precedência do contrato (docs/reference/workshop-env-contract.md):
# ambiente do SO VENCE o .env, que vence os defaults. É o que permite ao processo de réplica
# (Ansible) injetar valores por clone no SO sem tocar no arquivo (F-REAL-ENV-2).
#
# Uso: o script chamador define ROOT (raiz do repo) e faz:
#   . "$ROOT/scripts/lib/env-load.sh"
#   load_env_os_first
#
# Requer bash (usa ${!var} e export -p).

# Snapshot do ambiente exportado → source do .env → snapshot re-aplicado por cima.
# `export -p` emite `declare -x VAR="…"`; dentro de função isso criaria variável LOCAL,
# então trocamos por `export` (mesma sintaxe de atribuição) antes do eval.
load_env_os_first() {
  local _snap
  _snap="$(export -p | sed 's/^declare -x/export/')"
  set -a
  # shellcheck disable=SC1091
  [ -f "$ROOT/.env" ] && . "$ROOT/.env"
  set +a
  eval "$_snap"
}

# Materializa o ambiente EFETIVO (merged SO>.env) em $ROOT/.env.runtime, consumido pelo
# compose.plain.yml via env_file — é assim que o override do SO chega DENTRO do container.
# Só escreve chaves que existem no .env ou no .env.example (inclusive as comentadas lá,
# ex. OWNER_PASSWORD) E estão setadas no ambiente — preserva a semântica vazio-vs-ausente.
write_env_runtime() {
  local out="$ROOT/.env.runtime"
  local keys key
  keys="$(
    { [ -f "$ROOT/.env" ] && grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT/.env"
      [ -f "$ROOT/.env.example" ] && grep -oE '^#? ?[A-Za-z_][A-Za-z0-9_]*=' "$ROOT/.env.example"
    } | tr -d '# ' | sed 's/=$//' | sort -u
  )"
  : > "$out"
  chmod 600 "$out"
  {
    echo "# Gerado por scripts/up.sh a cada start — NÃO editar; NÃO commitar."
    echo "# Ambiente efetivo (SO > .env) entregue ao container via env_file (compose.plain.yml)."
    for key in $keys; do
      if [ -n "${!key+x}" ]; then
        printf '%s=%s\n' "$key" "${!key}"
      fi
    done
  } >> "$out"
}
