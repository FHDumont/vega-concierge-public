"""Order domain + SQLite persistence (ADR-006).

Choice: `sqlite3` from stdlib (no new dependency — 2vCPU/4GB budget, lean AMI).
Ephemeral local file per VM; `init_db()` does `create_all` at boot. In compose
path goes to a named volume via `ORDERS_DB` (DT-006).
Items and customer stored as JSON (catalog is small; no normalization).

Lifecycle (F-005, ADR-008): status PAID→SHIPPED→DELIVERED is COMPUTED by elapsed time
from PAID (deterministic, no background thread) and MATERIALIZED lazily in history on each read.
`history` records each transition with timestamp."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

from .db import connect
from ..settings import settings

# Lifecycle offsets (seconds from PAID). Short to fit in workshop; via env.
SHIP_AFTER_S = settings.order_ship_after_s
DELIVER_AFTER_S = settings.order_deliver_after_s


def init_db() -> None:
    """create_all at boot: creates orders table if not present.
    Light migration: adds `history` (F-005) and `user_id` (F-008) to old databases."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id          TEXT PRIMARY KEY,
                items       TEXT NOT NULL,   -- JSON: [{sku, name, qty, price}]
                customer    TEXT NOT NULL,   -- JSON: {name, email, address}
                total       REAL NOT NULL,
                status      TEXT NOT NULL,   -- PENDING | PAID | SHIPPED | DELIVERED | FAILED
                created_at  TEXT NOT NULL,   -- ISO-8601 UTC
                history     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{status, at}] (transitions)
                user_id     TEXT             -- order owner (F-008); NULL = guest order
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
    """Timestamp (UTC) when order entered `status`, from history."""
    for h in order["history"]:
        if h["status"] == status:
            return datetime.fromisoformat(h["at"])
    return None


def _advance_lifecycle(order: dict) -> dict:
    """Computes PAID→SHIPPED→DELIVERED by elapsed time from PAID and materializes
    transitions in history (deterministic: timestamps derive from paid_at+offset).
    Persists only when there's advance. FAILED/PENDING don't advance."""
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
    with connect() as conn:
        conn.execute("UPDATE orders SET status = ?, history = ? WHERE id = ?",
                     (status, json.dumps(history), order["id"]))
    order = dict(order)
    order["status"] = status
    order["history"] = history
    return order


def create_order(items: list[dict], customer: dict, total: float, status: str,
                 user_id: str | None = None, created_at: str | None = None) -> dict:
    """Creates and persists order with unique id. Total is recomputed by caller.
    `user_id` links order to logged-in user (F-008); None = guest order.
    `created_at` allows backdating order (test user seed, F-010);
    None uses current instant."""
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
    with connect() as conn:
        conn.execute(
            "INSERT INTO orders (id, items, customer, total, status, created_at, history, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order["id"], json.dumps(order["items"]), json.dumps(order["customer"]),
             order["total"], order["status"], order["created_at"], json.dumps(order["history"]), user_id),
        )
    return order


def transition(order_id: str, status: str, *, failure_reason: str | None = None) -> dict | None:
    """Advances status and records transition with timestamp in history."""
    order = get_order(order_id, advance=False)
    if order is None:
        return None
    history = order["history"] + [{"status": status, "at": _now_iso()}]
    with connect() as conn:
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
    with connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        return None
    order = _row_to_order(row)
    return _advance_lifecycle(order) if advance else order


def list_orders() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return [_advance_lifecycle(_row_to_order(r)) for r in rows]


def list_orders_for_user(user_id: str) -> list[dict]:
    """User's orders (purchase history, F-008), newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [_advance_lifecycle(_row_to_order(r)) for r in rows]


def order_owner(order_id: str) -> str | None:
    """user_id owning order (F-019, history detail authorization);
    None = guest order or non-existent. `user_id` is internal (doesn't go in Order shape)."""
    with connect() as conn:
        row = conn.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,)).fetchone()
    return row["user_id"] if row else None


# Statuses that count as effective spending (order paid onward). PENDING/FAILED don't count.
_SPENT_STATUSES = ("PAID", "SHIPPED", "DELIVERED")


def spend_for_user(user_id: str) -> float:
    """User's cumulative spend (sum of paid orders) — tier base (F-008)."""
    placeholders = ",".join("?" * len(_SPENT_STATUSES))
    with connect() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(total), 0) AS spent FROM orders "
            f"WHERE user_id = ? AND status IN ({placeholders})",
            (user_id, *_SPENT_STATUSES),
        ).fetchone()
    return float(row["spent"])


# --- aggregation for Admin (F-014) -------------------------------------------
# BUSINESS layer (not Store): owner sees all orders. Reuses list_orders()
# (already materializes lifecycle on read — ADR-008), so counters
# reflect already-advanced statuses (SHIPPED/DELIVERED).
_ALL_STATUSES = ("PENDING", "PAID", "SHIPPED", "DELIVERED", "FAILED", "REFUNDED")


def sales_summary() -> dict:
    """Sales summary: total orders, revenue, avg ticket, and status counts.

    Refunds (F-030): a REFUNDED order (Returns/Refund chain F-029) already left
    `_SPENT_STATUSES`, so `revenue` (= net) does NOT count it. For clarity we expose
    three faces (spec decision: show gross, refunds, and net):
      - `gross_revenue` = sales that were once effective (paid+ AND refunded);
      - `refunded_amount` = sum of REFUNDED orders (refund = full total, F-029);
      - `net_revenue` = gross − refunds = `revenue` (same measure as spend/tier).
    `avg_ticket` = net revenue / number of paid orders."""
    all_orders = list_orders()
    paid = [o for o in all_orders if o["status"] in _SPENT_STATUSES]
    refunded = [o for o in all_orders if o["status"] == "REFUNDED"]
    revenue = round(sum(o["total"] for o in paid), 2)          # net
    refunded_amount = round(sum(o["total"] for o in refunded), 2)
    gross_revenue = round(revenue + refunded_amount, 2)
    avg_ticket = round(revenue / len(paid), 2) if paid else 0.0
    by_status = {s: 0 for s in _ALL_STATUSES}
    for o in all_orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
    return {
        "orders": len(all_orders),
        "paid_orders": len(paid),
        "revenue": revenue,            # = net_revenue (kept for compat)
        "net_revenue": revenue,
        "gross_revenue": gross_revenue,
        "refunded_amount": refunded_amount,
        "returned_orders": len(refunded),
        "avg_ticket": avg_ticket,
        "by_status": by_status,
    }


def clear_all() -> int:
    """Deletes ALL orders (reset sales between batches — F-014). Returns how many
    were deleted. Destructive: confirmation is in UI. Doesn't re-seed DEMO user
    (seed runs only at boot) — their history zeroes until next seed/purchase."""
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        conn.execute("DELETE FROM orders")
    return int(n)
