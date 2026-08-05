"""Lógica de fechamento de pedido (checkout), extraída do endpoint p/ reúso (F-017).

`place_order` é o caminho ÚNICO do fechamento. Na F-025 (ADR-018) passou a ser **orquestrado
por um agente Fulfillment Coordinator**: valida o carrinho → `check_inventory`/`get_price`
(tools) → **`fraude`** (agente, decisão) → `payment` (gateway) → `place_order` (persistência) →
notificação. O endpoint `POST /api/orders` e o simulador de tráfego chamam a MESMA função, então
o pedido sintético exercita exatamente a mesma orquestração do real.

Mapa de problemas "decisão errada × dado correto" (ligado aos toggles do ProblemPanel):
- `fraud_false_positive` → o agente de fraude BLOQUEIA com cartão/dado válido → FAILED;
- `inventory_outage` → `check_inventory` (tool) levanta 503 com estoque real OK → FAILED;
- `payment_outage`/`payment_latency` → gateway falha/lento com cartão válido (F-016);
- `price_hallucination` → quote fora do catálogo correto (não bloqueia a venda)."""
from . import orders
from .graphs.fulfillment import arun_fulfillment_graph, run_fulfillment_graph
from .problems import FLAGS
from .runnable_config import build_runnable_config, make_thread_id, resolve_config


def _fulfillment_config(base, *, user_id: str | None, order_id: str):
    """Config do grafo fulfillment — mergeia order_id; 1 callback = 1 trace no Splunk Agent Observability."""
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
    """Cria e fecha um pedido. Recomputa o total; o **Fulfillment Coordinator** orquestra a
    decisão (estoque-serviço/preço/fraude) e o fechamento só ocorre se a decisão permite, há
    estoque real E o gateway de pagamento aprova (F-016); senão FAILED. Liga ao usuário da sessão
    se houver (F-008)."""
    total = sum(i["qty"] * i["price"] for i in items)
    order = orders.create_order(items, customer, total, status="PENDING", user_id=user_id)
    base = resolve_config(config, feature="fulfillment", user_id=user_id)
    fulfillment_config = _fulfillment_config(base, user_id=user_id, order_id=order["id"])
    try:
        result = run_fulfillment_graph(
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
    """Async variant of place_order — usa arun_fulfillment_graph (F-OBS-PREP-5)."""
    total = sum(i["qty"] * i["price"] for i in items)
    order = orders.create_order(items, customer, total, status="PENDING", user_id=user_id)
    base = resolve_config(config, feature="fulfillment", user_id=user_id)
    fulfillment_config = _fulfillment_config(base, user_id=user_id, order_id=order["id"])
    try:
        result = await arun_fulfillment_graph(
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
