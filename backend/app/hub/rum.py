"""Splunk RUM (Browser Agent) config — F-040-RUM.

Owner pastes **raw RUM snippet** (from Splunk manual) and toggles on; frontend
injects snippet into `<head>` (server-render in `layout.tsx`) for ALL browser sessions —
real visitors and headless simulator Browser mode sessions (F-039). **Off by default**
(standalone-first, ADR-003): nothing injected until owner enables.

Persistence: single-row table (`rum_config`) in the same SQLite (ADR-006), mirroring
`feature_flags.py` pattern. `snippet` is NOT secret in the usual sense: RUM access token is **client-side
by nature** (ends up in HTML of every visitor), so public read (`GET /api/rum`) is ok.
Still, EDIT is owner-only (raw snippet = arbitrary JS to all clients — see DT).
"""
import sqlite3

from ..store.db import connect

_ROW_ID = 1  # single-row table (config singleton)


def init_db() -> None:
    """create_all on boot: RUM config table (1 row) + default row (off, empty)."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS rum_config (
                id      INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                snippet TEXT    NOT NULL DEFAULT ''
            )"""
        )
        conn.execute(
            "INSERT OR IGNORE INTO rum_config (id, enabled, snippet) VALUES (?, 0, '')",
            (_ROW_ID,),
        )


def get_config() -> dict:
    """Persisted config ({enabled, snippet}). Tolerant of missing table → default (off, empty)."""
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM rum_config WHERE id = ?", (_ROW_ID,)).fetchone()
    except sqlite3.OperationalError:
        return {"enabled": False, "snippet": ""}
    if row is None:
        return {"enabled": False, "snippet": ""}
    return {"enabled": bool(row["enabled"]), "snippet": row["snippet"] or ""}


def update_config(enabled: bool | None = None, snippet: str | None = None) -> dict:
    """Edits config (owner). None fields are kept. Returns new config."""
    sets, vals = [], []
    if enabled is not None:
        sets.append("enabled = ?")
        vals.append(1 if enabled else 0)
    if snippet is not None:
        sets.append("snippet = ?")
        vals.append(snippet)
    if sets:
        vals.append(_ROW_ID)
        with connect() as conn:
            conn.execute(f"UPDATE rum_config SET {', '.join(sets)} WHERE id = ?", vals)
    return get_config()


def public_config() -> dict:
    """What front consumes (server-render in `layout.tsx`): returns `snippet` only when
    `enabled` (disabled → nothing to inject)."""
    cfg = get_config()
    return {"enabled": cfg["enabled"], "snippet": cfg["snippet"] if cfg["enabled"] else ""}
