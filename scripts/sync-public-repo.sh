#!/usr/bin/env bash
# Sync allowlisted paths from vega-concierge (private) → vega-concierge-public.
# Run from the private repo root. Default is dry-run; pass --apply to copy.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ALLOWLIST="$ROOT/scripts/public-sync.allowlist"
EXCLUDE="$ROOT/scripts/public-sync.exclude"
README_PUBLIC="$ROOT/README.public.md"
LICENSE_PUBLIC="$ROOT/LICENSE.public"

VEGA_PUBLIC_REPO="${VEGA_PUBLIC_REPO:-$ROOT/../vega-concierge-public}"
VEGA_PUBLIC_REMOTE="${VEGA_PUBLIC_REMOTE:-git@github.com:FHDumont/vega-concierge-public.git}"

DO_INIT=0
DO_APPLY=0
DO_COMMIT=0
DO_PUSH=0

usage() {
  cat <<'EOF'
Usage: ./scripts/sync-public-repo.sh [OPTIONS]

Sync essential project files to the public mirror (vega-concierge-public).

Options:
  --init     Clone VEGA_PUBLIC_REPO if missing (requires empty/nonexistent path)
  --apply    Copy files (default without this flag: dry-run only)
  --commit   Git commit in the destination after a successful sync
  --push     Push destination main to origin (implies --commit if --apply)
  -h, --help Show this help

Environment:
  VEGA_PUBLIC_REPO    Destination path (default: ../vega-concierge-public)
  VEGA_PUBLIC_REMOTE  Git remote for --init (default: git@github.com:FHDumont/vega-concierge-public.git)

Examples:
  ./scripts/sync-public-repo.sh
  ./scripts/sync-public-repo.sh --apply
  ./scripts/sync-public-repo.sh --init --apply --commit --push
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init) DO_INIT=1 ;;
    --apply) DO_APPLY=1 ;;
    --commit) DO_COMMIT=1 ;;
    --push) DO_PUSH=1; DO_COMMIT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [[ ! -f "$ROOT/scripts/dev.sh" || ! -d "$ROOT/backend" ]]; then
  echo "error: run from vega-concierge (private) repo root" >&2
  exit 1
fi

for f in "$ALLOWLIST" "$EXCLUDE" "$README_PUBLIC" "$LICENSE_PUBLIC"; do
  if [[ ! -f "$f" ]]; then
    echo "error: missing required file: $f" >&2
    exit 1
  fi
done

if [[ $DO_INIT -eq 1 && ! -d "$VEGA_PUBLIC_REPO/.git" ]]; then
  if [[ -e "$VEGA_PUBLIC_REPO" && -n "$(ls -A "$VEGA_PUBLIC_REPO" 2>/dev/null || true)" ]]; then
    echo "error: $VEGA_PUBLIC_REPO exists but is not a git repo — move it or set VEGA_PUBLIC_REPO" >&2
    exit 1
  fi
  echo "→ cloning $VEGA_PUBLIC_REMOTE → $VEGA_PUBLIC_REPO"
  git clone "$VEGA_PUBLIC_REMOTE" "$VEGA_PUBLIC_REPO"
fi

if [[ ! -d "$VEGA_PUBLIC_REPO" ]]; then
  echo "error: destination missing: $VEGA_PUBLIC_REPO (use --init or create the directory)" >&2
  exit 1
fi

DEST="$(cd "$VEGA_PUBLIC_REPO" && pwd)"
RSYNC_FLAGS=(-a --exclude-from="$EXCLUDE")
if [[ $DO_APPLY -eq 1 ]]; then
  RSYNC_FLAGS+=(--delete)
else
  RSYNC_FLAGS+=(-n --delete)
  echo "→ dry-run (pass --apply to copy)"
fi

echo "→ source: $ROOT"
echo "→ dest:   $DEST"

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"
  line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -z "$line" ]] && continue

  src="$ROOT/$line"
  if [[ ! -e "$src" ]]; then
    echo "error: allowlist path missing in private repo: $line" >&2
    exit 1
  fi

  if [[ "$line" == */ ]]; then
    dest_dir="$DEST/${line%/}"
    mkdir -p "$dest_dir"
    echo "→ rsync $line"
    rsync "${RSYNC_FLAGS[@]}" "$src" "$dest_dir/"
  else
    dest_parent="$(dirname "$DEST/$line")"
    mkdir -p "$dest_parent"
    echo "→ rsync $line"
    rsync "${RSYNC_FLAGS[@]}" "$src" "$DEST/$line"
  fi
done < "$ALLOWLIST"

if [[ $DO_APPLY -eq 1 ]]; then
  cp "$README_PUBLIC" "$DEST/README.md"
  cp "$LICENSE_PUBLIC" "$DEST/LICENSE"
  echo "→ wrote README.md and LICENSE"
else
  echo "→ would write README.md and LICENSE"
fi

guard_fail=0
FORBIDDEN=(
  AGENTS.md
  CLAUDE.md
  CONVENCOES.md
  PERFIL-LLM.md
  docs/SETUP.md
  docs/ROADMAP.md
  docs/CHANGELOG.md
  docs/DECISOES.md
  docs/DEBITO-TECNICO.md
  .cursor
  templates
  ansible
)
for path in "${FORBIDDEN[@]}"; do
  if [[ -e "$DEST/$path" ]]; then
    echo "error: forbidden file in public mirror: $path" >&2
    guard_fail=1
  fi
done
if [[ $guard_fail -ne 0 ]]; then
  exit 1
fi
echo "→ guard OK (no forbidden paths)"

SMOKE=(
  scripts/boot-workshop.sh
  compose.plain.yml
  control/systemd/install.sh
  workshop/hugo.toml
  .github/workflows/build-images.yml
)
for path in "${SMOKE[@]}"; do
  if [[ $DO_APPLY -eq 1 && ! -f "$DEST/$path" ]]; then
    echo "error: smoke check failed — missing $path" >&2
    exit 1
  fi
  if [[ $DO_APPLY -eq 0 ]]; then
    [[ -f "$ROOT/$path" ]] || { echo "error: smoke source missing: $path" >&2; exit 1; }
  fi
done
echo "→ smoke OK"

if [[ $DO_APPLY -eq 0 ]]; then
  echo "→ done (dry-run). Re-run with --apply to sync."
  exit 0
fi

if [[ $DO_COMMIT -eq 1 ]]; then
  if [[ ! -d "$DEST/.git" ]]; then
    echo "error: --commit requires a git repo at $DEST" >&2
    exit 1
  fi
  PRIVATE_SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
  (
    cd "$DEST"
    git add -A
    if git diff --cached --quiet; then
      echo "→ no changes to commit in public mirror"
    else
      git commit -m "chore: sync from vega-concierge @ ${PRIVATE_SHA}"
    fi
  )
fi

if [[ $DO_PUSH -eq 1 ]]; then
  (
    cd "$DEST"
    git push origin main
  )
  echo "→ pushed to origin main"
fi

echo "→ sync complete"
