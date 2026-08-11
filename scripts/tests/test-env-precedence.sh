#!/usr/bin/env bash
# Env precedence test (F-REAL-ENV-2): the OS environment wins over .env, and
# .env.runtime materializes the merge. Runs on laptop and VM: ./scripts/tests/test-env-precedence.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Minimal fake repo: .env + .env.example (with a commented-out key) + the real lib.
ROOT="$TMP"
mkdir -p "$ROOT/scripts/lib"
cp "$REPO_ROOT/scripts/lib/env-load.sh" "$ROOT/scripts/lib/env-load.sh"
cat > "$ROOT/.env" <<'EOF'
DEPLOYMENT_ENVIRONMENT=user-file
GALILEO_LOG_STREAM=stream-file
CONTROL_PASSWORD='file password with a space and $dollarsign'
EOF
cat > "$ROOT/.env.example" <<'EOF'
DEPLOYMENT_ENVIRONMENT=user-01
GALILEO_LOG_STREAM=default
CONTROL_PASSWORD=
# OWNER_PASSWORD=
EOF

fail=0
check() { # name expected obtained
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: expected [$2], got [$3]" >&2
    fail=1
  fi
}

run_case() { # clean baseline (the real VM already has overrides in /etc/environment via PAM) + case overrides
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

# Case 1 — nothing in the OS: the .env value applies.
out="$(run_case)"
check "no override: DEPLOYMENT comes from .env" "user-file" "$(sed -n 1p <<<"$out")"
check "no override: space/\$ in password intact" 'file password with a space and $dollarsign' "$(sed -n 3p <<<"$out")"
grep -q '^DEPLOYMENT_ENVIRONMENT=user-file$' "$ROOT/.env.runtime" && echo "ok   runtime mirrors .env" || { echo "FAIL runtime missing .env value" >&2; fail=1; }
grep -q '^OWNER_PASSWORD=' "$ROOT/.env.runtime" && { echo "FAIL OWNER_PASSWORD should not exist in the runtime" >&2; fail=1; } || echo "ok   missing key stays missing (OWNER_PASSWORD)"

# Case 2 — OS injects diverging values (simulates an Ansible replica): OS wins.
out="$(run_case DEPLOYMENT_ENVIRONMENT="user-os" GALILEO_LOG_STREAM='os value with a space and "quotes"')"
check "OS override: DEPLOYMENT comes from the OS" "user-os" "$(sed -n 1p <<<"$out")"
check "OS override: space/quotes value intact" 'os value with a space and "quotes"' "$(sed -n 2p <<<"$out")"
check "no override on the key: still from .env" 'file password with a space and $dollarsign' "$(sed -n 3p <<<"$out")"
grep -q '^DEPLOYMENT_ENVIRONMENT=user-os$' "$ROOT/.env.runtime" && echo "ok   runtime loads the OS override" || { echo "FAIL runtime missing the OS override" >&2; fail=1; }

# Case 3 — key only in the OS, present in .env.example as commented-out: it enters the runtime.
run_case OWNER_PASSWORD="os-secret" >/dev/null
grep -q '^OWNER_PASSWORD=os-secret$' "$ROOT/.env.runtime" && echo "ok   OS-only key (commented in example) enters the runtime" || { echo "FAIL OS OWNER_PASSWORD didn't enter the runtime" >&2; fail=1; }

# Case 4 — INSTANCE (unique replica name) becomes DEPLOYMENT_ENVIRONMENT when the OS doesn't provide it.
out="$(run_case INSTANCE="vm-workshop-42")"
check "INSTANCE maps to DEPLOYMENT_ENVIRONMENT (wins over .env)" "vm-workshop-42" "$(sed -n 1p <<<"$out")"
grep -q '^DEPLOYMENT_ENVIRONMENT=vm-workshop-42$' "$ROOT/.env.runtime" && echo "ok   runtime loads the mapped INSTANCE" || { echo "FAIL runtime missing the mapped INSTANCE" >&2; fail=1; }
grep -q '^INSTANCE=' "$ROOT/.env.runtime" && { echo "FAIL raw INSTANCE should not enter the runtime" >&2; fail=1; } || echo "ok   raw INSTANCE stays out of the runtime"

# Case 5 — INSTANCE wins even over a DEPLOYMENT_ENVIRONMENT present in the environment (the units
# load .env via EnvironmentFile, so that value may just be the baked placeholder).
out="$(run_case INSTANCE="vm-workshop-42" DEPLOYMENT_ENVIRONMENT="user-placeholder")"
check "INSTANCE wins over DEPLOYMENT_ENVIRONMENT from the environment" "vm-workshop-42" "$(sed -n 1p <<<"$out")"

# Case 6 — runtime permissions (secrets): 600.
perm="$(stat -c %a "$ROOT/.env.runtime" 2>/dev/null || stat -f %Lp "$ROOT/.env.runtime")"
check "runtime has chmod 600" "600" "$perm"

if [ "$fail" -ne 0 ]; then
  echo "test-env-precedence: FAILED" >&2
  exit 1
fi
echo "test-env-precedence: OK"
