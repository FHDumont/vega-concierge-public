"""Feature flags de SUPERFÍCIE/menu — F-033.

O owner liga/desliga áreas do menu (o que os PARTICIPANTES veem durante o workshop): liberar o
Behind the Scenes só na hora de ensinar, esconder o Admin/Simulator, ligar/desligar o Inspector.

Servido pela **mesma fonte de config** da cascata de LLM (F-020/F-026): em modo `local` valem as
flags do próprio SQLite desta loja; em modo `remote` valem as flags **servidas pelo hub** (que
propaga p/ as 150 VMs), com cache resiliente. A precedência (hub vence em `remote`) e o cálculo
das flags **efetivas** ficam em `effective_flags()` (a fronteira de visibilidade no front consome
isso). Owner nunca se autobloqueia: o gate é só no front e o owner passa por cima (ADR-021).

Persistência: tabela de 1 linha (`feature_flags`) no mesmo SQLite (ADR-006), default tudo ON
(nada escondido até o owner decidir — standalone-first). Sem segredo aqui → vai cru ao front.
"""
import sqlite3

from .db import connect

_ROW_ID = 1  # tabela de 1 linha (singleton de flags)

# Superfícies/menus controláveis. Default ON (nada escondido até o owner desligar).
FLAG_KEYS = ["behind_the_scenes", "admin", "simulator", "inspector"]
DEFAULTS = {k: True for k in FLAG_KEYS}


def init_db() -> None:
    """create_all no boot: tabela de flags (1 linha) + linha default idempotente (tudo ON)."""
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
    """Flags persistidas nesta loja. Tolerante a tabela ausente → defaults (tudo ON)."""
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM feature_flags WHERE id = ?", (_ROW_ID,)).fetchone()
    except sqlite3.OperationalError:
        return dict(DEFAULTS)
    if row is None:
        return dict(DEFAULTS)
    return {k: bool(row[k]) for k in FLAG_KEYS}


def update_flags(**partial) -> dict:
    """Edita as flags locais (owner). Ignora chaves desconhecidas. Devolve as flags locais novas."""
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
    """Flags EFETIVAS que o front consome — resolvidas pela FONTE ativa (ADR-021).

    - `local`: valem as flags do próprio SQLite.
    - `remote`: vencem as flags **servidas pelo hub** (cache resiliente do `RemoteConfigSource`);
      antes do 1º pull (sem opinião do hub) → defaults tudo ON (standalone-first, nada escondido).
    Chaves ausentes caem no default ON.
    """
    from . import config_source  # lazy: config_source.LocalConfigSource depende deste módulo
    src = config_source.get_active_source()
    if getattr(src, "name", "local") == "remote":
        try:
            base = src.get_flags() or {}
        except Exception:
            base = {}
    else:
        base = get_local_flags()
    return {k: bool(base.get(k, True)) for k in FLAG_KEYS}
