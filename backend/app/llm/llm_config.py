"""LLM config persistence (F-020, stage 2 — ADR-015).

Cascade providers `{name, kind, base_url, model, enabled, order, api_key}` in SQLite
(same file as orders/users — ADR-006). **API key is secret**: stored in plain (we need it to call
provider — can't hash it; acceptable on ephemeral per-participant VM, is DT), but **never**
returned to frontend or logged. API only sees **masked** version (`has_key` + hint of last digits).

This is the **local** implementation of config source. Stage 4 (ConfigSource) and F-021 (remote)
plug behind `llm._load_provider_configs` without consumers changing.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from ..store.db import DB_PATH, connect
from ..settings import settings

_KINDS = ("openai", "anthropic", "bedrock")  # supported kinds (see llm._ADAPTERS)
_PERSIST_FILENAME = "llm_providers.json"


def init_db() -> None:
    """create_all on boot: LLM providers table if doesn't exist."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_providers (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'openai',  -- openai | anthropic
                base_url    TEXT NOT NULL DEFAULT '',
                model       TEXT NOT NULL,
                api_key     TEXT NOT NULL DEFAULT '',        -- SECRET: never exposed to front
                enabled     INTEGER NOT NULL DEFAULT 1,
                ord         INTEGER NOT NULL DEFAULT 0,       -- cascade order (smaller = before)
                created_at  TEXT NOT NULL
            )"""
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "LP-" + uuid.uuid4().hex[:6].upper()


# --- mask (what goes to frontend) ------------------------------------------

def _key_hint(api_key: str) -> str | None:
    """Non-reversible key hint: only last 4 digits. None if no key."""
    if not api_key:
        return None
    return "••••" + api_key[-4:] if len(api_key) >= 4 else "••••"


def _mask(row: sqlite3.Row) -> dict:
    """Public representation (WITHOUT key): front never receives `api_key`."""
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model": row["model"],
        "enabled": bool(row["enabled"]),
        "order": row["ord"],
        "has_key": bool(row["api_key"]),
        "key_hint": _key_hint(row["api_key"]),
    }


# --- read for cascade (internal; includes key) ----------------------------

def list_enabled_with_keys() -> list[dict]:
    """ENABLED providers in order, WITH key — consumed by `llm.get_llm`.
    Tolerant to missing table (run_demo/standalone without init_db) → empty list (stub only)."""
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_providers WHERE enabled = 1 ORDER BY ord, created_at"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "base_url": r["base_url"],
             "model": r["model"], "api_key": r["api_key"]} for r in rows]


def get_provider_with_key(provider_id: str) -> dict | None:
    """Provider WITH key (internal use — e.g., Test endpoint). Never goes to front raw."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM llm_providers WHERE id = ?", (provider_id,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "base_url": row["base_url"],
            "model": row["model"], "api_key": row["api_key"]}


# --- CRUD (API only returns masked version) --------------------------------

def list_providers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM llm_providers ORDER BY ord, created_at").fetchall()
    return [_mask(r) for r in rows]


def _get_masked(provider_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM llm_providers WHERE id = ?", (provider_id,)).fetchone()
    return _mask(row) if row else None


def create_provider(name: str, kind: str, base_url: str, model: str,
                    api_key: str = "", enabled: bool = True, order: int | None = None) -> dict:
    kind = kind if kind in _KINDS else "openai"
    pid = _new_id()
    if order is None:  # append at end of cascade
        with connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(ord), -1) + 1 AS nxt FROM llm_providers").fetchone()
            order = row["nxt"]
    with connect() as conn:
        conn.execute(
            "INSERT INTO llm_providers (id, name, kind, base_url, model, api_key, enabled, ord, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, name.strip(), kind, base_url.strip(), model.strip(), api_key,
             1 if enabled else 0, order, _now_iso()),
        )
    return _get_masked(pid)


