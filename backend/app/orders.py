"""Domínio de Order + persistência em SQLite (ADR-006).

Escolha: `sqlite3` da stdlib (sem nova dependência — orçamento 2vCPU/4GB, AMI enxuta).
Arquivo local efêmero por VM; `init_db()` faz o `create_all` no boot. Em compose o
path vai p/ um volume nomeado via `ORDERS_DB` (DT-006).
itens e customer são guardados como JSON (catálogo é pequeno; sem normalizar).

Ciclo de vida (F-005, ADR-008): status PAID→SHIPPED→DELIVERED é COMPUTADO por tempo
decorrido desde o PAID (determinístico, sem thread de background) e MATERIALIZADO de
forma lazy no histórico a cada leitura. `history` guarda cada transição com timestamp."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

# Arquivo do BD ao lado do pacote (backend/vega.db). Override por env (testes/compose).
DB_PATH = os.getenv("ORDERS_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "vega.db"))

# Offsets do ciclo de vida (segundos desde o PAID). Curtos p/ caber no workshop; via env.
SHIP_AFTER_S = int(os.getenv("ORDER_SHIP_AFTER_S", "30"))
DELIVER_AFTER_S = int(os.getenv("ORDER_DELIVER_AFTER_S", "90"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """create_all no boot: cria a tabela de pedidos se não existir.
    Migração leve: adiciona `history` (F-005) e `user_id` (F-008) em BDs antigos."""
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id          TEXT PRIMARY KEY,
                items       TEXT NOT NULL,   -- JSON: [{sku, name, qty, price}]
                customer    TEXT NOT NULL,   -- JSON: {name, email, address}
                total       REAL NOT NULL,
                status      TEXT NOT NULL,   -- PENDING | PAID | SHIPPED | DELIVERED | FAILED
                created_at  TEXT NOT NULL,   -- ISO-8601 UTC
                history     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{status, at}] (transições)
                user_id     TEXT             -- dono do pedido (F-008); NULL = pedido de convidado
            )"""
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "history" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN history TEXT NOT NULL DEFAULT '[]'")
        if "user_id" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN user_id TEXT")
        if "failure_reason" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN failure_reason TEXT")


def _new_id() -> str:
    return "ORD-" + uuid.uuid4().hex[:6].upper()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_order(row: sqlite3.Row) -> dict:
    order = {
        "id": row["id"],
        "items": json.loads(row["items"]),
        "customer": json.loads(row["customer"]),
        "total": row["total"],
        "status": row["status"],
        "created_at": row["created_at"],
        "history": json.loads(row["history"] or "[]"),
    }
    keys = row.keys()
    if "failure_reason" in keys and row["failure_reason"]:
        order["failure_reason"] = row["failure_reason"]
    return order


def _ts_of(order: dict, status: str) -> datetime | None:
    """Timestamp (UTC) em que o pedido entrou em `status`, do histórico."""
    for h in order["history"]:
        if h["status"] == status:
            return datetime.fromisoformat(h["at"])
    return None


def _advance_lifecycle(order: dict) -> dict:
    """Computa PAID→SHIPPED→DELIVERED por tempo decorrido desde o PAID e materializa
    as transições no histórico (determinístico: timestamps derivam de paid_at+offset).
    Persiste só quando há avanço. FAILED/PENDING não avançam."""
    if order["status"] not in ("PAID", "SHIPPED"):
        return order
    paid_at = _ts_of(order, "PAID")
    if paid_at is None:
        return order
    elapsed = (datetime.now(timezone.utc) - paid_at).total_seconds()
    done = {h["status"] for h in order["history"]}
    new_transitions = []
    if elapsed >= SHIP_AFTER_S and "SHIPPED" not in done:
        new_transitions.append(("SHIPPED", paid_at + timedelta(seconds=SHIP_AFTER_S)))
    if elapsed >= DELIVER_AFTER_S and "DELIVERED" not in done:
        new_transitions.append(("DELIVERED", paid_at + timedelta(seconds=DELIVER_AFTER_S)))
    if not new_transitions:
        return order
    history = order["history"] + [{"status": s, "at": at.isoformat()} for s, at in new_transitions]
    status = new_transitions[-1][0]
    with _connect() as conn:
        conn.execute("UPDATE orders SET status = ?, history = ? WHERE id = ?",
                     (status, json.dumps(history), order["id"]))
    order = dict(order)
    order["status"] = status
    order["history"] = history
    return order


def create_order(items: list[dict], customer: dict, total: float, status: str,
                 user_id: str | None = None, created_at: str | None = None) -> dict:
    """Cria e persiste um pedido com id único. Total é recomputado pelo chamador.
    `user_id` liga o pedido ao usuário logado (F-008); None = pedido de convidado.
    `created_at` permite datar o pedido no passado (seed do usuário de teste, F-010);
    None usa o instante atual."""
    now = created_at or _now_iso()
    order = {
        "id": _new_id(),
        "items": items,
        "customer": customer,
        "total": round(total, 2),
        "status": status,
        "created_at": now,
        "history": [{"status": status, "at": now}],
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO orders (id, items, customer, total, status, created_at, history, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order["id"], json.dumps(order["items"]), json.dumps(order["customer"]),
             order["total"], order["status"], order["created_at"], json.dumps(order["history"]), user_id),
        )
    return order


