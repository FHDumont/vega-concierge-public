"""Config do Splunk RUM (Browser Agent) — F-040-RUM.

O owner cola o **snippet bruto** do RUM (do manual do Splunk) e liga o toggle; o frontend
injeta o snippet no `<head>` (server-render no `layout.tsx`) p/ TODAS as sessões de navegador —
visitantes reais e as sessões headless do simulador modo Browser (F-039). **Off por default**
(standalone-first, ADR-003): nada é injetado até o owner ligar.

Persistência: tabela de 1 linha (`rum_config`) no mesmo SQLite (ADR-006), espelhando o padrão de
`feature_flags.py`. O `snippet` NÃO é segredo no sentido usual: o RUM access token é **client-side
por natureza** (vai parar no HTML de todo visitante), então a leitura pública (`GET /api/rum`) é ok.
Mesmo assim a EDIÇÃO é owner-only (snippet bruto = JS arbitrário em todos os clientes — ver DT).
"""
import sqlite3

from .orders import DB_PATH  # mesmo arquivo SQLite (ADR-006)

_ROW_ID = 1  # tabela de 1 linha (singleton de config)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """create_all no boot: tabela de config do RUM (1 linha) + linha default (off, vazio)."""
    with _connect() as conn:
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
    """Config persistida ({enabled, snippet}). Tolerante a tabela ausente → default (off, vazio)."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM rum_config WHERE id = ?", (_ROW_ID,)).fetchone()
    except sqlite3.OperationalError:
        return {"enabled": False, "snippet": ""}
    if row is None:
        return {"enabled": False, "snippet": ""}
    return {"enabled": bool(row["enabled"]), "snippet": row["snippet"] or ""}


def update_config(enabled: bool | None = None, snippet: str | None = None) -> dict:
    """Edita a config (owner). Campos None são mantidos. Devolve a config nova."""
    sets, vals = [], []
    if enabled is not None:
        sets.append("enabled = ?")
        vals.append(1 if enabled else 0)
    if snippet is not None:
        sets.append("snippet = ?")
        vals.append(snippet)
    if sets:
        vals.append(_ROW_ID)
        with _connect() as conn:
            conn.execute(f"UPDATE rum_config SET {', '.join(sets)} WHERE id = ?", vals)
    return get_config()


def public_config() -> dict:
    """O que o front consome (server-render no `layout.tsx`): só devolve o `snippet` quando
    `enabled` (desligado → nada a injetar)."""
    cfg = get_config()
    return {"enabled": cfg["enabled"], "snippet": cfg["snippet"] if cfg["enabled"] else ""}
