"""Config source persistence — local vs remote (hub) — F-026, ADR-019.

A store can be **independent** (`source=local`: uses its own SQLite providers,
F-020) or a **hub client** (`source=remote`: pulls config from another store via URL + enrollment token).
The owner chooses via the Config screen; the choice persists in the same SQLite
(ADR-006), in a single-row table (`hub_settings`, fixed id).

This layer only stores the **choice** (mode/url/token/interval). The actual pull and
cache of config is done by `config_source.RemoteConfigSource`; serving as a hub is done by `hub.py`.

The `enrollment_token` is secret (authenticates the pull from the hub): never exposed raw to the front
(API returns only `has_token`). Same rule as LLM keys (DT-012 / DT-013).
"""
import sqlite3

from ..store.db import connect

_ROW_ID = 1  # single-row table only (settings singleton)

# Defaults. `serve_token` (hub side) starts empty = does not serve until owner sets a token.
_DEFAULTS = {
    "source": "local",          # local | remote
    "hub_url": "",              # hub URL (client side), e.g.: http://host:8000/api/hub/config
    "enrollment_token": "",     # token to pull from hub (client side) — SECRET
    "pull_interval_s": 45,      # periodic refresh interval for pull (s)
    "serve_token": "",          # token required to serve as hub (hub side) — SECRET; '' = does not serve
}


def init_db() -> None:
    """create_all on boot: source settings table (1 row) + idempotent default row."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS hub_settings (
                id               INTEGER PRIMARY KEY,
                source           TEXT NOT NULL DEFAULT 'local',  -- local | remote
                hub_url          TEXT NOT NULL DEFAULT '',
                enrollment_token TEXT NOT NULL DEFAULT '',       -- SECRET (pull) — never to front
                pull_interval_s  INTEGER NOT NULL DEFAULT 45,
                serve_token      TEXT NOT NULL DEFAULT ''        -- SECRET (serve) — never to front
            )"""
        )
        conn.execute(
            "INSERT OR IGNORE INTO hub_settings "
            "(id, source, hub_url, enrollment_token, pull_interval_s, serve_token) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_ROW_ID, _DEFAULTS["source"], _DEFAULTS["hub_url"],
             _DEFAULTS["enrollment_token"], _DEFAULTS["pull_interval_s"], _DEFAULTS["serve_token"]),
        )


def get_settings() -> dict:
    """Settings WITH secrets (internal use: pull, serve). Tolerant of missing table → defaults."""
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM hub_settings WHERE id = ?", (_ROW_ID,)).fetchone()
    except sqlite3.OperationalError:
        return dict(_DEFAULTS)
    if row is None:
        return dict(_DEFAULTS)
    return {
        "source": row["source"],
        "hub_url": row["hub_url"],
        "enrollment_token": row["enrollment_token"],
        "pull_interval_s": row["pull_interval_s"],
        "serve_token": row["serve_token"],
    }


def update_settings(*, source=None, hub_url=None, enrollment_token=None,
                    pull_interval_s=None, serve_token=None) -> dict:
    """Partial update. Tokens are **write-only**: only change when received non-empty
    (empty/None preserves current → front never re-sends the secret). Returns new settings."""
    sets, vals = [], []
    if source is not None:
        sets.append("source = ?"); vals.append("remote" if source == "remote" else "local")
    if hub_url is not None:
        sets.append("hub_url = ?"); vals.append(hub_url.strip())
    if pull_interval_s is not None:
        sets.append("pull_interval_s = ?"); vals.append(max(5, int(pull_interval_s)))
    if enrollment_token:  # write-only
        sets.append("enrollment_token = ?"); vals.append(enrollment_token.strip())
    if serve_token is not None:
        # serve_token accepts explicit empty string (owner disables serving) — not write-only.
        sets.append("serve_token = ?"); vals.append(serve_token.strip())
    if sets:
        vals.append(_ROW_ID)
        with connect() as conn:
            conn.execute(f"UPDATE hub_settings SET {', '.join(sets)} WHERE id = ?", vals)
    return get_settings()