def transition(order_id: str, status: str, *, failure_reason: str | None = None) -> dict | None:
    """Avança o status e registra a transição com timestamp no histórico."""
    order = get_order(order_id, advance=False)
    if order is None:
        return None
    history = order["history"] + [{"status": status, "at": _now_iso()}]
    with _connect() as conn:
        if failure_reason:
            conn.execute(
                "UPDATE orders SET status = ?, history = ?, failure_reason = ? WHERE id = ?",
                (status, json.dumps(history), failure_reason, order_id),
            )
        else:
            conn.execute(
                "UPDATE orders SET status = ?, history = ?, failure_reason = NULL WHERE id = ?",
                (status, json.dumps(history), order_id),
            )
    return get_order(order_id, advance=False)


def get_order(order_id: str, advance: bool = True) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    order = _row_to_order(row)
    return _advance_lifecycle(order) if advance else order


def list_orders() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return [_advance_lifecycle(_row_to_order(r)) for r in rows]


def list_orders_for_user(user_id: str) -> list[dict]:
    """Pedidos de um usuário (histórico de compras, F-008), mais recentes primeiro."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [_advance_lifecycle(_row_to_order(r)) for r in rows]


def order_owner(order_id: str) -> str | None:
    """user_id dono do pedido (F-019, autorização do detalhe no histórico);
    None = pedido de convidado ou inexistente. `user_id` é interno (não vai no shape Order)."""
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,)).fetchone()
    return row["user_id"] if row else None


# Status que contam como gasto efetivo (pedido pago em diante). PENDING/FAILED não contam.
_SPENT_STATUSES = ("PAID", "SHIPPED", "DELIVERED")


def spend_for_user(user_id: str) -> float:
    """Gasto acumulado do usuário (soma dos pedidos pagos) — base do tier (F-008)."""
    placeholders = ",".join("?" * len(_SPENT_STATUSES))
    with _connect() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(total), 0) AS spent FROM orders "
            f"WHERE user_id = ? AND status IN ({placeholders})",
            (user_id, *_SPENT_STATUSES),
        ).fetchone()
    return float(row["spent"])


# --- agregação p/ o Admin (F-014) -------------------------------------------
# Camada de NEGÓCIO (não a Loja): o dono vê todos os pedidos. Reusa list_orders()
# (já materializa o ciclo de vida na leitura — ADR-008), então os contadores
# refletem os status já avançados (SHIPPED/DELIVERED).
_ALL_STATUSES = ("PENDING", "PAID", "SHIPPED", "DELIVERED", "FAILED", "REFUNDED")


def sales_summary() -> dict:
    """Resumo de vendas: total de pedidos, faturamento, ticket médio e contagem por status.

    Reembolsos (F-030): um pedido REFUNDED (cadeia Returns/Refund F-029) já saiu de
    `_SPENT_STATUSES`, então `revenue` (= líquido) NÃO o conta. Para clareza expomos
    as três faces (decisão da spec: mostrar bruto, reembolsos e líquido):
      - `gross_revenue` = vendas que um dia foram efetivas (pagas+ E reembolsadas);
      - `refunded_amount` = soma dos pedidos REFUNDED (reembolso = total integral, F-029);
      - `net_revenue` = bruto − reembolsos = `revenue` (mesma régua do gasto/tier).
    `avg_ticket` = revenue líquido / nº de pedidos pagos."""
    all_orders = list_orders()
    paid = [o for o in all_orders if o["status"] in _SPENT_STATUSES]
    refunded = [o for o in all_orders if o["status"] == "REFUNDED"]
    revenue = round(sum(o["total"] for o in paid), 2)          # líquido
    refunded_amount = round(sum(o["total"] for o in refunded), 2)
    gross_revenue = round(revenue + refunded_amount, 2)
    avg_ticket = round(revenue / len(paid), 2) if paid else 0.0
    by_status = {s: 0 for s in _ALL_STATUSES}
    for o in all_orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
    return {
        "orders": len(all_orders),
        "paid_orders": len(paid),
        "revenue": revenue,            # = net_revenue (mantido p/ compat)
        "net_revenue": revenue,
        "gross_revenue": gross_revenue,
        "refunded_amount": refunded_amount,
        "returned_orders": len(refunded),
        "avg_ticket": avg_ticket,
        "by_status": by_status,
    }


def clear_all() -> int:
    """Apaga TODOS os pedidos (resetar vendas entre turmas — F-014). Retorna quantos
    foram apagados. Destrutivo: a confirmação fica na UI. Não re-semeia o usuário de
    DEMO (o seed roda só no boot) — o histórico dele zera até um novo seed/compra."""
    with _connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        conn.execute("DELETE FROM orders")
    return int(n)
