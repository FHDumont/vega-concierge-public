"""Acesso ao SQLite — arquivo único e conexão única (ADR-006).

Pedidos, usuários, provedores de LLM, config por agente, feature flags, settings de hub e RUM
moram todos no MESMO arquivo. Antes cada um desses módulos carregava a própria cópia de
`_connect()` e importava `DB_PATH` de `orders`, o que fazia `orders` parecer dono do banco.
Aqui não há dono: quem precisa do banco importa `db`.

Cada domínio continua com o SEU `init_db()` — este módulo só abre conexão, não conhece schema.
"""
from __future__ import annotations

import sqlite3
from ..settings import settings

# `ORDERS_DB` mantém o nome histórico (contrato do compose/EC2 — `ORDERS_DB=/data/vega.db`).
DB_PATH = settings.orders_db


def connect() -> sqlite3.Connection:
    """Conexão com `row_factory=Row` — as leituras dos módulos assumem acesso por nome de coluna."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
