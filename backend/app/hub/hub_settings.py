"""Persistência da FONTE de config — local vs remota (hub) — F-026, ADR-019.

Uma loja pode ser **independente** (`source=local`: usa os provedores do próprio SQLite,
F-020) ou **cliente de um hub** (`source=remote`: puxa a config de outra loja via URL + token
de enrollment). O owner escolhe pela tela de Config; a escolha persiste no mesmo SQLite
(ADR-006), numa tabela de 1 linha (`hub_settings`, id fixo).

Esta camada só guarda a **escolha** (mode/url/token/intervalo). Quem efetivamente puxa e
cacheia a config é `config_source.RemoteConfigSource`; quem serve como hub é `hub.py`.

O `enrollment_token` é segredo (autentica o pull do hub): nunca é exposto cru ao front
(API devolve só `has_token`). Mesma régua das chaves de LLM (DT-012 / DT-013).
"""
import sqlite3

from ..store.db import connect

_ROW_ID = 1  # tabela de 1 linha só (singleton de settings)

# Defaults. `serve_token` (lado hub) começa vazio = não serve até o owner definir um token.
_DEFAULTS = {
    "source": "local",          # local | remote
    "hub_url": "",              # URL do hub (lado cliente), ex.: http://host:8000/api/hub/config
    "enrollment_token": "",     # token p/ puxar do hub (lado cliente) — SEGREDO
    "pull_interval_s": 45,      # refresh periódico do pull (s)
    "serve_token": "",          # token exigido p/ servir como hub (lado hub) — SEGREDO; '' = não serve
}


def init_db() -> None:
    """create_all no boot: tabela de settings de fonte (1 linha) + linha default idempotente."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS hub_settings (
                id               INTEGER PRIMARY KEY,
                source           TEXT NOT NULL DEFAULT 'local',  -- local | remote
                hub_url          TEXT NOT NULL DEFAULT '',
                enrollment_token TEXT NOT NULL DEFAULT '',       -- SEGREDO (pull) — nunca ao front
                pull_interval_s  INTEGER NOT NULL DEFAULT 45,
                serve_token      TEXT NOT NULL DEFAULT ''        -- SEGREDO (serve) — nunca ao front
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
    """Settings COM segredos (uso interno: pull, serve). Tolerante a tabela ausente → defaults."""
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
    """Atualização parcial. Tokens são **write-only**: só trocam quando vêm não-vazios
    (vazio/None mantém o atual → o front nunca reenvia o segredo). Devolve os settings novos."""
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
        # serve_token aceita string vazia explícita (owner desliga o servir) — não é write-only.
        sets.append("serve_token = ?"); vals.append(serve_token.strip())
    if sets:
        vals.append(_ROW_ID)
        with connect() as conn:
            conn.execute(f"UPDATE hub_settings SET {', '.join(sets)} WHERE id = ?", vals)
    return get_settings()