def update_provider(provider_id: str, *, name=None, kind=None, base_url=None,
                    model=None, api_key=None, enabled=None, order=None) -> dict | None:
    """Partial update. `api_key` is **write-only**: only changes if non-empty
    (empty/None keeps current key → front never needs to resend secret)."""
    sets, vals = [], []
    if name is not None: sets.append("name = ?"); vals.append(name.strip())
    if kind is not None: sets.append("kind = ?"); vals.append(kind if kind in _KINDS else "openai")
    if base_url is not None: sets.append("base_url = ?"); vals.append(base_url.strip())
    if model is not None: sets.append("model = ?"); vals.append(model.strip())
    if enabled is not None: sets.append("enabled = ?"); vals.append(1 if enabled else 0)
    if order is not None: sets.append("ord = ?"); vals.append(int(order))
    if api_key:  # only replace when there's new key (write-only)
        sets.append("api_key = ?"); vals.append(api_key)
    if not sets:
        return _get_masked(provider_id)
    vals.append(provider_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE llm_providers SET {', '.join(sets)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            return None
    return _get_masked(provider_id)


def delete_provider(provider_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM llm_providers WHERE id = ?", (provider_id,))
    return cur.rowcount > 0


def reorder(ids: list[str]) -> list[dict]:
    """Reorder cascade by order of received ids (index → `ord`)."""
    with connect() as conn:
        for i, pid in enumerate(ids):
            conn.execute("UPDATE llm_providers SET ord = ? WHERE id = ?", (i, pid))
    return list_providers()


# --- persistence across fresh-states (F-REAL-ENV-1) -----------------------

def persist_dir() -> str:
    """Host/container directory for provider backup (`VEGA_PERSIST_DIR` or `.vega-persist`)."""
    root = settings.vega_persist_dir.strip()
    if not root:
        root = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), ".vega-persist")
    os.makedirs(root, exist_ok=True)
    return root


def persist_file_path() -> str:
    return os.path.join(persist_dir(), _PERSIST_FILENAME)


def _read_provider_rows() -> list[dict]:
    try:
        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM llm_providers ORDER BY ord, created_at"
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def export_providers_backup() -> int:
    """Save current providers to JSON — called by fresh-state before deleting SQLite."""
    rows = _read_provider_rows()
    path = persist_file_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return len(rows)


def restore_providers_backup() -> int:
    """Restore providers from JSON if table is empty (post fresh-state)."""
    path = persist_file_path()
    if not os.path.isfile(path):
        return 0
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM llm_providers").fetchone()["n"]
    if n:
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(rows, list) or not rows:
        return 0
    restored = 0
    with connect() as conn:
        for row in rows:
            if not isinstance(row, dict):
                continue
            pid = row.get("id") or _new_id()
            kind = row.get("kind") if row.get("kind") in _KINDS else "openai"
            conn.execute(
                "INSERT INTO llm_providers "
                "(id, name, kind, base_url, model, api_key, enabled, ord, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    str(row.get("name") or "Provider").strip(),
                    kind,
                    str(row.get("base_url") or "").strip(),
                    str(row.get("model") or "").strip(),
                    str(row.get("api_key") or ""),
                    1 if row.get("enabled", True) else 0,
                    int(row.get("ord", row.get("order", 0))),
                    str(row.get("created_at") or _now_iso()),
                ),
            )
            restored += 1
    return restored


# --- cascade bootstrap from `.env`/SO (F-BACKEND-3, Stage B) ----------------

# Aliases accepted in `LLM_PROVIDER_PRIORITY` (CLAUDE = ANTHROPIC). Each alias maps to ONE
# provider known by stable name below — it's the contract Ansible and Admin share.
_CASCADE_SPECS: dict[str, dict] = {
    "OPENAI": {
        "env_field": "openai_api_key",
        "name": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model_from_settings": "openai_chat_model",
    },
    "ANTHROPIC": {
        "env_field": "anthropic_api_key",
        "name": "Claude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "model_from_settings": "anthropic_chat_model",
    },
    "BEDROCK": {
        "env_field": "aws_bearer_token_bedrock",
        "name": "Bedrock",
        "kind": "bedrock",
        "base_url": "",
        "model_from_settings": "bedrock_chat_model",
    },
    "OLLAMA": {
        "name": "Ollama Local",
        "kind": "openai",
        "model_from_settings": "ollama_chat_model",
    },
}

