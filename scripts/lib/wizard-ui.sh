#!/usr/bin/env bash
# Shared UI helpers for the Vega first-run setup wizard.
# Sourced by setup-wizard.sh — no side effects on load.

# Associative env accumulator (bash 3.x compatible via parallel arrays).
WIZ_ENV_KEYS=()
WIZ_ENV_VALS=()

wiz_blank() {
  echo
}

wiz_section() {
  local title="$1"
  local body="$2"
  local dashes
  dashes="$(printf '%*s' "${#title}" '' | tr ' ' '-')"
  wiz_blank
  echo "$title"
  echo "$dashes"
  if [ -n "$body" ]; then
    echo "$body"
    wiz_blank
  fi
}

wiz_prompt() {
  # wiz_prompt VAR "question" "default"
  local var_name="$1"
  local question="$2"
  local default="$3"
  local input=""
  if [ -n "$default" ]; then
    printf '%s [%s]: ' "$question" "$default"
  else
    printf '%s: ' "$question"
  fi
  read -r input
  if [ -z "$input" ]; then
    input="$default"
  fi
  printf -v "$var_name" '%s' "$input"
}

wiz_secret() {
  # wiz_secret VAR "question"
  local var_name="$1"
  local question="$2"
  local input=""
  printf '%s (hidden, Enter to skip): ' "$question"
  read -rs input
  echo
  printf -v "$var_name" '%s' "$input"
}

wiz_yesno() {
  # wiz_yesno VAR "question" default_y|n
  local var_name="$1"
  local question="$2"
  local default="$3"
  local hint="y/N"
  [ "$default" = "y" ] && hint="Y/n"
  local input=""
  printf '%s [%s]: ' "$question" "$hint"
  read -r input
  input="${input:-$default}"
  case "$input" in
    y|Y|yes|Yes) printf -v "$var_name" '%s' "y" ;;
    *) printf -v "$var_name" '%s' "n" ;;
  esac
}

wiz_env_index() {
  local key="$1"
  local i
  for i in "${!WIZ_ENV_KEYS[@]}"; do
    if [ "${WIZ_ENV_KEYS[$i]}" = "$key" ]; then
      echo "$i"
      return 0
    fi
  done
  return 1
}

wiz_write_env() {
  local key="$1"
  local value="$2"
  local idx
  if idx="$(wiz_env_index "$key")"; then
    WIZ_ENV_VALS[$idx]="$value"
  else
    WIZ_ENV_KEYS+=("$key")
    WIZ_ENV_VALS+=("$value")
  fi
}

wiz_read_env_file() {
  local key="$1"
  local file="$2"
  [ -f "$file" ] || return 1
  local line val
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
      "$key"=*)
        val="${line#*=}"
        val="${val%\"}"
        val="${val#\"}"
        printf '%s' "$val"
        return 0
        ;;
    esac
  done < "$file"
  return 1
}

wiz_load_env() {
  local file="$1"
  [ -f "$file" ] || return 0
  local key val line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
      *=*)
        key="${line%%=*}"
        val="${line#*=}"
        val="${val%\"}"
        val="${val#\"}"
        wiz_write_env "$key" "$val"
        ;;
    esac
  done < "$file"
}

wiz_flush_env() {
  local file="$1"
  local example="$2"
  local tmp
  tmp="$(mktemp "${file}.XXXXXX")"

  # Header from .env.example (comments only + blank lines at top).
  if [ -f "$example" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in
        \#*|'') echo "$line" >> "$tmp" ;;
        *) break ;;
      esac
    done < "$example"
    echo >> "$tmp"
  fi

  local i key val
  for i in "${!WIZ_ENV_KEYS[@]}"; do
    key="${WIZ_ENV_KEYS[$i]}"
    val="${WIZ_ENV_VALS[$i]}"
    # Quote values that contain spaces or special chars.
    if [[ "$val" =~ [[:space:]\#\$\"\'\\] ]]; then
      printf '%s="%s"\n' "$key" "$val" >> "$tmp"
    else
      printf '%s=%s\n' "$key" "$val" >> "$tmp"
    fi
  done

  mv "$tmp" "$file"
}

wiz_command_exists() {
  command -v "$1" >/dev/null 2>&1
}

wiz_docker_network_exists() {
  docker network inspect "$1" >/dev/null 2>&1
}

wiz_ghcr_logged_in() {
  # docker stores creds in ~/.docker/config.json; grep is a pragmatic check.
  if [ -f "${HOME}/.docker/config.json" ] && grep -q '"ghcr.io"' "${HOME}/.docker/config.json" 2>/dev/null; then
    return 0
  fi
  return 1
}

wiz_sanitize_hostname() {
  local raw="$1"
  raw="$(echo "$raw" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9' '-')"
  raw="${raw#-}"
  raw="${raw%-}"
  [ -n "$raw" ] || raw="LOCAL"
  printf '%s' "$raw"
}

wiz_is_complete() {
  local mode="$1"
  local env_file="$2"

  [ -f "$env_file" ] || return 1

  local dep
  dep="$(wiz_read_env_file DEPLOYMENT_ENVIRONMENT "$env_file" || true)"
  [ -n "$dep" ] || return 1

  case "$mode" in
    dev)
      return 0
      ;;
    compose)
      wiz_command_exists docker || return 1
      docker compose version >/dev/null 2>&1 || return 1
      return 0
      ;;
    prod)
      # Legacy: prod wizard removed (F-DEPLOY-PROD-1) — treat as complete if .env exists.
      [ -f "$env_file" ] || return 1
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}
