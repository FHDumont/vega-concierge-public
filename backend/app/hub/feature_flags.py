"""Surface/menu feature flags — F-033.

The owner toggles menu areas (what PARTICIPANTS see during workshop): enable Behind the Scenes
only at teaching time, hide Admin/Simulator, toggle Inspector.

Served by the **same config source** as the LLM cascade (F-020/F-026): in `local` mode
flags from this store's SQLite apply; in `remote` mode flags **served by the hub** apply (which
propagates to 150 VMs), with resilient cache. Precedence (hub wins in `remote`) and
calculation of **effective** flags happens in `effective_flags()` (front's visibility boundary consumes
this). Owner never self-blocks: gate is front-only and owner can override (ADR-021).

Persistence: single-row table (`feature_flags`) in the same SQLite (ADR-006), default all ON
(nothing hidden until owner decides — standalone-first). No secret here → goes raw to front.
"""
import sqlite3

from ..store.db import connect

_ROW_ID = 1  # single-row table (flags singleton)

# Controllable surfaces/menus. Default ON (nothing hidden until owner disables).
FLAG_KEYS = ["behind_the_scenes", "admin", "simulator", "inspector"]
DEFAULTS = {k: True for k in FLAG_KEYS}


def init_db() -> None:
    """create_all on boot: flags table (1 row) + idempotent default row (all ON)."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS feature_flags (
                id                INTEGER PRIMARY KEY,
                behind_the_scenes INTEGER NOT NULL DEFAULT 1,
                admin             INTEGER NOT NULL DEFAULT 1,
                simulator         INTEGER NOT NULL DEFAULT 1,
                inspector         INTEGER NOT NULL DEFAULT 1
            )"""
        )
        conn.execute(
            "INSERT OR IGNORE INTO feature_flags (id, behind_the_scenes, admin, simulator, inspector) "
            "VALUES (?, 1, 1, 1, 1)",
            (_ROW_ID,),
        )


def get_local_flags() -> dict:
    """Flags persisted in this store. Tolerant of missing table → defaults (all ON)."""
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM feature_flags WHERE id = ?", (_ROW_ID,)).fetchone()
    except sqlite3.OperationalError:
        return dict(DEFAULTS)
    if row is None:
        return dict(DEFAULTS)
    return {k: bool(row[k]) for k in FLAG_KEYS}


def update_flags(**partial) -> dict:
    """Edits local flags (owner). Ignores unknown keys. Returns new local flags."""
    sets, vals = [], []
    for k in FLAG_KEYS:
        if k in partial and partial[k] is not None:
            sets.append(f"{k} = ?")
            vals.append(1 if partial[k] else 0)
    if sets:
        vals.append(_ROW_ID)
        with connect() as conn:
            conn.execute(f"UPDATE feature_flags SET {', '.join(sets)} WHERE id = ?", vals)
    return get_local_flags()


def effective_flags() -> dict:
    """EFFECTIVE flags consumed by front — resolved by ACTIVE source (ADR-021).

    - `local`: flags from this store's SQLite apply.
    - `remote`: flags **served by hub** win (resilient cache from `RemoteConfigSource`);
      before first pull (no hub opinion) → defaults all ON (standalone-first, nothing hidden).
    Missing keys default to ON.
    """
    from . import config_source  # lazy: config_source.LocalConfigSource depends on this module
    src = config_source.get_active_source()
    if getattr(src, "name", "local") == "remote":
        try:
            base = src.get_flags() or {}
        except Exception:
            base = {}
    else:
        base = get_local_flags()
    return {k: bool(base.get(k, True)) for k in FLAG_KEYS}