_DEFAULT_PRIORITY: tuple[str, ...] = ("BEDROCK", "OPENAI", "ANTHROPIC", "OLLAMA")

# Only cloud specs — frozen for `tests/test_env_example_contract.py` (token fields).
_ENV_SEED_SPECS: tuple[dict, ...] = tuple(
    spec for alias, spec in _CASCADE_SPECS.items() if alias != "OLLAMA"
)


def _normalize_priority_alias(raw: str) -> str | None:
    alias = raw.strip().upper()
    if alias == "CLAUDE":
        alias = "ANTHROPIC"
    return alias if alias in _CASCADE_SPECS else None


def _parse_provider_priority() -> list[str]:
    raw = settings.llm_provider_priority.strip()
    if not raw:
        return list(_DEFAULT_PRIORITY)
    aliases: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        alias = _normalize_priority_alias(part)
        if alias and alias not in seen:
            aliases.append(alias)
            seen.add(alias)
    return aliases or list(_DEFAULT_PRIORITY)


def _provider_row_by_name(name: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT id, name, api_key, enabled, ord FROM llm_providers WHERE name = ?",
            (name,),
        ).fetchone()


def _provider_token(spec: dict) -> str:
    env_field = spec.get("env_field")
    if not env_field:
        return "ollama"
    return str(getattr(settings, env_field, "") or "").strip()


def _provider_base_url(spec: dict) -> str:
    if spec.get("base_url"):
        return spec["base_url"]
    if spec["kind"] == "bedrock":
        return settings.aws_default_region
    if "model_from_settings" in spec:
        return f"{settings.ollama_base_url.rstrip('/')}/v1"
    return ""


def _provider_model(spec: dict) -> str:
    field = spec.get("model_from_settings")
    if field:
        return str(getattr(settings, field, "") or "").strip()
    return str(spec.get("model") or "").strip()


def _provider_configured(alias: str, spec: dict) -> bool:
    if alias == "OLLAMA":
        return bool(settings.ollama_base_url.strip())
    return bool(_provider_token(spec))


def seed_ollama_default() -> None:
    """Compat: Ollama entra via `seed_providers_from_env()` + `LLM_PROVIDER_PRIORITY`."""
    seed_providers_from_env()


def seed_providers_from_env() -> dict[str, int]:
    """Build LLM cascade at each boot from `.env`/SO.

    `LLM_PROVIDER_PRIORITY` defines order (e.g., `BEDROCK,OPENAI,ANTHROPIC,OLLAMA`). For each
    alias, if provider is configured (cloud token present or `OLLAMA_BASE_URL` for Ollama),
    creates/updates row and applies sequential `ord` — providers without credential are
    skipped until fallback local.

    Idempotent **by name**. **Env wins** key, order, and enabled each restart; **UI wins**
    model and base_url (except Bedrock, whose region comes from `AWS_DEFAULT_REGION` on create).

    Returns counters for boot log (`_bootstrap` in `api.py`).
    """
    created = 0
    updated = 0
    ordered = 0
    ord_idx = 0

    for alias in _parse_provider_priority():
        spec = _CASCADE_SPECS[alias]
        name = spec["name"]
        configured = _provider_configured(alias, spec)
        row = _provider_row_by_name(name)

        if not configured:
            if row is not None and alias != "OLLAMA" and row["enabled"]:
                update_provider(row["id"], enabled=False)
                ordered += 1
            continue

        token = _provider_token(spec)
        base_url = _provider_base_url(spec)
        model = _provider_model(spec)

        if row is None:
            create_provider(
                name=name,
                kind=spec["kind"],
                base_url=base_url,
                model=model,
                api_key=token,
                enabled=True,
                order=ord_idx,
            )
            created += 1
            ord_idx += 1
            continue

        key_changed = token and row["api_key"] != token
        order_changed = row["ord"] != ord_idx
        needs_enable = not row["enabled"]
        if key_changed:
            update_provider(row["id"], api_key=token)
            updated += 1
        if order_changed or needs_enable:
            update_provider(row["id"], order=ord_idx, enabled=True)
            ordered += 1
        ord_idx += 1

    return {"created": created, "updated": updated, "ordered": ordered}


