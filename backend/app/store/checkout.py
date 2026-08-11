"""Order-close logic (checkout), extracted from endpoint for reuse (F-017).

`place_order` is the UNIQUE close path. In F-025 (ADR-018) it became **orchestrated
by a Fulfillment Coordinator agent**: validates cart → `check_inventory`/`get_price`
(tools) → **`fraud`** (agent, decision) → `payment` (gateway) → `place_order` (persistence) →
notification. The `POST /api/orders` endpoint and traffic simulator call the SAME function, so
synthetic order exercises exactly the same orchestration as real.

Problem map "wrong decision × correct data" (tied to ProblemPanel toggles):
- `fraud_false_positive` → fraud agent BLOCKS with valid card/data → FAILED;
- `inventory_outage` → `check_inventory` (tool) raises 503 with real stock OK → FAILED;
- `payment_outage`/`payment_latency` → gateway fails/slow with valid card (F-016);
- `price_hallucination` → quote outside correct catalog (doesn't block sale)."""
from . import orders
from ..ai_agents.fulfillment_workflow import run_fulfillment_workflow
from ..problems import FLAGS
from ..runnable_config import build_runnable_config, make_thread_id, resolve_config


def _fulfillment_config(base, *, user_id: str | None, order_id: str):
    """Fulfillment graph config — merges order_id; 1 callback = 1 trace in Splunk Agent Observability."""
    meta = dict(base.get("metadata") or {})
    meta["order_id"] = order_id
    if user_id is not None:
        meta["user_id"] = user_id
    thread_id = (base.get("configurable") or {}).get("thread_id") or make_thread_id(user_id=user_id)
    if base.get("callbacks"):
        return {**base, "metadata": meta, "configurable": {"thread_id": thread_id}}
    return build_runnable_config(
        thread_id=thread_id,
        feature="fulfillment",
        metadata=meta,
    )


def place_order(
    items: list[dict],
    customer: dict,
    user_id: str | None = None,
    *,
    config=None,
) -> dict:
    """Creates and closes an order. Recomputes total; **Fulfillment Coordinator** orchestrates
    decision (stock-service/price/fraud) and close only happens if decision allows, there's
    real stock AND payment gateway approves (F-016); otherwise FAILED. Links to session user
    if present (F-008)."""
    total = sum(i["qty"] * i["price"] for i in items)
    order = orders.create_order(items, customer, total, status="PENDING", user_id=user_id)
    base = resolve_config(config, feature="fulfillment", user_id=user_id)
    fulfillment_config = _fulfillment_config(base, user_id=user_id, order_id=order["id"])
    try:
        result = run_fulfillment_workflow(
            items, total, order=order, config=fulfillment_config,
        )
        if result.get("order"):
            return result["order"]
        reason = result.get("failure_reason") or (
            "inventory_unavailable" if FLAGS.inventory_outage else "unknown"
        )
        return orders.transition(order["id"], "FAILED", failure_reason=reason)
    except Exception:
        return orders.transition(
            order["id"], "FAILED",
            failure_reason="inventory_unavailable" if FLAGS.inventory_outage else "unknown",
        )


async def aplace_order(
    items: list[dict],
    customer: dict,
    user_id: str | None = None,
    *,
    config=None,
) -> dict:
    """Async variant of place_order — uses arun_fulfillment_graph (F-OBS-PREP-5)."""
    total = sum(i["qty"] * i["price"] for i in items)
    order = orders.create_order(items, customer, total, status="PENDING", user_id=user_id)
    base = resolve_config(config, feature="fulfillment", user_id=user_id)
    fulfillment_config = _fulfillment_config(base, user_id=user_id, order_id=order["id"])
    try:
        result = run_fulfillment_workflow(
            items, total, order=order, config=fulfillment_config,
        )
        if result.get("order"):
            return result["order"]
        reason = result.get("failure_reason") or (
            "inventory_unavailable" if FLAGS.inventory_outage else "unknown"
        )
        return orders.transition(order["id"], "FAILED", failure_reason=reason)
    except Exception:
        return orders.transition(
            order["id"], "FAILED",
            failure_reason="inventory_unavailable" if FLAGS.inventory_outage else "unknown",
        )
