#!/usr/bin/env bash
# Teste da precedência de env (F-REAL-ENV-2): ambiente do SO vence o .env, e o
# .env.runtime materializa o merge. Roda no laptop e na VM: ./scripts/tests/test-env-precedence.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Repo fake mínimo: .env + .env.example (com chave comentada) + a lib real.
ROOT="$TMP"
mkdir -p "$ROOT/scripts/lib"
cp "$REPO_ROOT/scripts/lib/env-load.sh" "$ROOT/scripts/lib/env-load.sh"
cat > "$ROOT/.env" <<'EOF'
DEPLOYMENT_ENVIRONMENT=user-arquivo
GALILEO_LOG_STREAM=stream-arquivo
CONTROL_PASSWORD='senha do arquivo com espaço e $cifrao'
EOF
cat > "$ROOT/.env.example" <<'EOF'
DEPLOYMENT_ENVIRONMENT=user-01
GALILEO_LOG_STREAM=default
CONTROL_PASSWORD=
# OWNER_PASSWORD=
EOF

fail=0
check() { # nome esperado obtido
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: esperado [$2], obtido [$3]" >&2
    fail=1
  fi
}

run_case() { # baseline limpo (a VM real já tem overrides em /etc/environment via PAM) + overrides do caso
  env -u DEPLOYMENT_ENVIRONMENT -u GALILEO_LOG_STREAM -u CONTROL_PASSWORD -u OWNER_PASSWORD -u INSTANCE "$@" bash -c '
    set -euo pipefail
    ROOT="'"$ROOT"'"
    . "$ROOT/scripts/lib/env-load.sh"
    load_env_os_first
    write_env_runtime
    printf "%s\n" "$DEPLOYMENT_ENVIRONMENT"
    printf "%s\n" "$GALILEO_LOG_STREAM"
    printf "%s\n" "$CONTROL_PASSWORD"
  '
}

# Caso 1 — sem nada no SO: vale o .env.
out="$(run_case)"
check "sem override: DEPLOYMENT vem do .env" "user-arquivo" "$(sed -n 1p <<<"$out")"
check "sem override: senha com espaço/\$ intacta" 'senha do arquivo com espaço e $cifrao' "$(sed -n 3p <<<"$out")"
grep -q '^DEPLOYMENT_ENVIRONMENT=user-arquivo$' "$ROOT/.env.runtime" && echo "ok   runtime espelha .env" || { echo "FAIL runtime sem valor do .env" >&2; fail=1; }
grep -q '^OWNER_PASSWORD=' "$ROOT/.env.runtime" && { echo "FAIL OWNER_PASSWORD não deveria existir no runtime" >&2; fail=1; } || echo "ok   chave ausente segue ausente (OWNER_PASSWORD)"

# Caso 2 — SO injeta valores divergentes (simula réplica Ansible): SO vence.
out="$(run_case DEPLOYMENT_ENVIRONMENT="user-so" GALILEO_LOG_STREAM='so com espaço e "aspas"')"
check "override SO: DEPLOYMENT vem do SO" "user-so" "$(sed -n 1p <<<"$out")"
check "override SO: valor com espaço/aspas intacto" 'so com espaço e "aspas"' "$(sed -n 2p <<<"$out")"
check "sem override na chave: continua do .env" 'senha do arquivo com espaço e $cifrao' "$(sed -n 3p <<<"$out")"
grep -q '^DEPLOYMENT_ENVIRONMENT=user-so$' "$ROOT/.env.runtime" && echo "ok   runtime carrega o override do SO" || { echo "FAIL runtime sem override do SO" >&2; fail=1; }

# Caso 3 — chave só no SO, presente no .env.example como comentada: entra no runtime.
run_case OWNER_PASSWORD="segredo-so" >/dev/null
grep -q '^OWNER_PASSWORD=segredo-so$' "$ROOT/.env.runtime" && echo "ok   chave só-SO (comentada no example) entra no runtime" || { echo "FAIL OWNER_PASSWORD do SO não entrou no runtime" >&2; fail=1; }

# Caso 4 — INSTANCE (nome único da réplica) vira DEPLOYMENT_ENVIRONMENT quando o SO não o traz.
out="$(run_case INSTANCE="vm-workshop-42")"
check "INSTANCE mapeia p/ DEPLOYMENT_ENVIRONMENT (vence o .env)" "vm-workshop-42" "$(sed -n 1p <<<"$out")"
grep -q '^DEPLOYMENT_ENVIRONMENT=vm-workshop-42$' "$ROOT/.env.runtime" && echo "ok   runtime carrega o INSTANCE mapeado" || { echo "FAIL runtime sem INSTANCE mapeado" >&2; fail=1; }
grep -q '^INSTANCE=' "$ROOT/.env.runtime" && { echo "FAIL INSTANCE cru não deveria entrar no runtime" >&2; fail=1; } || echo "ok   INSTANCE cru fica fora do runtime"

# Caso 5 — DEPLOYMENT_ENVIRONMENT explícito no SO vence INSTANCE.
out="$(run_case INSTANCE="vm-workshop-42" DEPLOYMENT_ENVIRONMENT="user-explicito")"
check "DEPLOYMENT_ENVIRONMENT do SO vence INSTANCE" "user-explicito" "$(sed -n 1p <<<"$out")"

# Caso 6 — permissões do runtime (segredos): 600.
perm="$(stat -c %a "$ROOT/.env.runtime" 2>/dev/null || stat -f %Lp "$ROOT/.env.runtime")"
check "runtime com chmod 600" "600" "$perm"

if [ "$fail" -ne 0 ]; then
  echo "test-env-precedence: FALHOU" >&2
  exit 1
fi
echo "test-env-precedence: OK"
