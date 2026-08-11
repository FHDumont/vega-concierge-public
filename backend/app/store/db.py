"""SQLite access — single file and single connection (ADR-006).

Orders, users, LLM providers, per-agent config, feature flags, hub settings and RUM
all live in the SAME file. Before, each module loaded its own copy of
`_connect()` and imported `DB_PATH` from `orders`, which made `orders` seem like the owner.
Here there is no owner: whoever needs the database imports `db`.

Each domain keeps its own `init_db()` — this module only opens a connection, knows nothing about schema.
"""
from __future__ import annotations

import sqlite3
from ..settings import settings

# `ORDERS_DB` keeps the historical name (contract with compose/EC2 — `ORDERS_DB=/data/vega.db`).
DB_PATH = settings.orders_db


def connect() -> sqlite3.Connection:
    """Connection with `row_factory=Row` — module reads assume column-name access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
